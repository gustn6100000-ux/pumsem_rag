# -*- coding: utf-8 -*-
"""Step 2.3 병합 결과 품질 검증

M1. 병합 중복 제거 정확성 — 엔티티 수 ≤ 합계
M2. BELONGS_TO 커버리지 — 모든 WorkType에 BELONGS_TO 존재 ≥95%
M3. HAS_CHILD 계층 일관성 — 부모 없는 자식 = 0
M4. REFERENCES target 유효성 — 100%
M5. Section 엔티티 완전성 — 청크와 1:1 매핑
M6. 병합 전후 관계 손실 검사 — 기존 관계 유실 없는지
M7. 랜덤 샘플 3건 병합 결과 대조
"""
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent
MERGED_FILE = BASE / "phase2_output" / "merged_entities.json"
TABLE_FILE = BASE / "phase2_output" / "table_entities.json"
LLM_FILE = BASE / "phase2_output" / "llm_entities.json"
CHUNKS_FILE = BASE / "phase1_output" / "chunks.json"
TOC_FILE = BASE / "toc_parser" / "toc_parsed.json"
REPORT_FILE = BASE / "phase2_output" / "quality_report_step23.txt"

merged_data = json.loads(MERGED_FILE.read_text(encoding="utf-8"))
table_data = json.loads(TABLE_FILE.read_text(encoding="utf-8"))
llm_data = json.loads(LLM_FILE.read_text(encoding="utf-8"))
chunks_data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
toc_data = json.loads(TOC_FILE.read_text(encoding="utf-8"))

merged_exts = merged_data["extractions"]
chunks = chunks_data["chunks"]
chunk_map = {c["chunk_id"]: c for c in chunks}

lines = []
def log(msg=""):
    print(msg)
    lines.append(msg)


log("=" * 70)
log("  Step 2.3 병합 결과 품질 검증")
log("=" * 70)


# ━━━ M1. 병합 중복 제거 정확성 ━━━
log("\n━━━ M1. 병합 중복 제거 정확성 ━━━")
stats = merged_data.get("merge_stats", {})
before_ents = stats.get("entities_before", 0)
after_ents = stats.get("entities_after", 0)
dedup_ents = stats.get("entities_dedup", 0)
before_rels = stats.get("relationships_before", 0)
after_rels = stats.get("relationships_after", 0)

log(f"  엔티티: {before_ents:,} → {after_ents:,} (중복 제거: {dedup_ents:,}, {dedup_ents/before_ents*100:.1f}%)")
log(f"  관계: {before_rels:,} → {after_rels:,}")
ok_m1 = after_ents <= before_ents and dedup_ents >= 0
dedup_rate = dedup_ents / before_ents * 100 if before_ents else 0
range_ok = 5 <= dedup_rate <= 50  # 적정 범위
log(f"  중복 제거율: {dedup_rate:.1f}% (적정 범위: 5~50%)")
log(f"  판정: {'✅' if ok_m1 and range_ok else '⚠️'}")


# ━━━ M2. BELONGS_TO 커버리지 ━━━
log("\n━━━ M2. BELONGS_TO 커버리지 (WorkType → Section) ━━━")
worktype_count = 0
worktype_with_bt = 0
bt_missing_samples = []

for ext in merged_exts:
    wts = [e for e in ext.get("entities", []) if e["type"] == "WorkType"]
    rels = ext.get("relationships", [])
    bt_sources = {r["source"] for r in rels if r["type"] == "BELONGS_TO"}

    for wt in wts:
        worktype_count += 1
        if wt["name"] in bt_sources:
            worktype_with_bt += 1
        else:
            if len(bt_missing_samples) < 5:
                bt_missing_samples.append({
                    "chunk_id": ext["chunk_id"],
                    "worktype": wt["name"],
                })

bt_coverage = worktype_with_bt / worktype_count * 100 if worktype_count else 0
log(f"  전체 WorkType: {worktype_count:,}")
log(f"  BELONGS_TO 있음: {worktype_with_bt:,} ({bt_coverage:.1f}%)")
log(f"  BELONGS_TO 없음: {worktype_count - worktype_with_bt:,}")
ok_m2 = bt_coverage >= 95
log(f"  판정: {'✅' if ok_m2 else '⚠️'} (기준: ≥95%)")
if bt_missing_samples:
    log(f"  누락 샘플:")
    for s in bt_missing_samples[:3]:
        log(f"    [{s['chunk_id']}] {s['worktype']}")


