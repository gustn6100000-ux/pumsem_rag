# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")
from collections import Counter

d = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json", encoding="utf-8").read())
ents = d.get("entities", [])
rels = d.get("relationships", [])

tc = Counter(e["type"] for e in ents)
rc = Counter(r["type"] for r in rels)

print(f"총 엔티티: {len(ents)}")
print(f"총 관계: {len(rels)}")
print()
print("엔티티 타입별:")
for t, c in tc.most_common():
    print(f"  {t}: {c}")
print()
print("관계 타입별:")
for t, c in rc.most_common():
    print(f"  {t}: {c}")

# 엔티티 필드 샘플
print("\n=== 엔티티 필드 샘플 ===")
sample = ents[0]
for k in sample.keys():
    v = sample[k]
    print(f"  {k}: {type(v).__name__} = {str(v)[:80]}")

# 관계 필드 샘플
print("\n=== 관계 필드 샘플 ===")
sample_r = rels[0]
for k in sample_r.keys():
    v = sample_r[k]
    print(f"  {k}: {type(v).__name__} = {str(v)[:80]}")

# chunks.json 구조 확인
chunks = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json", encoding="utf-8").read())
print(f"\n=== 청크 ===")
print(f"총 청크: {len(chunks)}")
sample_c = chunks[0]
print("청크 필드:")
for k in sample_c.keys():
    v = sample_c[k]
    print(f"  {k}: {type(v).__name__} = {str(v)[:80]}")
