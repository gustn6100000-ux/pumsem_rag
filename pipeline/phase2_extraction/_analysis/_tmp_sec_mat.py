# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json", encoding="utf-8").read())

rels = []
for ext in norm.get("extractions", []):
    rels.extend(ext.get("relationships", []))
for rtype, rlist in norm.get("global_relationships", {}).items():
    rels.extend(rlist)

sec_mat = [r for r in rels if r.get("type") == "USES_MATERIAL" and r.get("source_type") == "Section"]
print(f"Section->Material USES_MATERIAL: {len(sec_mat)}건")
for r in sec_mat[:15]:
    src = r.get("source", "")[:35]
    tgt = r.get("target", "")[:30]
    qty = r.get("quantity")
    unit = r.get("unit", "")
    print(f"  [{src}] -> [{tgt}] qty={qty} {unit}")
