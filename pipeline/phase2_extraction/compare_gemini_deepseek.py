"""
Gemini vs DeepSeek 샘플 비교 스크립트
====================================
기존 Gemini llm_entities.json에서 20개 청크를 샘플링,
동일 청크를 DeepSeek-V3로 재추출하여 품질 비교.
"""
import json
import os
import random
import asyncio
from pathlib import Path
from openai import AsyncOpenAI
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
PHASE2_OUTPUT = PROJECT_ROOT / "phase2_output"
PHASE1_OUTPUT = PROJECT_ROOT / "phase1_output"

load_dotenv(PROJECT_ROOT / ".env")

# ─── 설정 ─────────────────────────────────────────────
SAMPLE_SIZE = 20
GEMINI_FILE = PHASE2_OUTPUT / "llm_entities.json"
CHUNKS_FILE = PHASE1_OUTPUT / "chunks.json"
OUTPUT_FILE = PHASE2_OUTPUT / "gemini_vs_deepseek_comparison.json"
REPORT_FILE = PROJECT_ROOT / "docs" / "20260213_Gemini_vs_DeepSeek_비교.md"

client = AsyncOpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

# ─── Step2와 동일한 프롬프트 ──────────────────────────
SYSTEM_PROMPT = """You are a Korean construction cost estimation data extractor.
Given a text chunk from a Korean construction cost handbook (품셈),
extract entities and relationships in JSON format.

Output JSON schema:
{
  "entities": [
    {"entity_id": "...", "name": "...", "type": "WorkType|Material|Equipment|Labor|Standard|Note", "properties": {...}}
  ],
  "relationships": [
    {"source_entity_id": "...", "target_entity_id": "...", "type": "USES_MATERIAL|REQUIRES_EQUIPMENT|REQUIRES_LABOR|APPLIES_STANDARD|HAS_NOTE", "properties": {...}}
  ]
}

Rules:
- entity_id format: {type}_{chunk_id}_{seq} (e.g. WorkType_ch001_1)
- Extract ALL entities mentioned: work types, materials, equipment, labor, standards, notes
- Include quantity/unit/spec in properties when available
- Respond ONLY with valid JSON, no markdown fences
"""


async def extract_with_deepseek(chunk_id: str, text: str) -> dict:
    """단일 청크를 DeepSeek-V3로 추출"""
    try:
        resp = await client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Chunk ID: {chunk_id}\n\n{text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=4096,
            timeout=60,
        )
        content = resp.choices[0].message.content
        return json.loads(content)
    except Exception as e:
        return {"error": str(e), "entities": [], "relationships": []}


