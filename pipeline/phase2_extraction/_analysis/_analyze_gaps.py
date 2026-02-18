# -*- coding: utf-8 -*-
"""두 이슈 심층 분석 → 파일 저장"""
import json, sys
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")

merged = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json",
    encoding="utf-8"
).read())

normalized = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())

out = []
def p(s=""):
    out.append(s)

# ═══════════════════════════════════════════════
#  이슈 1: HAS_CHILD 181건
# ═══════════════════════════════════════════════
p("=" * 60)
p("이슈 1: HAS_CHILD source 미매핑")
p("=" * 60)

has_child = normalized["global_relationships"]["HAS_CHILD"]
sections = {}
for e in normalized["entities"]:
    if e["type"] == "Section":
        sections[e["name"]] = e
        if e.get("code"):
            sections[e["code"]] = e

missing_src = [r for r in has_child if not r.get("source_entity_id")]
missing_tgt = [r for r in has_child if not r.get("target_entity_id")]

missing_src_names = Counter(r["source"] for r in missing_src)
p(f"\n미매핑 source 유니크: {len(missing_src_names)}")
p(f"미매핑 target 건수: {len(missing_tgt)}")

p(f"\n미매핑 source 이름:")
for name, cnt in missing_src_names.most_common():
    p(f"  '{name}': {cnt}건")

# 원본에서 존재 여부
merged_sections = set()
for ext in merged["extractions"]:
    for ent in ext.get("entities", []):
        if ent["type"] == "Section":
            merged_sections.add(ent["name"])
            if ent.get("code"):
                merged_sections.add(ent["code"])

p(f"\n원본 merged에서 존재 여부:")
for name in missing_src_names:
    p(f"  '{name}': merged에 존재={name in merged_sections}")

# 미매핑 target 분석  
missing_tgt_names = Counter(r["target"] for r in missing_tgt)
p(f"\n미매핑 target 유니크: {len(missing_tgt_names)}")
for name, cnt in missing_tgt_names.most_common(10):
    in_sect = name in sections
    p(f"  '{name}': {cnt}건, sections에 있음={in_sect}")

# ═══════════════════════════════════════════════
#  이슈 2: 방향 경고 125건
# ═══════════════════════════════════════════════
p("\n" + "=" * 60)
p("이슈 2: 방향 경고 125건")
p("=" * 60)

warnings = [w for w in normalized.get("warnings", []) if w["type"] == "direction_delete"]
p(f"\n방향 삭제 경고: {len(warnings)}건")

warn_chunks = Counter(w["chunk_id"] for w in warnings)
p(f"영향 청크: {len(warn_chunks)}개")

detail_patterns = Counter(w["detail"] for w in warnings)
p(f"\n삭제 패턴:")
for pat, cnt in detail_patterns.most_common():
    p(f"  {pat}: {cnt}")

# 영향 청크의 엔티티 분석
p(f"\n경고 청크 상위 10 분석:")
for chunk_id, cnt in warn_chunks.most_common(10):
    for ext in merged["extractions"]:
        if ext["chunk_id"] == chunk_id:
            ent_types = Counter(e["type"] for e in ext.get("entities", []))
            rel_types = Counter(r["type"] for r in ext.get("relationships", []))
            p(f"\n  {chunk_id} (경고 {cnt}건):")
            p(f"    엔티티: {dict(ent_types)}")
            p(f"    관계: {dict(rel_types)}")
            p(f"    section: {ext.get('section_id', 'N/A')}")
            chunk_warns = [w for w in warnings if w["chunk_id"] == chunk_id]
            for w in chunk_warns[:3]:
                p(f"    경고: {w['detail']}")
            break

report = "\n".join(out)
print(report)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\gap_analysis.txt", 
     "w", encoding="utf-8").write(report)
