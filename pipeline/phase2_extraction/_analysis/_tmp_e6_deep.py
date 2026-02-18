# -*- coding: utf-8 -*-
"""E6 실패 원인 심층 분석 v2 — 결과를 파일로 저장"""
import json, sys, re, random
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json", encoding="utf-8").read())
raw = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json", encoding="utf-8").read())
chunks_map = {c["chunk_id"]: c for c in raw["chunks"]}

ents = norm["entities"]
random.seed(42)

candidates = [
    e for e in ents
    if e.get("source_chunk_ids")
    and e["type"] not in ("Section",)
    and any(cid in chunks_map for cid in e["source_chunk_ids"])
]

by_type = defaultdict(list)
for e in candidates:
    by_type[e["type"]].append(e)

samples = []
for t, es in by_type.items():
    ratio = len(es) / len(candidates)
    n = max(1, round(ratio * 200))
    samples.extend(random.sample(es, min(n, len(es))))

out = []
causes = Counter()
type_causes = Counter()  # (type, cause)

for e in samples:
    name = e.get("name", "")
    norm_name = e.get("normalized_name", name)
    found = False

    for cid in e.get("source_chunk_ids", []):
        chunk = chunks_map.get(cid)
        if not chunk:
            continue

        text = chunk.get("text", "")
        tables = chunk.get("tables", [])
        table_str = ""
        if isinstance(tables, list):
            for t_item in tables:
                if isinstance(t_item, list):
                    for row in t_item:
                        if isinstance(row, (list, tuple)):
                            table_str += " " + " ".join(str(c) for c in row)
                elif isinstance(t_item, str):
                    table_str += " " + t_item
                elif isinstance(t_item, dict):
                    table_str += " " + json.dumps(t_item, ensure_ascii=False)

        full_text = text + " " + table_str

        if name in full_text:
            found = True; break
        norm_text = re.sub(r"\s+", "", full_text)
        norm_search = re.sub(r"\s+", "", norm_name)
        if norm_search and norm_search in norm_text:
            found = True; break
        tokens = [tok for tok in re.split(r"[\s()（）\[\]]+", name) if len(tok) >= 2]
        if tokens and all(tok in full_text for tok in tokens):
            found = True; break

    if not found:
        cid = e["source_chunk_ids"][0] if e["source_chunk_ids"] else None
        chunk = chunks_map.get(cid, {}) if cid else {}
        table_raw = json.dumps(chunk.get("tables", []), ensure_ascii=False)

        # 원인 분류 개선
        if name in table_raw or norm_name in re.sub(r"\s+", "", table_raw):
            cause = "table_parse_issue"
        elif name.startswith("note_"):
            cause = "synthetic_note_id"
        elif e.get("source_method") == "merged" and norm_name != name:
            cause = "merge_name_change"
        elif any(c in name for c in ["·", "×", "~", "′", "°"]):
            cause = "special_char_name"
        else:
            cause = "not_in_source"

        causes[cause] += 1
        type_causes[(e["type"], cause)] += 1
        out.append(f"  [{cause:20s}] {e['type']:12s} {name[:40]:40s} cid={cid}")

# 출력
lines = []
lines.append("=" * 70)
lines.append("  E6 할루시네이션 원인 분석")
lines.append("=" * 70)
lines.append(f"  총 샘플: {len(samples)}")
total_fail = sum(causes.values())
lines.append(f"  매칭 실패: {total_fail} ({total_fail/len(samples)*100:.1f}%)")
lines.append("")
lines.append("  원인 분류:")
for k, v in causes.most_common():
    lines.append(f"    {k:25s}: {v:4d} ({v/total_fail*100:.1f}%)")
lines.append("")
lines.append("  타입×원인:")
for (t, c), v in type_causes.most_common(15):
    lines.append(f"    {t:12s} × {c:20s}: {v}")
lines.append("")
lines.append("  샘플 (최대 30건):")
for o in out[:30]:
    lines.append(o)

result = "\n".join(lines)
print(result)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\e6_debug.txt", "w", encoding="utf-8").write(result)
