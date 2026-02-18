# -*- coding: utf-8 -*-
import json

with open('toc_parsed.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

section_map = data['section_map']

# Count by chapter
chapters = {}
for k, v in section_map.items():
    ch = v.get('chapter', 'UNKNOWN')
    chapters[ch] = chapters.get(ch, 0) + 1

print("=== CHAPTER STATISTICS ===")
for ch, cnt in chapters.items():
    print(f"  {ch}: {cnt}")

# Find truncated/problematic sections
print("\n=== PROBLEMATIC SECTIONS (short names <= 10 chars) ===")
sections = {}
for k, v in section_map.items():
    sec = v.get('section', '')
    sections[sec] = sections.get(sec, 0) + 1

for sec, cnt in sections.items():
    if len(sec) <= 10:
        print(f"  '{sec}': {cnt} items")

# Sample entries from end
print("\n=== LAST 10 ENTRIES ===")
items = list(section_map.items())
for k, v in items[-10:]:
    print(f"  {k}: {v}")
