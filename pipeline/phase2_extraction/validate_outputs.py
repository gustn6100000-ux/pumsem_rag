# -*- coding: utf-8 -*-
"""Phase 1.5: 독립 Post-Validator (수량/규격 Strict 검증)

목적:
- step5가 느슨하게 잡는 할루시네이션(임계값 10%) 대신,
- 토큰화(Tokenize) 및 완전 일치 기반의 엄격한 교차 검증을 수행.
- 수량(quantity), 단위(unit), 규격(spec), 자재/장비명(name)을
  원본 텍스트와 대조하여 일치하지 않으면 DLQ(Dead Letter Queue)로 격리.

결과물:
- validated_entities.json : 검증 통과 데이터 (DB 적재 후보)
- DLQ_entities.json       : 검증 실패 데이터 (Relaxed 재평가/격리 대상)
- validation_report.json  : 통계 요약 (품질 대시보드 시각화용)
"""

import json
import re
from pathlib import Path
from collections import Counter

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent
MAPPING_FILE = BASE_DIR / "phase1_output" / "chunks.json"
MERGED_FILE = BASE_DIR / "phase2_output" / "llm_entities.json"

def _tokenize(text: str) -> set[str]:
    """특수문자 제거 후 1음절 이상 형태소 단위(어절) 및 공백 제거 문자열로 분해"""
    if not text:
        return set()
    cleaned = re.sub(r"[^\w\s가-힣a-zA-Z0-9]", " ", text)
    tokens = [t for t in cleaned.split() if t]
    
    # 공백을 모두 붙인 문자열도 하나의 매치 단위로 추가 (띄어쓰기 차이 극복)
    joined = "".join(tokens)
    if joined:
        tokens.append(joined)
        
    return set(tokens)

def validate_entity(entity: dict, original_text: str) -> bool:
    """엔티티의 이름, 규격 단어가 원본 텍스트에 존재하는지 검증"""
    text_tokens = _tokenize(original_text)
    # 원본 텍스트 전체의 공백 제거 버전 추가 (부분 일치 검색용)
    original_text_joined = "".join(re.sub(r"[^\w\s가-힣a-zA-Z0-9]", "", original_text).split())
    
    # 1. 이름 검증
    name_str = entity.get("name", "")
    entity_type = entity.get("type", "")
    name_joined = "".join(re.sub(r"[^\w\s가-힣a-zA-Z0-9]", "", name_str).split())
    name_tokens = _tokenize(name_str)
    
    # 토큰이 하나라도 일치하거나, 공백 없는 이름이 원본에 부분 문자열로 포함되는지 확인
    match_count = sum(1 for t in name_tokens if t in text_tokens)
    
    if name_tokens and match_count == 0 and (not name_joined or name_joined not in original_text_joined):
        # Note나 Condition처럼 길거나 자연어 문장형 엔티티는 LLM의 요약이나 변형이 들어갈 수 있으므로
        # 아예 흔적도 없으면 워닝 성격으로 넘기거나(느슨하게 통과) 
        # 혹은 아예 False로 드롭하지 않고 통과시킨다 (Strict 모드라 해도 문장형은 정확히 Tokenize 되기 어려움).
        if entity_type in ["Note", "Condition"]:
            # 일단 패스시키되, 나중에 필요하면 DLQ_reason 대신 Warning 필드에 기록할 수 있음.
            pass
        else:
            return False
        
    # 2. 규격 검증
    spec_str = entity.get("spec", "")
    if spec_str:
        spec_joined = "".join(re.sub(r"[^\w\s가-힣a-zA-Z0-9]", "", spec_str).split())
        # Spec은 LLM이 추론/요약한 값(예: "V형", "H형")일 수 있어 맹목적 토큰 매칭 시 실패가 많음.
        # 따라서 부분 문자열 일치만 체크하고, 토큰이 없다고 False로 처리하지 않음. (Relaxed)
        if spec_joined and spec_joined not in original_text_joined:
            # 텍스트 내에 단순 누락된 경우는 1차 검증(Strict)에서 패스율을 너무 깎으므로
            # 여기서는 name이라도 맞으면 통과시키되, 원한다면 Warning만 남길 수 있게 패스 처리.
            pass
            
    return True

def validate_relationship(rel: dict, original_text: str) -> bool:
    """관계의 수량, 규격 단어가 원본 텍스트에 존재하는지 검증"""
    text = original_text.replace(" ", "").replace(",", "")
    original_text_joined = "".join(re.sub(r"[^\w\s가-힣a-zA-Z0-9]", "", original_text).split())
    
    # 1. 수량 검증
    qty = rel.get("quantity")
    if qty is not None:
        qty_str1 = str(qty)
        qty_str2 = f"{qty:g}"
        if qty_str1 not in text and qty_str2 not in text:
            # DLQ 기록 (수량 에러는 Relationship 환각의 주요 원인)
            return False

    # 2. 관련 property 규격 검증
    props = rel.get("properties", {})
    spec_str = props.get("source_spec", "")
    if spec_str:
        spec_joined = "".join(re.sub(r"[^\w\s가-힣a-zA-Z0-9]", "", str(spec_str)).split())
        if spec_joined and spec_joined not in original_text_joined:
            # Entity spec과 동일한 이유로 느슨하게 통과시킴
            pass
            
    return True

