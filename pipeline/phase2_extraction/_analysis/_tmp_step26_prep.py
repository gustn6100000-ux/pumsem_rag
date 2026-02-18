# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
from collections import Counter

d = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json", encoding="utf-8").read())
chunks = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json", encoding="utf-8").read())

print("=== Chunk 구조 ===")
if isinstance(chunks, dict):
    chunk_list = list(chunks.values())
    print(f"  Type: dict (keys = chunk_ids)")
else:
    chunk_list = chunks
    print(f"  Type: list")
c0 = chunk_list[0]
for k in c0:
    print(f"  {k}: {type(c0[k]).__name__} = {str(c0[k])[:60]}")
print(f"  Chunk count: {len(chunk_list)}")

exts = d["extractions"]
total_rels = sum(len(e.get("relationships", [])) for e in exts)
print(f"\nTotal rels across extractions: {total_rels}")

depts = Counter(e.get("department", "?") for e in exts)
print(f"\nDepartments: {dict(depts)}")

ents = d["entities"]
tc = Counter(e["type"] for e in ents)
print(f"\n=== 엔티티 타입별 ({len(ents)}건) ===")
for t, c in tc.most_common():
    print(f"  {t}: {c}")

rc = Counter()
for ext in exts:
    for r in ext.get("relationships", []):
        rc[r["type"]] += 1
print(f"\n=== 관계 타입별 ({total_rels}건) ===")
for t, c in rc.most_common():
    print(f"  {t}: {c}")

# entity 필드 중 None이 아닌 비율
fields = ["code", "spec", "unit", "quantity", "source_section_id", "source_method"]
print("\n=== 엔티티 필드 채워진 비율 ===")
for f in fields:
    filled = sum(1 for e in ents if e.get(f) is not None and e.get(f) != "")
    print(f"  {f}: {filled}/{len(ents)} = {filled/len(ents)*100:.1f}%")