async def main():
    # 1. Gemini 결과 로드
    print("📂 Gemini 결과 로드...")
    gemini_data = json.loads(GEMINI_FILE.read_text(encoding="utf-8"))
    gemini_extractions = {ext["chunk_id"]: ext for ext in gemini_data.get("extractions", [])}
    
    # 2. 청크 로드
    print("📂 청크 로드...")
    chunks_data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = {c["chunk_id"]: c for c in chunks_data.get("chunks", [])}
    
    # 3. 샘플링: Gemini가 추출한 청크 중 다양한 타입 포함
    available = [cid for cid in gemini_extractions if cid in chunks]
    random.seed(42)
    sample_ids = random.sample(available, min(SAMPLE_SIZE, len(available)))
    print(f"\n🎯 샘플 {len(sample_ids)}개 선택")
    
    # 4. DeepSeek 추출
    print("🤖 DeepSeek-V3 추출 시작...")
    tasks = []
    for cid in sample_ids:
        text = chunks[cid].get("text", "")
        tables = chunks[cid].get("tables", "")
        full_text = f"{text}\n\n{tables}" if tables else text
        tasks.append(extract_with_deepseek(cid, full_text))
    
    deepseek_results = await asyncio.gather(*tasks)
    print(f"  ✅ DeepSeek 추출 완료: {len(deepseek_results)}건")
    
    # 5. 비교 분석
    comparison = []
    total_gemini_entities = 0
    total_deepseek_entities = 0
    total_gemini_rels = 0
    total_deepseek_rels = 0
    
    for cid, ds_result in zip(sample_ids, deepseek_results):
        gm = gemini_extractions[cid]
        gm_entities = gm.get("entities", [])
        gm_rels = gm.get("relationships", [])
        ds_entities = ds_result.get("entities", [])
        ds_rels = ds_result.get("relationships", [])
        
        total_gemini_entities += len(gm_entities)
        total_deepseek_entities += len(ds_entities)
        total_gemini_rels += len(gm_rels)
        total_deepseek_rels += len(ds_rels)
        
        # 엔티티 타입별 비교
        gm_types = {}
        for e in gm_entities:
            t = e.get("type", "Unknown")
            gm_types[t] = gm_types.get(t, 0) + 1
        ds_types = {}
        for e in ds_entities:
            t = e.get("type", "Unknown")
            ds_types[t] = ds_types.get(t, 0) + 1
        
        comparison.append({
            "chunk_id": cid,
            "title": chunks[cid].get("title", ""),
            "gemini": {
                "entities": len(gm_entities),
                "relationships": len(gm_rels),
                "types": gm_types,
            },
            "deepseek": {
                "entities": len(ds_entities),
                "relationships": len(ds_rels),
                "types": ds_types,
                "error": ds_result.get("error"),
            },
        })
    
    # 6. JSON 저장
    result = {
        "sample_size": len(sample_ids),
        "summary": {
            "gemini_total_entities": total_gemini_entities,
            "deepseek_total_entities": total_deepseek_entities,
            "gemini_total_relationships": total_gemini_rels,
            "deepseek_total_relationships": total_deepseek_rels,
            "entity_ratio": round(total_deepseek_entities / max(total_gemini_entities, 1), 2),
            "rel_ratio": round(total_deepseek_rels / max(total_gemini_rels, 1), 2),
        },
        "comparisons": comparison,
    }
    OUTPUT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n📄 비교 JSON: {OUTPUT_FILE}")
    
    # 7. 마크다운 리포트
    md = []
    md.append("# Gemini vs DeepSeek-V3 샘플 비교 결과\n")
    md.append(f"> **샘플 수**: {len(sample_ids)}개 청크 (seed=42)\n")
    md.append(f"> **비교일**: 2026-02-13\n")
    md.append("\n---\n")
    md.append("\n## 전체 요약\n")
    md.append("| 항목 | Gemini | DeepSeek-V3 | 비율 |")
    md.append("|---|---|---|---|")
    md.append(f"| 엔티티 합계 | {total_gemini_entities} | {total_deepseek_entities} | ×{result['summary']['entity_ratio']} |")
    md.append(f"| 관계 합계 | {total_gemini_rels} | {total_deepseek_rels} | ×{result['summary']['rel_ratio']} |")
    md.append(f"| 평균 엔티티/청크 | {total_gemini_entities/len(sample_ids):.1f} | {total_deepseek_entities/len(sample_ids):.1f} | |")
    md.append(f"| 평균 관계/청크 | {total_gemini_rels/len(sample_ids):.1f} | {total_deepseek_rels/len(sample_ids):.1f} | |")
    md.append("\n---\n")
    md.append("\n## 청크별 상세 비교\n")
    md.append("| # | 청크 | 제목 | Gemini E | DS E | Gemini R | DS R |")
    md.append("|---|---|---|---|---|---|---|")
    for i, c in enumerate(comparison, 1):
        title_short = (c["title"] or "")[:25]
        err = " ⚠️" if c["deepseek"].get("error") else ""
        md.append(f"| {i} | `{c['chunk_id']}` | {title_short} | {c['gemini']['entities']} | {c['deepseek']['entities']}{err} | {c['gemini']['relationships']} | {c['deepseek']['relationships']} |")
    
    md.append("\n---\n")
    md.append("\n## 타입별 분포 비교 (합산)\n")
    
    # 타입별 합산
    all_gm_types = {}
    all_ds_types = {}
    for c in comparison:
        for t, cnt in c["gemini"]["types"].items():
            all_gm_types[t] = all_gm_types.get(t, 0) + cnt
        for t, cnt in c["deepseek"]["types"].items():
            all_ds_types[t] = all_ds_types.get(t, 0) + cnt
    
    all_types = sorted(set(list(all_gm_types.keys()) + list(all_ds_types.keys())))
    md.append("| 타입 | Gemini | DeepSeek | 차이 |")
    md.append("|---|---|---|---|")
    for t in all_types:
        gv = all_gm_types.get(t, 0)
        dv = all_ds_types.get(t, 0)
        diff = dv - gv
        sign = "+" if diff > 0 else ""
        md.append(f"| {t} | {gv} | {dv} | {sign}{diff} |")
    
    errors = [c for c in comparison if c["deepseek"].get("error")]
    if errors:
        md.append(f"\n> ⚠️ DeepSeek 에러 {len(errors)}건: 해당 청크는 비교에서 제외 권장\n")
    
    REPORT_FILE.write_text("\n".join(md), encoding="utf-8")
    print(f"📊 리포트: {REPORT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
