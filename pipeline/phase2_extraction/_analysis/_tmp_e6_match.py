# -*- coding: utf-8 -*-
"""E6 2단계 매칭이 실패하는 이유 정밀 추적"""
import json, sys, re
sys.stdout.reconfigure(encoding="utf-8")

raw = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json", encoding="utf-8").read())
chunks_map = {c["chunk_id"]: c for c in raw["chunks"]}

# "기타기계" 테스트 — C-0299-A-d
chunk = chunks_map.get("C-0299-A-d", {})

def flatten(obj):
    parts = []
    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, (int, float)):
        parts.append(str(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            parts.extend(flatten(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            parts.extend(flatten(item))
    return parts

text = chunk.get("text", "")
table_flat = " ".join(flatten(chunk.get("tables", [])))
full = text + " " + table_flat

# 공백 제거
no_space = re.sub(r"\s+", "", full)

search = "기타기계"
print(f"Search: '{search}'")
print(f"In full_text: {search in full}")
print(f"In no_space: {search in no_space}")

# "골재생산기계" — C-0297-I
chunk2 = chunks_map.get("C-0297-I", {})
full2 = chunk2.get("text", "") + " " + " ".join(flatten(chunk2.get("tables", [])))
no_space2 = re.sub(r"\s+", "", full2)
search2 = "골재생산기계"
print(f"\nSearch: '{search2}'")
print(f"In full_text: {search2 in full2}")
print(f"In no_space: {search2 in no_space2}")

# "치즐" — C-0200-A-a
chunk3 = chunks_map.get("C-0200-A-a", {})
full3 = chunk3.get("text", "") + " " + " ".join(flatten(chunk3.get("tables", [])))
no_space3 = re.sub(r"\s+", "", full3)
search3 = "치즐"
print(f"\nSearch: '{search3}'")
print(f"In full_text: {search3 in full3}")
print(f"In no_space: {search3 in no_space3}")
# Look for partial
for i in range(len(no_space3) - 1):
    if no_space3[i:i+2] == "치즐":
        print(f"  FOUND at position {i}: ...{no_space3[max(0,i-10):i+20]}...")
        break

# "구리합금관" — C-0790-A
chunk4 = chunks_map.get("C-0790-A", {})
full4 = chunk4.get("text", "") + " " + " ".join(flatten(chunk4.get("tables", [])))
no_space4 = re.sub(r"\s+", "", full4)
search4 = "구리합금관"
print(f"\nSearch: '{search4}'")
print(f"In full_text: {search4 in full4}")
print(f"In no_space: {search4 in no_space4}")

# What about "보정계수" and "시가지" — C-0535-B
chunk5 = chunks_map.get("C-0535-B", {})
full5 = chunk5.get("text", "") + " " + " ".join(flatten(chunk5.get("tables", [])))
no_space5 = re.sub(r"\s+", "", full5)
search5a = "지형별"
search5b = "보정계수"
print(f"\nSearch: '{search5a}' and '{search5b}'")
print(f"'{search5a}' in no_space: {search5a in no_space5}")
print(f"'{search5b}' in no_space: {search5b in no_space5}")
print(f"'지형별보정계수(시가지)' in no_space: {'지형별보정계수(시가지)' in no_space5}")
print(f"'지형별보정계수시가지' in no_space: {'지형별보정계수시가지' in no_space5}")
