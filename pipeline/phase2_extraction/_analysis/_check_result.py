# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

path = r"G:\내 드라이브\Antigravity\python_code\phase2_output\llm_entities.json"
data = json.loads(open(path, encoding="utf-8").read())

print(f"total_chunks: {data['total_chunks']}")
print(f"processed_chunks: {data['processed_chunks']}")
print(f"total_entities: {data['total_entities']}")
print(f"total_relationships: {data['total_relationships']}")

print("\nentity_type_counts:")
for k, v in sorted(data.get("entity_type_counts", {}).items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

print("\nrelationship_type_counts:")
for k, v in sorted(data.get("relationship_type_counts", {}).items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}")

# 실패 건수
failed = [e for e in data["extractions"] if e.get("confidence", 1) == 0.0]
print(f"\nfailed: {len(failed)}")
for f in failed[:10]:
    print(f"  {f['chunk_id']}: {f.get('warnings', [])}")
