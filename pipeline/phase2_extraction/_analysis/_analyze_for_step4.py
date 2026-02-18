# -*- coding: utf-8 -*-
import json, sys, re, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(Path(r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json").read_text(encoding="utf-8"))
exts = data["extractions"]
OUT = Path(r"G:\내 드라이브\Antigravity\python_code\phase2_output\_step4_analysis.txt")

all_ents = []
for ext in exts:
    for e in ext.get("entities", []):
        e["_chunk_id"] = ext["chunk_id"]
        all_ents.append(e)

lines = []
def p(s=""):
    lines.append(s)

p(f"총 엔티티: {len(all_ents):,}")
p()
p("[1. 타입별 전체 vs 고유 이름]")
for etype in ["WorkType", "Labor", "Equipment", "Material", "Note", "Section", "Standard"]:
    typed = [e for e in all_ents if e["type"] == etype]
    unique_names = set(e.get("normalized_name", e["name"].replace(" ", "")) for e in typed)
    p(f"  {etype:12s}: 전체 {len(typed):>6,} | 고유 {len(unique_names):>6,} | 중복률 {(1-len(unique_names)/max(len(typed),1))*100:.1f}%")

p()
p("[2. 가장 자주 등장 TOP 15]")
name_key_count = Counter()
for e in all_ents:
    key = f"{e['type']}::{e.get('normalized_name', e['name'].replace(' ', ''))}"
    name_key_count[key] += 1
for name, cnt in name_key_count.most_common(15):
    p(f"  {cnt:>5}회: {name}")

p()
p("[3. 공백 변형 후보]")
space_variants = defaultdict(set)
for e in all_ents:
    norm = e.get("normalized_name", e["name"].replace(" ", "")).lower()
    space_variants[(e["type"], norm)].add(e["name"])
space_dup = [(k, v) for k, v in space_variants.items() if len(v) > 1]
p(f"  공백 변형 그룹: {len(space_dup)}")
for (t, n), names in sorted(space_dup, key=lambda x: -len(x[1]))[:10]:
    p(f"  [{t}] {n}: {sorted(names)}")

p()
p("[4. 관계 방향 오류]")
dir_errors = Counter()
for ext in exts:
    for r in ext.get("relationships", []):
        st = r.get("source_type", "")
        tt = r.get("target_type", "")
        rt = r.get("type", "")
        if rt == "REQUIRES_LABOR" and st != "WorkType":
            dir_errors[f"{st}→{tt} ({rt})"] += 1
        elif rt == "REQUIRES_EQUIPMENT" and st != "WorkType":
            dir_errors[f"{st}→{tt} ({rt})"] += 1
        elif rt == "USES_MATERIAL" and st != "WorkType":
            dir_errors[f"{st}→{tt} ({rt})"] += 1
        elif rt == "HAS_NOTE" and st == tt:
            dir_errors[f"{st}→{tt} ({rt})"] += 1
total_dir = sum(dir_errors.values())
p(f"  총 방향 오류: {total_dir}")
for pattern, cnt in dir_errors.most_common():
    p(f"    {pattern}: {cnt}")

p()
p("[5. 수량 이상치]")
for rtype in ["REQUIRES_LABOR", "REQUIRES_EQUIPMENT", "USES_MATERIAL"]:
    qtys = []
    for ext in exts:
        for r in ext.get("relationships", []):
            if r["type"] == rtype and r.get("quantity") is not None:
                qtys.append(r["quantity"])
    if qtys:
        qtys.sort()
        p95 = qtys[int(len(qtys)*0.95)] if len(qtys) > 20 else max(qtys)
        neg = sum(1 for q in qtys if q < 0)
        big = sum(1 for q in qtys if q > p95 * 3)
        p(f"  {rtype}: n={len(qtys)}, min={min(qtys):.2f}, max={max(qtys):.2f}, p95={p95:.2f}, 음수={neg}, 이상치(>p95*3)={big}")

p()
p("[6. confidence 분포]")
confs = [e.get("confidence", 1.0) for e in all_ents]
bins = [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)]
for lo, hi in bins:
    cnt = sum(1 for c in confs if lo <= c < hi)
    p(f"  {lo:.1f}~{hi:.2f}: {cnt:>6,} ({cnt/len(confs)*100:.1f}%)")

p()
empty = sum(1 for e in all_ents if not e.get("name", "").strip())
p(f"빈 이름 엔티티: {empty}")

p()
unicode_issues = 0
for e in all_ents:
    name = e.get("name", "")
    nfkc = unicodedata.normalize("NFKC", name)
    if name != nfkc:
        unicode_issues += 1
p(f"NFKC 정규화 대상: {unicode_issues}")

# 정규화 후 예상 고유 엔티티 수
p()
p("[7. 정규화 후 예상 고유 엔티티 수]")
for etype in ["WorkType", "Labor", "Equipment", "Material", "Note", "Standard"]:
    typed = [e for e in all_ents if e["type"] == etype]
    unique = set()
    for e in typed:
        norm = unicodedata.normalize("NFKC", e.get("normalized_name", e["name"].replace(" ", ""))).lower().strip()
        spec = (e.get("spec") or "").strip()
        if etype in ("WorkType", "Equipment", "Material"):
            unique.add((etype, norm, spec))
        else:
            unique.add((etype, norm))
    p(f"  {etype:12s}: 전체 {len(typed):>6,} → 고유 {len(unique):>6,} (감소 {len(typed)-len(unique):>5,})")

OUT.write_text("\n".join(lines), encoding="utf-8")
print(f"저장: {OUT}")
print("\n".join(lines))
