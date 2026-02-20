# -*- coding: utf-8 -*-
"""Phase 2.8: Master LLM Entities 병합 (Merge Valid & Recovered)

목적:
- Phase 1.5 Strict Validation(validate_outputs.py)을 통과한 `validated_entities.json`과
- Phase 2.5 Quarantine Review(step2_5_quarantine_review.py)에서 구제받은 `recovered_entities.json`을 병합하여
- 오염 노드가 전혀 없는 순수한 LLM 추출 데이터인 `llm_entities_master.json`을 생성합니다.
- 이 파일은 Phase 3 (step3_relation_builder -> step4 -> step6) 파이프라인의 안전한 입력값이 됩니다.
"""

import json
from pathlib import Path

# 경로 설정
BASE_DIR = Path(__file__).resolve().parent.parent
VALIDATED_FILE = BASE_DIR / "phase1_5_validation" / "validated_entities.json"
RECOVERED_FILE = BASE_DIR / "phase1_5_validation" / "recovered_entities.json"
MASTER_FILE = BASE_DIR / "phase2_output" / "llm_entities_master.json"

def main():
    print("===== 데이터 병합 시작 =====")
    
    validated_data = []
    if VALIDATED_FILE.exists():
        v_data = json.loads(VALIDATED_FILE.read_text(encoding="utf-8"))
        validated_data = v_data.get("extractions", [])
        print(f"✅ Validated 데이터 로드 완료: {len(validated_data)} 청크")
    else:
        print("❌ Validated 데이터가 없습니다.")

    recovered_data = []
    if RECOVERED_FILE.exists():
        r_data = json.loads(RECOVERED_FILE.read_text(encoding="utf-8"))
        recovered_data = r_data.get("extractions", [])
        print(f"✅ Recovered 데이터 로드 완료: {len(recovered_data)} 청크")
    else:
        print("❌ Recovered 데이터가 없습니다.")

    # 청크 아이디별 통합 병합을 위한 Dict
    master_map = {}
    
    # 1. Validated 데이터 적재
    for ext in validated_data:
        cid = ext["chunk_id"]
        master_map[cid] = ext.copy()
        
    # 2. Recovered 데이터 병합 (같은 청크 ID에 대해 엔티티, 관계 리스트 합치기)
    for ext in recovered_data:
        cid = ext["chunk_id"]
        if cid in master_map:
            master_map[cid]["entities"].extend(ext.get("entities", []))
            master_map[cid]["relationships"].extend(ext.get("relationships", []))
        else:
            master_map[cid] = ext.copy()
            
    master_extractions = list(master_map.values())
    
    # 저장
    MASTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    MASTER_FILE.write_text(
        json.dumps({"extractions": master_extractions}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    print(f"\n🚀 Master 데이터 생성 완료: {len(master_extractions)} 청크 -> {MASTER_FILE.name}")

if __name__ == "__main__":
    main()
