# -*- coding: utf-8 -*-
"""Independent audit for Step 2.4 outputs.

Checks consistency between reported numbers and actual normalized_entities.json.
Outputs a human-readable report under phase2_output/quality_report_step24_audit.txt.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERGED = ROOT / "phase2_output" / "merged_entities.json"
NORMALIZED = ROOT / "phase2_output" / "normalized_entities.json"
REPORT = ROOT / "phase2_output" / "quality_report_step24_audit.txt"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_relationships(norm: dict) -> list[dict]:
    rels: list[dict] = []
    for ext in norm.get("extractions", []):
        rels.extend(ext.get("relationships", []))
    for _, glist in norm.get("global_relationships", {}).items():
        rels.extend(glist)
    return rels


def count_outliers_flagged(rels: list[dict]) -> tuple[int, int]:
    thresholds = {
        "REQUIRES_LABOR": 75,
        "REQUIRES_EQUIPMENT": 3300,
        "USES_MATERIAL": 225,
    }
    flagged = 0
    unflagged = 0
    for r in rels:
        rt = r.get("type")
        qty = r.get("quantity")
        th = thresholds.get(rt)
        if th is None or qty is None:
            continue
        if qty > th:
            if r.get("properties", {}).get("outlier_flag"):
                flagged += 1
            else:
                unflagged += 1
    return flagged, unflagged


def count_direction_errors(rels: list[dict], allow_section_material: bool) -> tuple[int, Counter]:
    if allow_section_material:
        uses_material_sources = {"WorkType", "Section"}
    else:
        uses_material_sources = {"WorkType"}

    rules: dict[str, tuple[set[str], str]] = {
        "REQUIRES_LABOR": ({"WorkType"}, "Labor"),
        "REQUIRES_EQUIPMENT": ({"WorkType"}, "Equipment"),
        "USES_MATERIAL": (uses_material_sources, "Material"),
        "HAS_NOTE": ({"WorkType", "Section", "Equipment", "Material", "Standard", "Labor"}, "Note"),
        "APPLIES_STANDARD": ({"WorkType", "Section", "Equipment", "Material"}, "Standard"),
        "BELONGS_TO": ({"WorkType"}, "Section"),
    }

    errors = Counter()
    for r in rels:
        rt = r.get("type", "")
        if rt not in rules:
            continue
        valid_src, exp_tgt = rules[rt]
        st = r.get("source_type", "")
        tt = r.get("target_type", "")
        if st not in valid_src or tt != exp_tgt:
            errors[f"{st}->{tt} ({rt})"] += 1
    return sum(errors.values()), errors


def main() -> None:
    merged = load_json(MERGED)
    norm = load_json(NORMALIZED)

    entities = norm.get("entities", [])
    rels = collect_relationships(norm)

    issues: list[str] = []
    warns: list[str] = []
    lines: list[str] = []

    def add(line: str = "") -> None:
        lines.append(line)

    add("=" * 72)
    add("Step 2.4 Independent Audit")
    add("=" * 72)

    # Structural consistency
    meta_ent = norm.get("total_entities")
    meta_rel = norm.get("total_relationships")
    if meta_ent != len(entities):
        issues.append(f"total_entities mismatch: meta={meta_ent}, actual={len(entities)}")
    if meta_rel != len(rels):
        issues.append(f"total_relationships mismatch: meta={meta_rel}, actual={len(rels)}")

    etc = norm.get("entity_type_counts", {})
    rtc = norm.get("relationship_type_counts", {})
    if sum(etc.values()) != len(entities):
        issues.append(f"entity_type_counts sum mismatch: sum={sum(etc.values())}, actual={len(entities)}")
    if sum(rtc.values()) != len(rels):
        issues.append(f"relationship_type_counts sum mismatch: sum={sum(rtc.values())}, actual={len(rels)}")

    # Normalization stats sanity
    ns = norm.get("normalization_stats", {})
    in_ent = ns.get("input_entities")
    out_ent = ns.get("output_entities")
    dd = ns.get("dedup_removed")
    if isinstance(in_ent, int) and isinstance(out_ent, int) and isinstance(dd, int):
        if in_ent - out_ent != dd:
            issues.append(
                f"normalization_stats arithmetic mismatch: input-output={in_ent - out_ent}, dedup_removed={dd}"
            )
        if out_ent != len(entities):
            issues.append(
                f"normalization_stats.output_entities stale: stats={out_ent}, actual={len(entities)}"
            )

    # IDs
    ids = [e.get("entity_id", "") for e in entities]
    if any(not i for i in ids):
        issues.append("empty entity_id exists")
    if len(set(ids)) != len(ids):
        issues.append("duplicate entity_id exists")

    # Self ref
    self_ref = 0
    for r in rels:
        sid = r.get("source_entity_id")
        tid = r.get("target_entity_id")
        if sid and tid and sid == tid:
            self_ref += 1
    if self_ref:
        issues.append(f"self-referential relations remain: {self_ref}")

    # Duplicate relations by extended key
    dup_ctr = Counter(
        (
            r.get("source_entity_id", ""),
            r.get("target_entity_id", ""),
            r.get("type", ""),
            r.get("quantity"),
            r.get("unit", ""),
            r.get("per_unit", ""),
        )
        for r in rels
    )
    dup_groups = sum(1 for c in dup_ctr.values() if c > 1)
    dup_rows = sum(c - 1 for c in dup_ctr.values() if c > 1)
    if dup_rows:
        issues.append(f"duplicate relations remain: groups={dup_groups}, rows={dup_rows}")

    # Direction errors (two modes)
    err_relaxed, detail_relaxed = count_direction_errors(rels, allow_section_material=True)
    err_strict, detail_strict = count_direction_errors(rels, allow_section_material=False)
    if err_relaxed:
        issues.append(f"direction errors(relaxed rule)={err_relaxed}")
    if err_strict and not err_relaxed:
        warns.append(
            f"strict direction errors={err_strict} (relaxed=0). Section->Material USES_MATERIAL policy needs explicit documentation."
        )

    flagged, unflagged = count_outliers_flagged(rels)
    if unflagged:
        issues.append(f"unflagged outliers remain: {unflagged}")

    # Cross-check with reports' typical claims
    add("[Counts]")
    add(f"entities={len(entities):,}, relationships={len(rels):,}")
    add(f"entity_type_counts={dict(sorted(etc.items()))}")
    add(f"relationship_type_counts={dict(sorted(rtc.items()))}")
    add("")

    add("[Core Checks]")
    add(f"direction_errors_relaxed={err_relaxed}")
    add(f"direction_errors_strict={err_strict}")
    if err_strict:
        top = detail_strict.most_common(5)
        add("strict_direction_top5=" + str(top))
    add(f"duplicates_extkey_groups={dup_groups}, rows={dup_rows}")
    add(f"outliers_flagged={flagged}, outliers_unflagged={unflagged}")
    add("")

    add("[Consistency Checks]")
    add(f"meta_total_entities={meta_ent}, actual={len(entities)}")
    add(f"meta_total_relationships={meta_rel}, actual={len(rels)}")
    add(f"stats_output_entities={out_ent}, actual={len(entities)}")
    add(f"stats_dedup_removed={dd}, input-output={(in_ent - out_ent) if isinstance(in_ent, int) and isinstance(out_ent, int) else 'n/a'}")
    add("")

    add("[Verdict]")
    if issues:
        add("FAIL")
        for i in issues:
            add(f"- {i}")
    else:
        add("PASS")
    if warns:
        add("[Warnings]")
        for w in warns:
            add(f"- {w}")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved: {REPORT}")


if __name__ == "__main__":
    main()