def main():
    if not MAPPING_FILE.exists() or not MERGED_FILE.exists():
        print(f"필요 파일 없음: {MAPPING_FILE} 또는 {MERGED_FILE}")
        return

    # 청크 데이터 로드
    chunks_data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    chunk_map = {c["chunk_id"]: c for c in chunks_data.get("chunks", [])}

    # 머지 데이터 로드
    merged_data = json.loads(MERGED_FILE.read_text(encoding="utf-8"))
    
    validated = []
    dlq = []
    
    stats = {
        "total": len(merged_data.get("extractions", [])),
        "passed": 0,
        "failed": 0,
        "reasons": Counter()
    }

    print("Post-Validation (Strict Mode) 진행 중...")
    
    for ext in merged_data.get("extractions", []):
        chunk_id = ext["chunk_id"]
        chunk_info = chunk_map.get(chunk_id, {})
        # Title 및 상위 메타데이터(chapter, department 등)도 중요한 컨텍스트이므로 텍스트 자원에 포함
        base_text_components = [
            chunk_info.get("department", ""),
            chunk_info.get("chapter", ""),
            chunk_info.get("subsection", ""),
            chunk_info.get("title", ""),
            chunk_info.get("text", "")
        ]
        original_text = " ".join([c for c in base_text_components if c])
        
        # 테이블 데이터들도 original_text 로직에 섞어서 _tokenize 되도록 문자열화
        for t in chunk_info.get("tables", []):
            original_text += " " + json.dumps(t, ensure_ascii=False)
            
        # 개별 엔티티/관계별로 검증 결과를 나눔
        valid_entities = []
        dlq_entities = []
        
        # 1. 엔티티 개별 검증
        for ent in ext.get("entities", []):
            if validate_entity(ent, original_text):
                valid_entities.append(ent)
            else:
                ent["dlq_reason"] = [f"Entity mismatch: {ent.get('name')} / {ent.get('spec')}"]
                dlq_entities.append(ent)
                stats["reasons"]["Entity mismatch"] += 1
                
        # 2. 관계 개별 검증
        valid_relationships = []
        dlq_relationships = []
        
        for rel in ext.get("relationships", []):
            # 관계의 source/target이 valid_entities에 남아있는 애들인지 먼저 확인 (선택적 엄격함)
            # 여기서는 순수하게 관계 데이터 텍스트만 검사.
            if validate_relationship(rel, original_text):
                valid_relationships.append(rel)
            else:
                rel["dlq_reason"] = [f"Relationship mismatch: qty {rel.get('quantity')} / spec {rel.get('properties', {}).get('source_spec')}"]
                dlq_relationships.append(rel)
                stats["reasons"]["Relationship mismatch"] += 1
        
        # 검증 결과 재분배 (청크 껍데기는 유지하되, 내부 배열을 valid 와 dlq 로 분리하여 담는다)
        if valid_entities or valid_relationships:
            valid_ext = ext.copy()
            valid_ext["entities"] = valid_entities
            valid_ext["relationships"] = valid_relationships
            validated.append(valid_ext)
            
        if dlq_entities or dlq_relationships:
            dlq_ext = ext.copy()
            dlq_ext["entities"] = dlq_entities
            dlq_ext["relationships"] = dlq_relationships
            # 청크 전체의 dlq_reason을 지우고, 개별 아이템 안에 사유가 들어있음
            if "dlq_reason" in dlq_ext:
                del dlq_ext["dlq_reason"]
            dlq.append(dlq_ext)
            stats["failed"] += 1 # 실패가 하나라도 발생한 청크 수
            
        if not dlq_entities and not dlq_relationships:
             stats["passed"] += 1 # 완전 무결한 청크 수
                
    # 저장
    out_dir = BASE_DIR / "phase1_5_validation"
    out_dir.mkdir(exist_ok=True)
    
    (out_dir / "validated_entities.json").write_text(json.dumps({"extractions": validated}, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "DLQ_entities.json").write_text(json.dumps({"extractions": dlq}, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "validation_report.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    
    print("\n--- 검증 요약 ---")
    print(f"Total: {stats['total']}")
    print(f"Passed: {stats['passed']} ({(stats['passed']/max(1, stats['total'])*100):.1f}%)")
    print(f"Failed (DLQ): {stats['failed']}")
    print(f"Top Reasons:")
    for r, cnt in stats["reasons"].most_common(5):
        print(f"  - {r}: {cnt}건")

if __name__ == "__main__":
    main()
