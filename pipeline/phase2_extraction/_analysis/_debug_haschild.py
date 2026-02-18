# -*- coding: utf-8 -*-
"""HAS_CHILD source/target 필드 분석"""
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())

# HAS_CHILD 관계 구조 확인
has_child = data.get("global_relationships", {}).get("HAS_CHILD", [])
print(f"HAS_CHILD: {len(has_child)}")

# id 매핑 안 된 것들
missing = [r for r in has_child if not r.get("source_entity_id")]
print(f"source_entity_id 빈 것: {len(missing)}")

# Section entities의 name/code/title 확인
sections = [e for e in data["entities"] if e["type"] == "Section"]
section_names = {s["name"] for s in sections}
section_codes = {s.get("code", "") for s in sections}
section_titles = {s.get("title", "") for s in sections}

# missing의 source가 어디에 해당?
for r in missing[:10]:
    src = r.get("source", "")
    in_name = src in section_names
    in_code = src in section_codes
    in_title = src in section_titles
    print(f"  src='{src[:40]}' name={in_name} code={in_code} title={in_title}")
    # section_id도 확인
    print(f"    keys: {list(r.keys())}")

# Section 엔티티 샘플
print("\n\nSection 엔티티 샘플:")
for s in sections[:3]:
    print(f"  name='{s['name'][:40]}' code='{s.get('code', '')}' title='{s.get('title', '')}' entity_id={s.get('entity_id')}")
