# -*- coding: utf-8 -*-
"""Phase 2.5: Quarantine Review (DLQ 재평가)

목적:
- Phase 1.5 Strict Validation(validate_outputs.py)에서 토큰 매칭 실패로 
  격리된(DLQ) 데이터들에 대해 LLM-as-a-judge 평가를 수행합니다.
- 테이블 구조 붕괴 등으로 인한 '정상적인 추론(False-Negative)'은 구제(Recover)하고,
  여러 단어를 결합해 창조한 '환각(True-Positive DLQ)'은 폐기(Discard)합니다.

입출력:
- Input: phase1_5_validation/DLQ_entities.json
- Output: phase1_5_validation/recovered_entities.json, phase1_5_validation/discarded_entities.json
"""

import asyncio
import json
import os
import re
from pathlib import Path
from tqdm.asyncio import tqdm
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
import argparse

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent
CHUNKS_FILE = BASE_DIR / "phase1_output" / "chunks.json"
DLQ_FILE = BASE_DIR / "phase1_5_validation" / "DLQ_entities.json"
RECOVERED_FILE = BASE_DIR / "phase1_5_validation" / "recovered_entities.json"
DISCARDED_FILE = BASE_DIR / "phase1_5_validation" / "discarded_entities.json"

# 환경변수 로드
load_dotenv(BASE_DIR / ".env")

# 비동기 LLM 클라이언트 (DeepSeek 사용. 비용/속도 최적화)
client = AsyncOpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

# --- Pydantic 스키마 정의 ---
class ItemEvaluation(BaseModel):
    item_id: str = Field(description="평가 대상 아이템의 고유 식별자 (예: ENT-1, REL-2)")
    is_valid: bool = Field(description="결론: 원본 텍스트/표의 논리적 문맥에서 도출 가능한 합당한 정보인가? (True/False)")
    confidence: float = Field(description="판정 확신도 (0.0 ~ 1.0)")
    reason: str = Field(description="판정 사유 (표 구조상 도출됨, 언어 환각/임의 창조됨 등 명확히 기재)")

class ChunkReviewResult(BaseModel):
    reviews: list[ItemEvaluation] = Field(description="청크 내 각 에러 아이템에 대한 평가 결과 목록")

# 프롬프트 템플릿
SYSTEM_PROMPT = """당신은 건설/엔지니어링 데이터 추출 파이프라인의 최고 품질 관리자(Chief Data Validator)입니다.
앞선 파이프라인에서 '텍스트 단순 글자 매칭'에 실패하여 격리된 데이터들의 문맥적 타당성을 재평가해야 합니다.

[판별 원칙]
1. 허용 (is_valid: true) - "문맥적 추론 (False-Negative)"
   - 원본 PDF의 표(Table)가 파싱 중 줄바꿈이나 셀 붕괴로 텍스트가 파편화되었지만, 
     표의 헤더나 구조 문맥상 해당 엔티티(장비명, 직무명, 규격 등)나 수량 배정 관계가 정당하게 추론되는 경우.
     (예: 텍스트엔 "조경기능", "공" 으로 쪼개져 있더라도 결과물이 "조경기능공" 이라면 합격)

2. 거절 (is_valid: false) - "언어 환각 및 임의 요약 (True-Positive DLQ)"
   - 청크 내 여러 문장에 흩어져 있는 단어들("국토교통부장관", "고시", "기준")을 무리하게 조합하여 
     하나의 새로운 명사(예: "국토교통부장관 고시 측량용역대가기준")를 창조한 경우, 절대적으로 거절하십시오. DB에 오염 노드를 만듭니다.
   - 원본 정보에 근거가 없는 완벽한 할루시네이션(Hallucination).

입력으로 원본 텍스트/표 데이터와, 검증에 실패한 Entity 및 Relationship 목록이 제공됩니다.
각 아이템별로 주어진 item_id를 매핑하여 리뷰 결과를 JSON으로 엄격히 반환하십시오.
"""

