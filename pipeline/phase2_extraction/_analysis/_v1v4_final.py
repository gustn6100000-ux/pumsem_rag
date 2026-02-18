# -*- coding: utf-8 -*-
"""V1 잔여 26건 + V4 최종 판별 → 파일 저장"""
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

rel_by_ent = Counter()
for r in all_rels:
    rel_by_ent[r.get("source_entity_id", "")] += 1
    rel_by_ent[r.get("target_entity_id", "")] += 1

out = []
def p(s=""):
    out.append(s)

p("=" * 70)
p("  V1 잔여 26건 (1글자 한글) 최종 판별")
p("=" * 70)

short = [e for e in ents if len(e.get("name", "")) == 1 
         and e["type"] in ("WorkType", "Equipment", "Material", "Labor")]

valid_1char = []
garbage_1char = []
for e in short:
    eid = e.get("entity_id", "")
    rels_n = rel_by_ent.get(eid, 0)
    spec = e.get("spec", "")
    chunks = len(e.get("source_chunk_ids", []))
    p(f"  [{e['type']}] name='{e['name']}' spec='{spec[:30]}' "
      f"rels={rels_n} chunks={chunks} id={eid}")
    if rels_n > 0:
        valid_1char.append(e)
    else:
        garbage_1char.append(e)

p(f"\n  유효 (관계 있음): {len(valid_1char)}건")
p(f"  가비지 후보 (관계 없음): {len(garbage_1char)}건")
if garbage_1char:
    p("  가비지 후보:")
    for e in garbage_1char:
        p(f"    {e['entity_id']}: '{e['name']}' ({e['type']}) spec='{e.get('spec','')}'")

p("\n" + "=" * 70)
p("  V4 의미없는 spec 분석")
p("=" * 70)
trivial_specs = []
for e in ents:
    s = e.get("spec", "")
    if s and (len(s) == 1 or re.match(r"^\d+$", s)):
        eid = e.get("entity_id", "")
        rels_n = rel_by_ent.get(eid, 0)
        trivial_specs.append((e, rels_n))

trivial_no_rel = [(e, r) for e, r in trivial_specs if r == 0]
trivial_with_rel = [(e, r) for e, r in trivial_specs if r > 0]

p(f"  의미없는 spec 총: {len(trivial_specs)}건")
p(f"  관계 있음: {len(trivial_with_rel)}건")
p(f"  관계 없음: {len(trivial_no_rel)}건")
p(f"  타입별: {dict(Counter(e['type'] for e, _ in trivial_specs))}")

p("\n" + "=" * 70)
p("  V4 과잉분화 관계 유무")
p("=" * 70)
by_name = defaultdict(list)
for e in ents:
    by_name[(e["type"], e.get("normalized_name", ""))].append(e)
over_split = {k: v for k, v in by_name.items() if len(v) >= 5}

overall_total = 0
overall_orphan = 0
for k, es in over_split.items():
    total = len(es)
    orphan = sum(1 for e in es if rel_by_ent.get(e.get("entity_id"), 0) == 0)
    overall_total += total
    overall_orphan += orphan

p(f"  과잉분화 그룹 엔티티: {overall_total}")
p(f"  관계 없음: {overall_orphan} ({overall_orphan/overall_total*100:.1f}%)")

report = "\n".join(out)
print(report)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\v1v4_final.txt", 
     "w", encoding="utf-8").write(report)
