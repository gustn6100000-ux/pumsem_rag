# -*- coding: utf-8 -*-
"""Step 2.1 결과 검증 스크립트"""
import json, sys
from pathlib import Path
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

result_path = Path(__file__).parent.parent / "phase2_output" / "table_entities.json"
data = json.loads(result_path.read_text(encoding="utf-8"))

# REQUIRES_LABOR 관계 샘플
labor_rels = []
equip_rels = []
belongs_rels = []
for ext in data["extractions"]:
    for r in ext["relationships"]:
        if r["type"] == "REQUIRES_LABOR":
            labor_rels.append((ext["section_id"], ext["title"], r["source"], r["target"], r["quantity"], r["unit"]))
        elif r["type"] == "REQUIRES_EQUIPMENT":
            equip_rels.append((ext["section_id"], ext["title"], r["source"], r["target"], r["quantity"], r["unit"]))
        elif r["type"] == "BELONGS_TO":
            belongs_rels.append((ext["section_id"], r["source"], r["target"]))

print(f"=== REQUIRES_LABOR samples ({len(labor_rels)} total) ===")
for item in labor_rels[:10]:
    print(f"  [{item[0]}] {item[1]} | {item[2]} → {item[3]} ({item[4]} {item[5]})")

print(f"\n=== REQUIRES_EQUIPMENT samples ({len(equip_rels)} total) ===")
for item in equip_rels[:10]:
    print(f"  [{item[0]}] {item[1]} | {item[2]} → {item[3]} ({item[4]} {item[5]})")

print(f"\n=== BELONGS_TO samples ({len(belongs_rels)} total) ===")
for item in belongs_rels[:10]:
    print(f"  [{item[0]}] {item[1]} → {item[2]}")

# WorkType 엔티티 상위 빈도
wt_names = Counter()
for ext in data["extractions"]:
    for e in ext["entities"]:
        if e["type"] == "WorkType":
            wt_names[e["normalized_name"]] += 1
print(f"\n=== Top 15 WorkType entities ===")
for name, cnt in wt_names.most_common(15):
    print(f"  [{cnt:3d}] {name}")

# Labor 엔티티 상위 빈도
labor_names = Counter()
for ext in data["extractions"]:
    for e in ext["entities"]:
        if e["type"] == "Labor":
            labor_names[e["normalized_name"]] += 1
print(f"\n=== Top 15 Labor entities ===")
for name, cnt in labor_names.most_common(15):
    print(f"  [{cnt:3d}] {name}")

# 경고 분석
warn_counter = Counter()
for ext in data["extractions"]:
    for w in ext["warnings"]:
        warn_counter[w[:80]] += 1
print(f"\n=== Warning patterns ({sum(warn_counter.values())} total) ===")
for w, cnt in warn_counter.most_common(10):
    print(f"  [{cnt:3d}] {w}")
