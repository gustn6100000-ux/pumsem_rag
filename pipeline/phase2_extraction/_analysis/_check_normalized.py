# -*- coding: utf-8 -*-
"""Step 2.4 결과 확인"""
import json, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())

print(f"total_entities: {data['total_entities']:,}")
print(f"total_relationships: {data['total_relationships']:,}")
print()

print("entity_type_counts:")
for t, c in sorted(data["entity_type_counts"].items(), key=lambda x: -x[1]):
    print(f"  {t}: {c:,}")

print()
print("relationship_type_counts:")
for t, c in sorted(data["relationship_type_counts"].items(), key=lambda x: -x[1]):
    print(f"  {t}: {c:,}")

print()
print("normalization_stats:")
stats = data["normalization_stats"]
for k, v in stats.items():
    print(f"  {k}: {v}")

print()
print(f"warnings: {len(data.get('warnings', []))}")
warn_types = Counter(w.get("type") for w in data.get("warnings", []))
for t, c in warn_types.most_common():
    print(f"  {t}: {c}")

print()
# entity_id 품질
ents = data["entities"]
ids = [e.get("entity_id", "") for e in ents]
print(f"entity_id 부여: {sum(1 for i in ids if i):,} / {len(ents):,}")
print(f"entity_id 유니크: {len(set(ids)):,}")
print(f"source_chunk_ids 평균: {sum(len(e.get('source_chunk_ids', [])) for e in ents)/len(ents):.1f}")

# 관계에서 entity_id 존재 여부
total_rels = 0
has_src_id = 0
has_tgt_id = 0
for ext in data.get("extractions", []):
    for r in ext.get("relationships", []):
        total_rels += 1
        if r.get("source_entity_id"):
            has_src_id += 1
        if r.get("target_entity_id"):
            has_tgt_id += 1
global_rels = data.get("global_relationships", {})
for rtype, rels in global_rels.items():
    for r in rels:
        total_rels += 1
        if r.get("source_entity_id"):
            has_src_id += 1
        if r.get("target_entity_id"):
            has_tgt_id += 1

print(f"\n관계 entity_id 매핑:")
print(f"  총 관계: {total_rels:,}")
print(f"  source_entity_id: {has_src_id:,} ({has_src_id/total_rels*100:.1f}%)")
print(f"  target_entity_id: {has_tgt_id:,} ({has_tgt_id/total_rels*100:.1f}%)")

# 파일 크기
import os
fsize = os.path.getsize(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json")
print(f"\n파일 크기: {fsize/1024/1024:.1f} MB")
