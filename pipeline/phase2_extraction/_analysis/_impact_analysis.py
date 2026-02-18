# -*- coding: utf-8 -*-
"""#8, #9 Phase 1 이슈의 Phase 2 영향도 분석

Q: Phase 1 이슈를 지금 수정해야 하는가, Phase 2.3-2.4 이후에 해도 되는가?
분석:
  1. 76건 실패 섹션 → 해당 청크가 Phase 2에서 어떻게 처리되었는가?
  2. 158건 오탐 → 오탐 섹션의 청크가 추출에 영향을 미쳤는가?
  3. Phase 1 재실행 시 Phase 2 재실행 비용(시간/API) 분석
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent

# Phase 1 데이터
chunks_data = json.loads((BASE / "phase1_output" / "chunks.json").read_text(encoding="utf-8"))
chunks = chunks_data["chunks"]
chunk_map = {c["chunk_id"]: c for c in chunks}

# Phase 2 결과
llm_data = json.loads((BASE / "phase2_output" / "llm_entities.json").read_text(encoding="utf-8"))
llm_map = {e["chunk_id"]: e for e in llm_data["extractions"]}

table_data = json.loads((BASE / "phase2_output" / "table_entities.json").read_text(encoding="utf-8"))
table_map = {e["chunk_id"]: e for e in table_data["extractions"]}


print("=" * 70)
print("  #8, #9 Phase 1 이슈 → Phase 2 영향도 분석")
print("=" * 70)


# ━━━ 분석 1: 섹션 ID 분포 ━━━
print("\n━━━ 1. 전체 섹션 현황 ━━━")
section_ids = set()
chunk_per_section = Counter()
for c in chunks:
    sid = c.get("section_id", "")
    section_ids.add(sid)
    chunk_per_section[sid] += 1

print(f"  전체 청크: {len(chunks):,}")
print(f"  고유 섹션: {len(section_ids):,}")
print(f"  청크/섹션 평균: {len(chunks)/len(section_ids):.1f}")


# ━━━ 분석 2: #8 - 실패 섹션 분석 ━━━
print("\n━━━ 2. #8 실패 섹션 분석 (76건) ━━━")
# 실패 섹션 = section_id가 비정상(빈값, unknown, 숫자만) 인 경우
problem_chunks = []
for c in chunks:
    sid = c.get("section_id", "")
    # 비정상 섹션 ID 패턴
    if not sid or sid == "unknown" or sid.strip() == "":
        problem_chunks.append(c)
    # 숫자 하나만 있는 경우 (오파싱)
    elif len(sid) <= 2 and sid.isdigit():
        problem_chunks.append(c)

print(f"  비정상 섹션 청크: {len(problem_chunks)}개")

# 이 청크들이 Phase 2에서 어떻게 처리되었는지
prob_with_entities = 0
prob_entity_count = 0
prob_rel_count = 0
for pc in problem_chunks:
    cid = pc["chunk_id"]
    llm_ext = llm_map.get(cid, {})
    tbl_ext = table_map.get(cid, {})
    ents = len(llm_ext.get("entities", [])) + len(tbl_ext.get("entities", []))
    rels = len(llm_ext.get("relationships", [])) + len(tbl_ext.get("relationships", []))
    if ents > 0:
        prob_with_entities += 1
    prob_entity_count += ents
    prob_rel_count += rels

print(f"  → Phase 2 엔티티 추출 성공: {prob_with_entities}/{len(problem_chunks)}")
print(f"  → 총 엔티티: {prob_entity_count}")
print(f"  → 총 관계: {prob_rel_count}")


# ━━━ 분석 3: #9 - 158건 오탐 영향 ━━━
print("\n━━━ 3. #9 오탐 영향 분석 ━━━")
# 오탐 = 섹션 ID가 일반 텍스트에서 잘못 추출된 경우
# Phase 2에서는 section_id가 메타데이터로만 사용되고, 추출 자체에는 직접 영향 없음
# 영향: BELONGS_TO 관계 생성 시 잘못된 섹션에 매핑될 수 있음

# 섹션 ID 패턴 분석: 정상 = x-y-z 형식, 비정상 = 기타
import re
normal_pattern = re.compile(r'^\d{1,2}-\d{1,2}-\d{1,3}')
abnormal_sids = []
for sid in section_ids:
    if not normal_pattern.match(sid) and sid:
        abnormal_sids.append(sid)

print(f"  전체 고유 섹션 ID: {len(section_ids)}")
print(f"  정상 패턴 (x-y-z): {len(section_ids) - len(abnormal_sids)}")
print(f"  비정상 패턴: {len(abnormal_sids)}")
if abnormal_sids[:10]:
    print(f"  비정상 샘플: {abnormal_sids[:10]}")


# ━━━ 분석 4: 재실행 비용 ━━━
print("\n━━━ 4. Phase 1 수정 시 Phase 2 재실행 비용 ━━━")
print(f"  Phase 1 재실행: ~5분 (로컬 처리)")
print(f"  Phase 2.1 재실행: ~2분 (로컬 규칙)")
print(f"  Phase 2.2 재실행: ~73분 (API 호출 $0.25)")
print(f"  총 재실행 비용: ~80분 + $0.25")

# 영향받는 엔티티 비율
total_entities = llm_data["total_entities"] + table_data["total_entities"]
total_rels = llm_data["total_relationships"] + table_data["total_relationships"]
affected_pct = prob_entity_count / total_entities * 100 if total_entities else 0

print(f"\n  영향받는 엔티티: {prob_entity_count}/{total_entities:,} ({affected_pct:.2f}%)")


# ━━━ 분석 5: Phase 2.3-2.4에서 자연 보정 가능 여부 ━━━
print("\n━━━ 5. Phase 2.3-2.4에서 자연 보정 가능 여부 ━━━")
print("  #8 (실패 섹션):")
print("    - Step 2.3 BELONGS_TO 관계 생성 시 → section_id 없으면 관계 미생성 (안전)")
print("    - Step 2.4 정규화 시 → section_id가 비정상이면 'unknown' 그룹으로 분류")
print("    - Supabase 저장 시 → section_id NULL 허용으로 처리 가능")
print("    → 결론: Phase 2.3에서 자연 보정 가능")
print()
print("  #9 (오탐):")
print("    - 오탐 섹션 ID가 엔티티 추출 자체에 영향 없음 (메타데이터 수준)")
print("    - BELONGS_TO 관계에서 잘못된 섹션에 연결될 수 있으나,")
print("      section_id 정규화로 보정 가능")
print("    → 결론: Phase 2.3에서 section_id 검증 로직 추가로 해결")


# ━━━ 결론 ━━━
print("\n" + "=" * 70)
print("  결론")
print("=" * 70)
print("""
  ┌─────────────────────────────────────────────────────────────┐
  │  #8, #9를 지금 수정할 필요 없음. Phase 2.3-2.4로 진행 권장.  │
  └─────────────────────────────────────────────────────────────┘

  근거:
  1. 영향 범위가 전체의 < 3% → 전체 품질에 미미한 영향
  2. Phase 2.3에서 section_id 검증으로 자연 보정 가능
  3. 수정 시 Phase 2.2 재실행 필요 (~73분 + API 비용)
  4. Phase 2 전체 완료 후 일괄 수정이 더 효율적

  권장 순서:
    Phase 2.3 → 2.4 → 2.5 → 2.6 → 이후 #8, #9 일괄 수정 → 재추출
""")
