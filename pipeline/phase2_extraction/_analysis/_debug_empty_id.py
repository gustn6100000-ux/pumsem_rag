# -*- coding: utf-8 -*-
"""entity_id 빈 문자열 분석"""
import json, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())

empty_src = []
empty_tgt = []
for ext in data.get("extractions", []):
    for r in ext.get("relationships", []):
        if r.get("source_entity_id", "") == "":
            empty_src.append(r)
        if r.get("target_entity_id", "") == "":
            empty_tgt.append(r)

for rtype, rels in data.get("global_relationships", {}).items():
    for r in rels:
        if r.get("source_entity_id", "") == "":
            empty_src.append(r)
        if r.get("target_entity_id", "") == "":
            empty_tgt.append(r)

print(f"source_entity_id 빈 문자열: {len(empty_src)}")
if empty_src:
    src_types = Counter((r.get("source_type", "N/A"), r.get("type", "")) for r in empty_src)
    for k, v in src_types.most_common(10):
        print(f"  {k}: {v}")
    for r in empty_src[:3]:
        print(f"  샘플: {r.get('source_type')}:{r.get('source')[:30]} -> {r.get('target_type')}:{r.get('target', '')[:30]} [{r.get('type')}]")

print(f"\ntarget_entity_id 빈 문자열: {len(empty_tgt)}")
if empty_tgt:
    tgt_types = Counter((r.get("target_type", "N/A"), r.get("type", "")) for r in empty_tgt)
    for k, v in tgt_types.most_common(10):
        print(f"  {k}: {v}")
    for r in empty_tgt[:3]:
        print(f"  샘플: {r.get('source_type')}:{r.get('source', '')[:30]} -> {r.get('target_type')}:{r.get('target', '')[:30]} [{r.get('type')}]")