async def evaluate_chunk(chunk_id: str, original_text: str, items_to_review: list, sem: asyncio.Semaphore):
    """단일 청크 내의 실패 아이템들을 LLM을 통해 일괄 평가"""
    if not items_to_review:
        return []
        
    async with sem:
        # 평가 프롬프트 구성
        prompt = f"==== 원본 데이터 (Chunk ID: {chunk_id}) ====\n{original_text}\n\n"
        prompt += "==== 검증 대상 (Missing/Mismatch Items) ====\n"
        
        for item in items_to_review:
            item_id = item["item_id"]
            if item["type"] == "entity":
                data = item["data"]
                prompt += f"[{item_id}] Entity: name='{data.get('name')}', type='{data.get('type')}', spec='{data.get('spec')}', error='{data.get('dlq_reason')}'\n"
            else:
                data = item["data"]
                prompt += f"[{item_id}] Relationship: source='{data.get('source')}', target='{data.get('target')}', qty='{data.get('quantity')}', spec='{data.get('properties', {}).get('source_spec')}', error='{data.get('dlq_reason')}'\n"
        
        prompt += "\n위의 각 [...] 아이템에 대해 JSON 형태로 평가 결과를 반환해 주세요."
        
        try:
            response = await client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1, # 일관된 논리적 판단을 위해 온도를 낮춤
                max_tokens=2000
            )
            
            content = response.choices[0].message.content
            # JSON 파싱 (가끔 모델이 마크다운 ```json 래핑을 할 때 안전하게 처리)
            cleaned = re.sub(r"^```json\s*", "", content.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            
            result_dict = json.loads(cleaned)
            # keys 가 reviews가 아닐 수 있으므로 유연하게 처리
            reviews = result_dict.get("reviews", [])
            if not reviews and isinstance(result_dict, list):
                reviews = result_dict
            elif not reviews and len(result_dict.keys()) > 0:
                 # fallback
                 for k, v in result_dict.items():
                     if isinstance(v, list):
                         reviews = v
                         break
            return reviews
            
        except Exception as e:
            print(f"[ERROR] Chunk {chunk_id} 평가 실패: {str(e)}")
            return []

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=0, help="테스트용 샘플 개수 (0이면 전체)")
    args = parser.parse_args()

    if not DLQ_FILE.exists() or not CHUNKS_FILE.exists():
        print("DLQ_entities.json 또는 chunks.json 파일이 존재하지 않습니다.")
        return

    print("데이터 로딩 중...")
    dlq_data = json.loads(DLQ_FILE.read_text(encoding="utf-8"))
    chunks_data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    chunk_map = {c["chunk_id"]: c for c in chunks_data.get("chunks", [])}

    extractions = dlq_data.get("extractions", [])
    if args.sample > 0:
        extractions = extractions[:args.sample]

    print(f"총 {len(extractions)}개의 DLQ 청크 재평가 준비 완료 (동시성 10 제한).")
    
    sem = asyncio.Semaphore(10) # 429 방어
    tasks = []
    task_metadata = []

    for ext in extractions:
        chunk_id = ext["chunk_id"]
        c_info = chunk_map.get(chunk_id, {})
        
        # 원본 컨텍스트 조립
        base_text_components = [
            c_info.get("department", ""),
            c_info.get("chapter", ""),
            c_info.get("title", ""),
            c_info.get("text", "")
        ]
        original_text = " ".join([c for c in base_text_components if c])
        for t in c_info.get("tables", []):
            original_text += "\n[Table Data]: " + json.dumps(t, ensure_ascii=False)
            
        # 리뷰할 아이템 목록 생성 (고유 ID 발급)
        items_to_review = []
        for i, ent in enumerate(ext.get("entities", [])):
            items_to_review.append({"item_id": f"ENT-{i}", "type": "entity", "data": ent})
        for i, rel in enumerate(ext.get("relationships", [])):
            items_to_review.append({"item_id": f"REL-{i}", "type": "relationship", "data": rel})
            
        if items_to_review:
            task = asyncio.create_task(evaluate_chunk(chunk_id, original_text, items_to_review, sem))
            tasks.append(task)
            task_metadata.append({"chunk_id": chunk_id, "ext": ext, "items": items_to_review})

    print("비동기 LLM 평가 시작...")
    results = await tqdm.gather(*tasks)

    # 결과 분류
    recovered_chunks = []
    discarded_chunks = []
    
    total_recovered_items = 0
    total_discarded_items = 0

    for meta, review_list in zip(task_metadata, results):
        ext = meta["ext"]
        items_map = {item["item_id"]: item for item in meta["items"]}
        
        # 리뷰 결과를 바탕으로 분리
        r_entities = []
        r_relationships = []
        
        d_entities = []
        d_relationships = []
        
        for review in review_list:
            item_id = review.get("item_id")
            if not item_id or item_id not in items_map:
                continue
                
            orig_item = items_map[item_id]
            data = orig_item["data"]
            # 리뷰 결과 병합 (사유 기록)
            data["quarantine_reason"] = review.get("reason", "")
            data["quarantine_confidence"] = review.get("confidence", 0.0)
            
            is_valid = review.get("is_valid", False)
            
            if orig_item["type"] == "entity":
                if is_valid:
                    r_entities.append(data)
                    total_recovered_items += 1
                else:
                    d_entities.append(data)
                    total_discarded_items += 1
            else:
                if is_valid:
                    r_relationships.append(data)
                    total_recovered_items += 1
                else:
                    d_relationships.append(data)
                    total_discarded_items += 1

        # 청크 조립 (구제된 것)
        if r_entities or r_relationships:
            r_ext = ext.copy()
            r_ext["entities"] = r_entities
            r_ext["relationships"] = r_relationships
            recovered_chunks.append(r_ext)
            
        # 청크 조립 (폐기된 것)
        if d_entities or d_relationships:
            d_ext = ext.copy()
            d_ext["entities"] = d_entities
            d_ext["relationships"] = d_relationships
            discarded_chunks.append(d_ext)

    # 파일 저장
    RECOVERED_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    if recovered_chunks:
        RECOVERED_FILE.write_text(
            json.dumps({"extractions": recovered_chunks}, ensure_ascii=False, indent=2), 
            encoding="utf-8"
        )
    if discarded_chunks:
        DISCARDED_FILE.write_text(
            json.dumps({"extractions": discarded_chunks}, ensure_ascii=False, indent=2), 
            encoding="utf-8"
        )

    print("\n[Quarantine Review 완료]")
    print(f"평가 대상 Chunk: {len(tasks)}건")
    print(f"✅ 구제된(Recovered) 세부 속성: {total_recovered_items}건 -> {RECOVERED_FILE.name}")
    print(f"❌ 폐기된(Discarded) 세부 속성: {total_discarded_items}건 -> {DISCARDED_FILE.name}")

if __name__ == "__main__":
    asyncio.run(main())