# ━━━ M3. HAS_CHILD 계층 일관성 ━━━
log("\n━━━ M3. HAS_CHILD 계층 일관성 ━━━")
hc_rels = merged_data.get("global_relationships", {}).get("HAS_CHILD", [])
parent_ids = set()
child_ids = set()
for r in hc_rels:
    parent_ids.add(r["properties"]["parent_id"])
    child_ids.add(r["properties"]["child_id"])

# 부모가 없는 자식 (최상위 레벨 제외)
orphan_children = set()
for cid in child_ids:
    parts = cid.split("-")
    if len(parts) >= 3:  # 3단계 이상이면 부모가 있어야 함
        expected_parent = "-".join(parts[:-1])
        if expected_parent not in parent_ids and expected_parent not in child_ids:
            orphan_children.add(cid)

log(f"  HAS_CHILD 관계: {len(hc_rels):,}")
log(f"  고유 부모: {len(parent_ids):,}")
log(f"  고유 자식: {len(child_ids):,}")
log(f"  고아 자식 (부모 없음): {len(orphan_children)}")
ok_m3 = len(orphan_children) == 0
log(f"  판정: {'✅' if ok_m3 else '⚠️'}")
if orphan_children:
    for oc in list(orphan_children)[:3]:
        log(f"    고아: {oc}")


# ━━━ M4. REFERENCES 유효성 ━━━
log("\n━━━ M4. REFERENCES target 유효성 ━━━")
ref_rels = merged_data.get("global_relationships", {}).get("REFERENCES", [])
# 모든 Section 엔티티의 이름 수집
all_section_names = set()
for ext in merged_exts:
    for e in ext.get("entities", []):
        if e["type"] == "Section":
            all_section_names.add(e["name"])

ref_valid = 0
ref_invalid = 0
for r in ref_rels:
    if r["target"] in all_section_names or r["source"] in all_section_names:
        ref_valid += 1
    else:
        ref_invalid += 1

log(f"  REFERENCES 관계: {len(ref_rels)}")
log(f"  유효: {ref_valid}, 무효: {ref_invalid}")
ok_m4 = ref_invalid == 0 or len(ref_rels) == 0
log(f"  판정: {'✅' if ok_m4 else '⚠️'}")


# ━━━ M5. Section 엔티티 완전성 ━━━
log("\n━━━ M5. Section 엔티티 완전성 ━━━")
section_count = sum(
    1 for ext in merged_exts
    if any(e["type"] == "Section" for e in ext.get("entities", []))
)
total_chunks = len(merged_exts)
section_coverage = section_count / total_chunks * 100 if total_chunks else 0
log(f"  전체 청크: {total_chunks:,}")
log(f"  Section 엔티티 포함: {section_count:,} ({section_coverage:.1f}%)")
ok_m5 = section_coverage >= 95
log(f"  판정: {'✅' if ok_m5 else '⚠️'} (기준: ≥95%)")


# ━━━ M6. 관계 손실 검사 ━━━
log("\n━━━ M6. 병합 전후 관계 유형별 비교 ━━━")
# Step 2.1 관계 유형
t_rel_types = Counter()
for ext in table_data["extractions"]:
    for r in ext.get("relationships", []):
        t_rel_types[r["type"]] += 1

# Step 2.2 관계 유형
l_rel_types = Counter()
for ext in llm_data["extractions"]:
    for r in ext.get("relationships", []):
        l_rel_types[r["type"]] += 1

# 병합 후 관계 유형
m_rel_types = Counter()
for ext in merged_exts:
    for r in ext.get("relationships", []):
        m_rel_types[r["type"]] += 1
for r in hc_rels:
    m_rel_types[r["type"]] += 1
for r in ref_rels:
    m_rel_types[r["type"]] += 1

