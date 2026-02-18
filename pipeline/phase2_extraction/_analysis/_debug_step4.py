# -*- coding: utf-8 -*-
"""Step 2.4 차이 분석: 예상 vs 실측"""
import json, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())

# 1. Note 중복 분석 — 왜 6,249인가 (예상 5,212)
ents = data["entities"]
notes = [e for e in ents if e["type"] == "Note"]
print("=== Note 분석 ===")
print(f"  총 Note: {len(notes)}")

# Note 키: (type, normalized_name, source_section_id)
note_keys = Counter()
for n in notes:
    key = (n.get("normalized_name", ""), n.get("source_section_id", "unknown"))
    note_keys[key] += 1

multi = {k: v for k, v in note_keys.items() if v > 1}
print(f"  중복 키: {len(multi)}")
print(f"  중복 키 top 10:")
for k, v in sorted(multi.items(), key=lambda x: -x[1])[:10]:
    print(f"    {k}: {v}")

# Note에서 source_section_id 분포
sid_counter = Counter(n.get("source_section_id", "N/A") for n in notes)
print(f"\n  source_section_id 분포:")
print(f"    가진 Note: {sum(1 for n in notes if n.get('source_section_id')):,}")
print(f"    없는 Note: {sum(1 for n in notes if not n.get('source_section_id')):,}")
print(f"    유니크 sid: {len(sid_counter)}")

# 2. 방향 삭제 2828건 분석
warnings = data.get("warnings", [])
dir_del = [w for w in warnings if w["type"] == "direction_delete"]
print(f"\n=== 방향 삭제 분석 ({len(dir_del)}건) ===")
detail_patterns = Counter()
for w in dir_del:
    d = w.get("detail", "")
    # 패턴 추출
    if "No WorkType" in d:
        detail_patterns["No WorkType in chunk"] += 1
    elif "Unhandled" in d:
        detail_patterns[d[:50]] += 1
    else:
        detail_patterns[d[:50]] += 1

print("  패턴별:")
for p, c in detail_patterns.most_common(20):
    print(f"    {p}: {c}")

# 3. entity_id 누락 관계 분석
missing_src = []
missing_tgt = []
for ext in data.get("extractions", []):
    for r in ext.get("relationships", []):
        if not r.get("source_entity_id"):
            missing_src.append(r)
        if not r.get("target_entity_id"):
            missing_tgt.append(r)

print(f"\n=== entity_id 누락 관계 ===")
print(f"  source 누락: {len(missing_src)}")
print(f"  target 누락: {len(missing_tgt)}")

if missing_src:
    src_types = Counter((r.get("source_type", ""), r.get("type", "")) for r in missing_src)
    print(f"\n  source 누락 패턴 (type, rel_type):")
    for k, v in src_types.most_common(10):
        print(f"    {k}: {v}")
    print(f"\n  source 누락 샘플:")
    for r in missing_src[:5]:
        print(f"    {r.get('source_type')}:{r.get('source')} → {r.get('target_type')}:{r.get('target')} [{r.get('type')}]")

if missing_tgt:
    tgt_types = Counter((r.get("target_type", ""), r.get("type", "")) for r in missing_tgt)
    print(f"\n  target 누락 패턴 (type, rel_type):")
    for k, v in tgt_types.most_common(10):
        print(f"    {k}: {v}")
    print(f"\n  target 누락 샘플:")
    for r in missing_tgt[:5]:
        print(f"    {r.get('source_type')}:{r.get('source')} → {r.get('target_type')}:{r.get('target')} [{r.get('type')}]")
