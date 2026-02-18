# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
from collections import Counter

d = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json", encoding="utf-8").read())

print("Top-level keys:")
for k in d.keys():
    v = d[k]
    if isinstance(v, list):
        print(f"  {k}: list[{len(v)}]")
    elif isinstance(v, dict):
        print(f"  {k}: dict[{len(v)} keys]")
    else:
        print(f"  {k}: {type(v).__name__} = {str(v)[:60]}")

# 관계 찾기
for k in d.keys():
    if "rel" in k.lower() or "edge" in k.lower():
        print(f"\nFound relationships key: {k} -> len={len(d[k])}")
        if len(d[k]) > 0:
            print("  Sample keys:", list(d[k][0].keys()))

# extractions 구조 확인
if "extractions" in d:
    exts = d["extractions"]
    print(f"\nextractions: {len(exts)} items")
    if exts:
        ext0 = exts[0]
        print("  Keys:", list(ext0.keys()))
        if "relationships" in ext0:
            all_rels = []
            for ext in exts:
                all_rels.extend(ext.get("relationships", []))
            print(f"  Total relationships across extractions: {len(all_rels)}")
            if all_rels:
                rc = Counter(r["type"] for r in all_rels)
                print("  관계 타입별:")
                for t, c in rc.most_common():
                    print(f"    {t}: {c}")
                print("  Sample rel keys:", list(all_rels[0].keys()))