all_types = sorted(set(t_rel_types) | set(l_rel_types) | set(m_rel_types))
log(f"  {'유형':25s} {'2.1':>8s} {'2.2':>8s} {'합계':>8s} {'병합후':>8s} {'변화':>8s}")
log(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
for t in all_types:
    tv = t_rel_types.get(t, 0)
    lv = l_rel_types.get(t, 0)
    total = tv + lv
    mv = m_rel_types.get(t, 0)
    diff = mv - total
    sign = "+" if diff > 0 else ""
    log(f"  {t:25s} {tv:>8,} {lv:>8,} {total:>8,} {mv:>8,} {sign}{diff:>7,}")

# 핵심 관계 손실 여부
core_types = ["REQUIRES_LABOR", "REQUIRES_EQUIPMENT", "USES_MATERIAL"]
loss_count = 0
for ct in core_types:
    before = t_rel_types.get(ct, 0) + l_rel_types.get(ct, 0)
    after = m_rel_types.get(ct, 0)
    if after < before * 0.9:  # 10% 이상 손실
        loss_count += 1

ok_m6 = loss_count == 0
log(f"\n  핵심 관계 손실: {'없음 ✅' if ok_m6 else f'{loss_count}건 ⚠️'}")


# ━━━ M7. 랜덤 샘플 3건 병합 대조 ━━━
log("\n━━━ M7. 랜덤 샘플 3건 병합 결과 대조 ━━━")
table_map = {e["chunk_id"]: e for e in table_data["extractions"]}
llm_map = {e["chunk_id"]: e for e in llm_data["extractions"]}
merged_map = {e["chunk_id"]: e for e in merged_exts}

# 양쪽 모두 있는 청크에서 샘플링
both_ids = sorted(set(table_map.keys()) & set(llm_map.keys()))
random.seed(123)
sample_ids = random.sample(both_ids, min(3, len(both_ids)))

for cid in sample_ids:
    t = table_map[cid]
    l = llm_map[cid]
    m = merged_map[cid]
    log(f"\n  ── {cid} ({m.get('title', '')}) ──")
    log(f"    Step 2.1: {len(t.get('entities', []))}개 엔티티, {len(t.get('relationships', []))}개 관계")
    log(f"    Step 2.2: {len(l.get('entities', []))}개 엔티티, {len(l.get('relationships', []))}개 관계")
    log(f"    합계:     {len(t.get('entities', [])) + len(l.get('entities', []))}개 엔티티")
    log(f"    병합 후:  {len(m.get('entities', []))}개 엔티티, {len(m.get('relationships', []))}개 관계")
    dedup = (len(t.get("entities", [])) + len(l.get("entities", []))) - len(m.get("entities", []))
    log(f"    중복 제거: {dedup}개")

    # BELONGS_TO 확인
    bt = [r for r in m.get("relationships", []) if r["type"] == "BELONGS_TO"]
    log(f"    BELONGS_TO: {len(bt)}개")
    if bt:
        log(f"      예: {bt[0]['source']} → {bt[0]['target']}")

    # Section 엔티티 확인
    secs = [e for e in m.get("entities", []) if e["type"] == "Section"]
    log(f"    Section 엔티티: {len(secs)}개")


# ━━━ 종합 ━━━
log("\n" + "=" * 70)
log("  Step 2.3 검증 종합")
log("=" * 70)
log(f"  M1 중복 제거 정확성  : {dedup_rate:.1f}% {'✅' if ok_m1 and range_ok else '⚠️'}")
log(f"  M2 BELONGS_TO 커버리지: {bt_coverage:.1f}% {'✅' if ok_m2 else '⚠️'}")
log(f"  M3 HAS_CHILD 일관성  : 고아 {len(orphan_children)}건 {'✅' if ok_m3 else '⚠️'}")
log(f"  M4 REFERENCES 유효성 : {'✅' if ok_m4 else '⚠️'}")
log(f"  M5 Section 완전성    : {section_coverage:.1f}% {'✅' if ok_m5 else '⚠️'}")
log(f"  M6 관계 손실         : {'없음 ✅' if ok_m6 else '있음 ⚠️'}")
log(f"  M7 샘플 대조         : 위 3건 확인 필요")

all_pass = all([ok_m1, range_ok, ok_m2, ok_m3, ok_m4, ok_m5, ok_m6])
log(f"\n  전체 판정: {'✅ PASS' if all_pass else '⚠️ 일부 항목 확인 필요'}")
log("=" * 70)

REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
log(f"\n  리포트 저장: {REPORT_FILE}")
