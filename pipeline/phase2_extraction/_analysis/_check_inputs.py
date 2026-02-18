# -*- coding: utf-8 -*-
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

# 1. toc_parsed.json
toc = Path(r"G:\내 드라이브\Antigravity\python_code\toc_parser\toc_parsed.json")
print(f"toc_parsed.json exists: {toc.exists()}")
if toc.exists():
    d = json.loads(toc.read_text(encoding="utf-8"))
    sm = d.get("section_map", {})
    print(f"  section_map keys: {len(sm)}")
    for i, (k, v) in enumerate(sm.items()):
        if i >= 3: break
        print(f"  {k}: {v}")

# 2. cross_references
chunks = json.loads(Path(r"G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json").read_text(encoding="utf-8"))
xref_count = sum(1 for c in chunks["chunks"] if c.get("cross_references"))
xref_total = sum(len(c.get("cross_references", [])) for c in chunks["chunks"])
print(f"\nchunks with cross_refs: {xref_count}")
print(f"total cross_refs: {xref_total}")

for c in chunks["chunks"]:
    if c.get("cross_references"):
        for xr in c["cross_references"][:2]:
            ctx = xr.get("context", "?")[:60]
            print(f"  [{c['chunk_id']}] ref={xr.get('ref_section','?')} ctx={ctx}")
        break

# 3. section_id 분포
from collections import Counter
sids = Counter(c.get("section_id", "") for c in chunks["chunks"])
print(f"\nunique section_ids in chunks: {len(sids)}")

# 4. dept/chapter 분포
depts = Counter(c.get("department", "") for c in chunks["chunks"])
print(f"\ndepartments:")
for d, n in depts.most_common():
    print(f"  {d}: {n}")
