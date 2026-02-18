# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json",
    encoding="utf-8"
).read())

print(f"total_chunks: {data['total_chunks']}")
print(f"total_entities: {data['total_entities']:,}")
print(f"total_relationships: {data['total_relationships']:,}")

print("\nmerge_stats:")
for k, v in data.get("merge_stats", {}).items():
    print(f"  {k}: {v:,}" if isinstance(v, int) else f"  {k}: {v}")

print("\nentity_type_counts:")
for k, v in sorted(data.get("entity_type_counts", {}).items(), key=lambda x: -x[1]):
    print(f"  {k}: {v:,}")

print("\nrelationship_type_counts:")
for k, v in sorted(data.get("relationship_type_counts", {}).items(), key=lambda x: -x[1]):
    print(f"  {k}: {v:,}")

# global rels
gr = data.get("global_relationships", {})
print(f"\nglobal HAS_CHILD: {len(gr.get('HAS_CHILD', []))}")
print(f"global REFERENCES: {len(gr.get('REFERENCES', []))}")

# 파일 크기
import os
fsize = os.path.getsize(r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json")
print(f"\nfile size: {fsize / 1024 / 1024:.1f} MB")
