# -*- coding: utf-8 -*-
"""빈 텍스트 청크 원인 분석"""
import json, sys, re
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(open(r"G:\My Drive\Antigravity\pipeline\phase1_output\chunks.json", encoding="utf-8").read())
chunks = data["chunks"]
empty = [c for c in chunks if not c.get("text", "").strip()]
has_text_chunks = [c for c in chunks if c.get("text", "").strip()]

print("=== Part 1: Sub-chunk Pattern ===")
base_ids = defaultdict(list)
for c in chunks:
    cid = c["chunk_id"]
    match = re.match(r"(C-\d+)", cid)
    base = match.group(1) if match else cid
    base_ids[base].append(c)

multi = {k: v for k, v in base_ids.items() if len(v) > 1}
single = {k: v for k, v in base_ids.items() if len(v) == 1}
print(f"Single-chunk sections: {len(single)}")
print(f"Multi-chunk sections: {len(multi)} (total chunks: {sum(len(v) for v in multi.values())})")

# Text pattern in multi-chunk sections
print("\n=== Part 2: Text Distribution in Multi-Chunk Sections ===")
pattern_counts = Counter()
for base, sub_chunks in multi.items():
    pattern = tuple("T" if c.get("text", "").strip() else "E" for c in sorted(sub_chunks, key=lambda x: x["chunk_id"]))
    pattern_counts[pattern] += 1

for p, cnt in pattern_counts.most_common(15):
    desc = " -> ".join(p)
    print(f"  {desc}: {cnt} sections")

# First chunk has text, rest empty?
first_text_rest_empty = 0
first_empty = 0
all_empty_multi = 0
for base, sub_chunks in multi.items():
    sorted_c = sorted(sub_chunks, key=lambda x: x["chunk_id"])
    has_text = [bool(c.get("text", "").strip()) for c in sorted_c]
    if has_text[0] and not any(has_text[1:]):
        first_text_rest_empty += 1
    elif not has_text[0]:
        first_empty += 1
    if not any(has_text):
        all_empty_multi += 1

print(f"\nFirst sub-chunk has text, rest empty: {first_text_rest_empty}")
print(f"First sub-chunk also empty: {first_empty}")
print(f"All sub-chunks empty: {all_empty_multi}")

# 13-2-4 example
print("\n=== Part 3: 13-2-4 Example ===")
target = [c for c in chunks if c.get("section_id", "").startswith("13-2-4")]
for c in sorted(target, key=lambda x: x["chunk_id"]):
    text = c.get("text", "").strip()
    tables = len(c.get("tables", []))
    suffix = c["chunk_id"].replace("C-0956", "")
    print(f"  C-0956{suffix}: text={len(text):>4}ch  tables={tables}  {'TEXT: '+text[:60] if text else '(EMPTY)'}")

# Table-only empty chunks: what types of tables?
print("\n=== Part 4: Empty Text Chunk Table Composition ===")
d_only = sum(1 for c in empty if c.get("tables") and all(t.get("type") == "D_기타" for t in c["tables"]))
a_only = sum(1 for c in empty if c.get("tables") and all(t.get("type") == "A_품셈" for t in c["tables"]))
b_only = sum(1 for c in empty if c.get("tables") and all(t.get("type") == "B_규모기준" for t in c["tables"]))
mixed = len(empty) - d_only - a_only - b_only - 2  # 2 = no table at all
print(f"D_only: {d_only}")
print(f"A_only: {a_only}")
print(f"B_only: {b_only}")
print(f"Mixed: {mixed}")
print(f"No table: 2")

# Sub-chunk suffix distribution
print("\n=== Part 5: Sub-chunk Suffix Distribution ===")
suffixes = Counter()
for c in empty:
    cid = c["chunk_id"]
    match = re.match(r"C-\d+(.*)", cid)
    suffix = match.group(1) if match else ""
    if suffix:
        # Classify: -A, -B, -C, -D, -E, ... -A-a, -A-b, etc.
        parts = suffix.split("-")[1:]  # remove leading empty
        if len(parts) == 1:
            suffixes[f"Level 1 (-{parts[0]})"] += 1
        elif len(parts) == 2:
            suffixes[f"Level 2 (-{parts[0]}-{parts[1]})"] += 1
        elif len(parts) >= 3:
            suffixes[f"Level 3+"] += 1
    else:
        suffixes["Base (no suffix)"] += 1

for k, v in suffixes.most_common():
    print(f"  {k}: {v}")

# Can we recover text from sibling chunks?
print("\n=== Part 6: Sibling Text Recovery Potential ===")
recoverable = 0
not_recoverable = 0
for c in empty:
    cid = c["chunk_id"]
    match = re.match(r"(C-\d+)", cid)
    base = match.group(1) if match else cid
    siblings = base_ids.get(base, [])
    sibling_texts = [s.get("text", "").strip() for s in siblings if s["chunk_id"] != cid and s.get("text", "").strip()]
    if sibling_texts:
        recoverable += 1
    else:
        not_recoverable += 1

print(f"Has sibling with text (recoverable context): {recoverable}")
print(f"No sibling with text (isolated): {not_recoverable}")
