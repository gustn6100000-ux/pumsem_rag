# -*- coding: utf-8 -*-
"""V1 불량 이름 53건 상세 분석 + 가비지 판별"""
import json, sys, re, unicodedata
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())
ents = norm["entities"]

print("=" * 70)
print("  V1 불량 이름 전수 분석")
print("=" * 70)

# ── 1글자 이하 ──
print("\n━━━ 1글자 이하 엔티티 ━━━")
short = [e for e in ents if len(e.get("name", "")) <= 1 
         and e["type"] in ("WorkType", "Equipment", "Material", "Labor")]
for e in short:
    # 이 엔티티를 참조하는 관계 수
    eid = e.get("entity_id", "")
    rel_count = 0
    for ext in norm.get("extractions", []):
        for r in ext.get("relationships", []):
            if r.get("source_entity_id") == eid or r.get("target_entity_id") == eid:
                rel_count += 1
    
    src_chunks = e.get("source_chunk_ids", [])
    print(f"  [{e['type']}] name='{e['name']}' norm='{e.get('normalized_name','')}' "
          f"spec='{e.get('spec','')}' id={eid} "
          f"chunks={len(src_chunks)} rels={rel_count} conf={e.get('confidence',0)}")

# 가비지 판별 기준:
# - name이 특수문자만 (-, ", →) → 가비지
# - name이 1글자 한글이지만 유효한 자재/장비 (붓, 잭, 개 등) → 유효
print("\n  판별:")
garbage = []
valid_short = []
for e in short:
    name = e["name"]
    if name in ("-", "\"", "→", ":", "", " "):
        garbage.append(e)
        print(f"    ❌ 가비지: '{name}' ({e['type']})")
    elif re.match(r"^[가-힣]$", name):
        # 한글 1글자 → 맥락 확인 필요
        if name in ("붓", "잭", "핀", "삽", "솔", "줄", "봉", "관", "판", "통"):
            valid_short.append(e)
            print(f"    ✅ 유효: '{name}' ({e['type']}) — 실제 장비/자재명")
        else:
            # 모호 — chunk에서 확인
            valid_short.append(e)
            print(f"    ⚠️ 모호: '{name}' ({e['type']}) spec='{e.get('spec','')}'")
    else:
        if re.match(r"^[a-zA-Z]$", name):
            garbage.append(e)
            print(f"    ❌ 가비지: '{name}' ({e['type']})")
        else:
            valid_short.append(e)
            print(f"    ⚠️ 기타: '{name}' ({e['type']})")

print(f"\n  총: {len(short)}건, 가비지: {len(garbage)}건, 유효: {len(valid_short)}건")

# ── NFKC 미완료 ──
print("\n━━━ NFKC 미완료 엔티티 ━━━")
nfkc_issues = []
for e in ents:
    name = e.get("name", "")
    nfkc = unicodedata.normalize("NFKC", name)
    if nfkc != name:
        diff_chars = [(c, unicodedata.normalize("NFKC", c)) for c in name if unicodedata.normalize("NFKC", c) != c]
        nfkc_issues.append((e, diff_chars))

print(f"  총: {len(nfkc_issues)}건")
# 어떤 호환문자가 남아있는지
compat_chars = Counter()
for e, diffs in nfkc_issues:
    for orig, converted in diffs:
        compat_chars[f"'{orig}'→'{converted}'"] += 1

print(f"  호환문자 분포:")
for c, cnt in compat_chars.most_common():
    print(f"    {c}: {cnt}")

print(f"\n  샘플:")
for e, diffs in nfkc_issues[:10]:
    print(f"    [{e['type']}] '{e['name'][:50]}' id={e.get('entity_id')}")

# 가비지 엔티티 ID 목록 저장
garbage_ids = [e["entity_id"] for e in garbage]
print(f"\n━━━ 제거 대상 가비지 ━━━")
print(f"  {len(garbage_ids)}건: {garbage_ids}")
