# -*- coding: utf-8 -*-
import json, sys, os
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json", encoding="utf-8").read())
rels = []
for ext in norm.get("extractions", []):
    rels.extend(ext.get("relationships", []))
for rtype, rlist in norm.get("global_relationships", {}).items():
    rels.extend(rlist)

no_unit = [r for r in rels if r.get("quantity") is not None and not r.get("unit")]
rt_cnt = Counter(r.get("type", "") for r in no_unit)
print(f"수량+단위없음 {len(no_unit)}건 타입별: {dict(rt_cnt)}")
for r in no_unit[:8]:
    rt = r.get("type", "")
    qty = r.get("quantity")
    src = r.get("source", "")[:25]
    tgt = r.get("target", "")[:25]
    print(f"  {rt}: qty={qty} [{src}] -> [{tgt}]")

chunks_path = r"G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json"
print(f"\nchunks.json 존재: {os.path.exists(chunks_path)}")
if os.path.exists(chunks_path):
    chunks = json.loads(open(chunks_path, encoding="utf-8").read())
    if isinstance(chunks, list):
        print(f"청크 수: {len(chunks)}")
        if chunks:
            print(f"청크 키: {list(chunks[0].keys())[:10]}")
    elif isinstance(chunks, dict):
        print(f"키: {list(chunks.keys())[:5]}")
