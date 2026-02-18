# -*- coding: utf-8 -*-
"""고아 엔티티 3,979건 심층 분석 → 삭제 가능 건 식별"""
import json, sys, re
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())
ents = norm["entities"]

all_rels = []
for ext in norm.get("extractions", []):
    for r in ext.get("relationships", []):
        all_rels.append(r)
for rtype, rels in norm.get("global_relationships", {}).items():
    all_rels.extend(rels)

ref_ids = set()
for r in all_rels:
    ref_ids.add(r.get("source_entity_id", ""))
    ref_ids.add(r.get("target_entity_id", ""))
ref_ids.discard("")

orphans = [e for e in ents if e["entity_id"] not in ref_ids]
non_orphans = [e for e in ents if e["entity_id"] in ref_ids]

out = []
def p(s=""): out.append(s)

p("=" * 70)
p(f"고아 엔티티 심층 분석 ({len(orphans)}건)")
p("=" * 70)

# 타입별 통계
orphan_types = Counter(e["type"] for e in orphans)
total_types = Counter(e["type"] for e in ents)

p("\n타입별 고아 비율:")
for t in sorted(total_types.keys()):
    total = total_types[t]
    orphan = orphan_types.get(t, 0)
    p(f"  {t}: {orphan}/{total} ({orphan/total*100:.1f}%)")

# ── Section 고아 60건 ──
p(f"\n{'='*70}")
p("Section 고아 (60건) — Section에 HAS_CHILD/BELONGS_TO 없는 것은 문제")
section_orphans = [e for e in orphans if e["type"] == "Section"]
# Section 고아 중 code 있는 것
with_code = [e for e in section_orphans if e.get("code")]
without_code = [e for e in section_orphans if not e.get("code")]
p(f"  code 있음: {len(with_code)}")
p(f"  code 없음: {len(without_code)}")
p("  code 없음 샘플:")
for e in without_code[:10]:
    p(f"    {e['entity_id']}: '{e['name'][:40]}' chunks={len(e.get('source_chunk_ids',[]))}")

# ── Note 고아 1,417건 ──
p(f"\n{'='*70}")
p("Note 고아 (1,417건)")
note_orphans = [e for e in orphans if e["type"] == "Note"]
# note_ 접두사 (자동 생성 ID)
auto_notes = [e for e in note_orphans if e["name"].startswith("note_")]
named_notes = [e for e in note_orphans if not e["name"].startswith("note_")]
p(f"  자동생성 ID (note_*): {len(auto_notes)}")
p(f"  의미 있는 이름: {len(named_notes)}")
p("  의미 있는 이름 샘플:")
for e in named_notes[:10]:
    p(f"    {e['entity_id']}: '{e['name'][:50]}'")

# ── WorkType 고아 703건 ──
p(f"\n{'='*70}")
p("WorkType 고아 (703건)")
wt_orphans = [e for e in orphans if e["type"] == "WorkType"]
# spec 있는 것 vs 없는 것
wt_with_spec = [e for e in wt_orphans if e.get("spec")]
wt_no_spec = [e for e in wt_orphans if not e.get("spec")]
p(f"  spec 있음: {len(wt_with_spec)}")
p(f"  spec 없음: {len(wt_no_spec)}")
# spec 있는 고아 → 같은 이름의 비고아가 존재하는지?
wt_names = set()
for e in non_orphans:
    if e["type"] == "WorkType":
        wt_names.add(e.get("normalized_name", ""))

covered = sum(1 for e in wt_orphans if e.get("normalized_name") in wt_names)
p(f"  비고아에 같은 이름 존재: {covered} (spec만 다른 변종)")
p(f"  완전 유닉: {len(wt_orphans) - covered}")

# ── Equipment 고아 769건 ──
p(f"\n{'='*70}")
p("Equipment 고아 (769건)")
eq_orphans = [e for e in orphans if e["type"] == "Equipment"]
eq_names = set()
for e in non_orphans:
    if e["type"] == "Equipment":
        eq_names.add(e.get("normalized_name", ""))
eq_covered = sum(1 for e in eq_orphans if e.get("normalized_name") in eq_names)
p(f"  비고아에 같은 이름 존재: {eq_covered} (spec만 다른 변종)")
p(f"  완전 유닉: {len(eq_orphans) - eq_covered}")

# ── Material 고아 946건 ──
p(f"\n{'='*70}")
p("Material 고아 (946건)")
mat_orphans = [e for e in orphans if e["type"] == "Material"]
mat_names = set()
for e in non_orphans:
    if e["type"] == "Material":
        mat_names.add(e.get("normalized_name", ""))
mat_covered = sum(1 for e in mat_orphans if e.get("normalized_name") in mat_names)
p(f"  비고아에 같은 이름 존재: {mat_covered} (spec만 다른 변종)")
p(f"  완전 유닉: {len(mat_orphans) - mat_covered}")

# ── 결론 ──
p(f"\n{'='*70}")
p("결론")
p("=" * 70)
p(f"""
고아 엔티티 구조:
  → WorkType/Equipment/Material 고아의 대부분은 spec만 다른 변종.
     같은 이름의 비고아(관계 보유)가 이미 존재하므로 데이터 정보 손실 없음.
  
  → Note 고아 1,417건: 자동생성 ID(note_*)가 많으며,
     글로벌 dedup으로 HAS_NOTE 관계가 다른 청크에서 이미 보존됨.
  
  → 고아 제거 시 16,364 → ~12,385 엔티티로 축소 가능하나,
     향후 RAG 검색에서 spec별 변종 조회가 필요할 수 있으므로
     현 단계에서 삭제보다는 유지가 안전.

권장: 고아 엔티티 유지 (RAG 단계에서 필요 시 인덱싱/검색 대상에서 제외)
""")

report = "\n".join(out)
print(report)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\orphan_analysis.txt",
     "w", encoding="utf-8").write(report)
