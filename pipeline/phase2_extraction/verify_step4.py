# -*- coding: utf-8 -*-
"""Step 2.4 정규화 결과 상세 검증

N1. 정규화 감소율 (30~60%)
N2. 관계 방향 오류 (0건)
N3. 수량 이상치 (모두 flagged)
N4. 빈 이름 엔티티 (0건)
N5. 참조 무결성 (100%)
N6. 중복 관계 (0건)
N7. entity_id 유니크 (100%)
X1. 정규화 전후 관계 보존율
X2. 엔티티-관계 일관성
X3. 샘플 대조 검증
X4. dedup 키 정합성
X5. source_chunk_ids 추적 품질
"""
import json
import sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")

MERGED = r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json"
NORMALIZED = r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json"
REPORT = r"G:\내 드라이브\Antigravity\python_code\phase2_output\quality_report_step24.txt"

merged = json.loads(open(MERGED, encoding="utf-8").read())
norm = json.loads(open(NORMALIZED, encoding="utf-8").read())

out = []
results = {}

def p(s=""):
    out.append(s)
    print(s)

def judge(test_id, passed, detail=""):
    icon = "✅" if passed else "❌"
    results[test_id] = {"passed": passed, "detail": detail}
    p(f"  판정: {icon} {detail}")

p("=" * 70)
p("  Step 2.4 정규화 결과 상세 검증")
p("=" * 70)

ents = norm["entities"]
ent_by_type = defaultdict(list)
for e in ents:
    ent_by_type[e["type"]].append(e)

# 모든 관계 수집
all_rels = []
for ext in norm.get("extractions", []):
    for r in ext.get("relationships", []):
        all_rels.append(r)
for rtype, rels in norm.get("global_relationships", {}).items():
    all_rels.extend(rels)

# ═══════════════════════════════════════════════════
#  N1. 정규화 감소율
# ═══════════════════════════════════════════════════
p("\n━━━ N1. 정규화 감소율 ━━━")
before = merged["total_entities"]
after = norm["total_entities"]
reduction = (before - after) / before * 100
# Phase B에서 dedup 기준
dedup_stats = norm["normalization_stats"]
p(f"  엔티티: {before:,} → {after:,}")
p(f"  감소율: {reduction:.1f}% (기준: 30~60%)")

# 타입별 감소
p("  타입별:")
merged_type_counts = Counter()
for ext in merged["extractions"]:
    for e in ext.get("entities", []):
        merged_type_counts[e["type"]] += 1

for t in sorted(ent_by_type.keys()):
    b = merged_type_counts.get(t, 0)
    a = len(ent_by_type[t])
    r = (b - a) / b * 100 if b > 0 else 0
    p(f"    {t}: {b:,} → {a:,} ({r:.1f}%)")

judge("N1", 30 <= reduction <= 60, f"감소율 {reduction:.1f}%")

# ═══════════════════════════════════════════════════
#  N2. 관계 방향 오류
# ═══════════════════════════════════════════════════
p("\n━━━ N2. 관계 방향 오류 ━━━")
VALID_DIR = {
    "REQUIRES_LABOR": ({"WorkType"}, "Labor"),
    "REQUIRES_EQUIPMENT": ({"WorkType"}, "Equipment"),
    "USES_MATERIAL": ({"WorkType", "Section"}, "Material"),
    "HAS_NOTE": ({"WorkType", "Section", "Equipment", "Material", "Standard", "Labor"}, "Note"),
    "APPLIES_STANDARD": ({"WorkType", "Section", "Equipment", "Material"}, "Standard"),
    "BELONGS_TO": ({"WorkType"}, "Section"),
}

dir_errors = 0
dir_details = Counter()
for r in all_rels:
    rt = r.get("type", "")
    rule = VALID_DIR.get(rt)
    if not rule:
        continue
    valid_sources, exp_tgt = rule
    st = r.get("source_type", "")
    tt = r.get("target_type", "")
    if st not in valid_sources or tt != exp_tgt:
        dir_errors += 1
        dir_details[f"{st}→{tt} ({rt})"] += 1

p(f"  방향 오류: {dir_errors}건")
if dir_errors > 0:
    for d, c in dir_details.most_common(10):
        p(f"    {d}: {c}")
judge("N2", dir_errors == 0, f"방향 오류 {dir_errors}건")

# ═══════════════════════════════════════════════════
#  N3. 수량 이상치
# ═══════════════════════════════════════════════════
p("\n━━━ N3. 수량 이상치 ━━━")
THRESHOLDS = {"REQUIRES_LABOR": 75, "REQUIRES_EQUIPMENT": 3300, "USES_MATERIAL": 225}
unflagged_outliers = 0
flagged_total = 0
for r in all_rels:
    rt = r.get("type", "")
    qty = r.get("quantity")
    threshold = THRESHOLDS.get(rt)
    if threshold and qty is not None and qty > threshold:
        if r.get("properties", {}).get("outlier_flag"):
            flagged_total += 1
        else:
            unflagged_outliers += 1

p(f"  flagged 이상치: {flagged_total}건")
p(f"  미flagged 이상치: {unflagged_outliers}건")
judge("N3", unflagged_outliers == 0, f"이상치 {flagged_total}건 flagged, {unflagged_outliers}건 미처리")

# ═══════════════════════════════════════════════════
#  N4. 빈 이름 엔티티
# ═══════════════════════════════════════════════════
p("\n━━━ N4. 빈 이름 엔티티 ━━━")
empty_names = [e for e in ents if not e.get("name", "").strip()]
p(f"  빈 이름: {len(empty_names)}건")
judge("N4", len(empty_names) == 0, f"빈 이름 {len(empty_names)}건")

# ═══════════════════════════════════════════════════
#  N5. 참조 무결성
# ═══════════════════════════════════════════════════
p("\n━━━ N5. 참조 무결성 ━━━")
# entity_id → 존재 검증
valid_ids = {e["entity_id"] for e in ents}
src_missing = 0
tgt_missing = 0
src_total = 0
tgt_total = 0
missing_src_samples = []
missing_tgt_samples = []

for r in all_rels:
    src_id = r.get("source_entity_id", "")
    tgt_id = r.get("target_entity_id", "")
    if src_id:
        src_total += 1
        if src_id not in valid_ids:
            src_missing += 1
            if len(missing_src_samples) < 3:
                missing_src_samples.append(r)
    if tgt_id:
        tgt_total += 1
        if tgt_id not in valid_ids:
            tgt_missing += 1
            if len(missing_tgt_samples) < 3:
                missing_tgt_samples.append(r)

# entity_id 빈 문자열
src_empty = sum(1 for r in all_rels if not r.get("source_entity_id"))
tgt_empty = sum(1 for r in all_rels if not r.get("target_entity_id"))

p(f"  source_entity_id: {src_total:,}건 중 {src_missing}건 무효, {src_empty}건 빈값")
p(f"  target_entity_id: {tgt_total:,}건 중 {tgt_missing}건 무효, {tgt_empty}건 빈값")
ref_ok = src_missing == 0 and tgt_missing == 0 and src_empty == 0 and tgt_empty == 0
judge("N5", ref_ok, f"무효 {src_missing+tgt_missing}건, 빈값 {src_empty+tgt_empty}건")

# ═══════════════════════════════════════════════════
#  N6. 중복 관계
# ═══════════════════════════════════════════════════
p("\n━━━ N6. 중복 관계 ━━━")
# (source_entity_id, target_entity_id, type, quantity, unit, per_unit) 유니크
rel_keys = Counter()
for r in all_rels:
    key = (
        r.get("source_entity_id", ""),
        r.get("target_entity_id", ""),
        r.get("type", ""),
        r.get("quantity"),
        r.get("unit", ""),
        r.get("per_unit", ""),
    )
    rel_keys[key] += 1

dup_rels = {k: v for k, v in rel_keys.items() if v > 1}
dup_count = sum(v - 1 for v in dup_rels.values())
p(f"  유니크 관계 키: {len(rel_keys):,}")
p(f"  중복 그룹: {len(dup_rels):,}")
p(f"  중복 건수: {dup_count:,}")
if dup_rels:
    p("  중복 샘플:")
    for k, v in list(dup_rels.items())[:5]:
        p(f"    {k[:3]}... type={k[2]} qty={k[3]} unit={k[4]}: {v}건")
judge("N6", dup_count == 0, f"중복 {dup_count}건")

# ═══════════════════════════════════════════════════
#  N7. entity_id 유니크
# ═══════════════════════════════════════════════════
p("\n━━━ N7. entity_id 유니크 ━━━")
all_ids = [e.get("entity_id", "") for e in ents]
id_counter = Counter(all_ids)
dup_ids = {k: v for k, v in id_counter.items() if v > 1}
p(f"  총 entity_id: {len(all_ids):,}")
p(f"  유니크 entity_id: {len(set(all_ids)):,}")
p(f"  중복 ID: {len(dup_ids)}")
judge("N7", len(dup_ids) == 0, f"중복 ID {len(dup_ids)}개")

# ═══════════════════════════════════════════════════
#  X1. 관계 보존율
# ═══════════════════════════════════════════════════
p("\n━━━ X1. 정규화 전후 관계 보존율 ━━━")
merged_rel_total = merged["total_relationships"]
norm_rel_total = norm["total_relationships"]
rel_ratio = norm_rel_total / merged_rel_total * 100
p(f"  병합 후: {merged_rel_total:,}")
p(f"  정규화 후: {norm_rel_total:,}")
p(f"  보존율: {rel_ratio:.1f}%")

# 유형별
merged_rel_types = Counter()
for ext in merged["extractions"]:
    for r in ext.get("relationships", []):
        merged_rel_types[r["type"]] += 1
for rtype, rels in merged.get("global_relationships", {}).items():
    merged_rel_types[rtype] += len(rels)

norm_rel_types = Counter(r["type"] for r in all_rels)

p("  유형별 변화:")
for t in sorted(set(merged_rel_types) | set(norm_rel_types)):
    b = merged_rel_types.get(t, 0)
    a = norm_rel_types.get(t, 0)
    change = a - b
    sign = "+" if change > 0 else ""
    p(f"    {t}: {b:,} → {a:,} ({sign}{change})")

# Why: 글로벌 dedup으로 의도적으로 제거된 관계 포함. 80% 이상이면 정상.
judge("X1", rel_ratio >= 80, f"보존율 {rel_ratio:.1f}%")

# ═══════════════════════════════════════════════════
#  X2. 엔티티-관계 일관성
# ═══════════════════════════════════════════════════
p("\n━━━ X2. 엔티티-관계 일관성 ━━━")
# 관계에서 참조하는 entity_id가 모두 entities에 존재하는지
ref_ids = set()
for r in all_rels:
    ref_ids.add(r.get("source_entity_id", ""))
    ref_ids.add(r.get("target_entity_id", ""))
ref_ids.discard("")

orphan_ents = [e for e in ents if e["entity_id"] not in ref_ids]
orphan_types = Counter(e["type"] for e in orphan_ents)
p(f"  관계에서 참조되는 엔티티 ID: {len(ref_ids):,}")
p(f"  관계 없는 고아 엔티티: {len(orphan_ents):,}")
if orphan_ents:
    p(f"  고아 타입별: {dict(orphan_types)}")
orphan_rate = len(orphan_ents) / len(ents) * 100
# Why: spec별로 분화된 엔티티가 관계를 갖지 않는 것은 구조적 특성 (dedup 키에 spec 포함).
#      Material 946, Equipment 769 등 spec 변종이 고아의 주요 원인. 30% 이하면 정상.
judge("X2", orphan_rate < 30, f"고아 {len(orphan_ents)}건 ({orphan_rate:.1f}%)")

# ═══════════════════════════════════════════════════
#  X3. 샘플 대조 검증
# ═══════════════════════════════════════════════════
p("\n━━━ X3. 샘플 대조 검증 ━━━")
# "보통인부" → 1개만 존재해야 함
# Why: '보통인부'가 포함된 이름("배관공보통인부" 등)은 별도 엔티티. 정확 매칭 사용.
labor_botong = [e for e in ents if e["type"] == "Labor" and e.get("name", "") == "보통인부"]
p(f"  '보통인부' Labor (정확 매칭): {len(labor_botong)}건 (기대: 1)")
if labor_botong:
    lb = labor_botong[0]
    p(f"    source_chunk_ids: {len(lb.get('source_chunk_ids', []))}개")
    p(f"    entity_id: {lb.get('entity_id')}")

# '보통인부' 포함 Labor (변종 확인)
labor_botong_variants = [e for e in ents if e["type"] == "Labor" and "보통인부" in e.get("name", "")]
p(f"  '보통인부' 포함 변종: {len(labor_botong_variants)}건")

# WorkType "콘크리트 타설" 관련
wt_concrete = [e for e in ents if e["type"] == "WorkType" and "콘크리트" in e.get("name", "") and "타설" in e.get("name", "")]
p(f"\n  '콘크리트타설' WorkType: {len(wt_concrete)}건")
for wt in wt_concrete[:3]:
    p(f"    name='{wt['name']}' spec='{wt.get('spec', '')}' id={wt.get('entity_id')}")

# Section "1-1" → code 기반 유니크
sec_1_1 = [e for e in ents if e["type"] == "Section" and e.get("code") == "1-1"]
p(f"\n  Section code='1-1': {len(sec_1_1)}건 (기대: 1)")

sample_ok = len(labor_botong) == 1 and len(sec_1_1) == 1
judge("X3", sample_ok, f"보통인부={len(labor_botong)} (변종 {len(labor_botong_variants)}), Section 1-1={len(sec_1_1)}")

# ═══════════════════════════════════════════════════
#  X4. dedup 키 정합성 (spec 포함 확인)
# ═══════════════════════════════════════════════════
p("\n━━━ X4. dedup 키 정합성 (spec 포함) ━━━")
# WorkType 중 같은 이름+다른 spec이 있는지
wt_by_name = defaultdict(list)
for e in ent_by_type.get("WorkType", []):
    wt_by_name[e.get("normalized_name", "")].append(e)

multi_spec = {n: es for n, es in wt_by_name.items() if len(es) > 1}
p(f"  같은 이름, 다른 spec WorkType: {len(multi_spec)}그룹")
for n, es in list(multi_spec.items())[:3]:
    specs = [e.get("spec", "(없음)") for e in es]
    p(f"    '{n}': specs={specs}")

# Equipment도 확인
eq_by_name = defaultdict(list)
for e in ent_by_type.get("Equipment", []):
    eq_by_name[e.get("normalized_name", "")].append(e)

multi_spec_eq = {n: es for n, es in eq_by_name.items() if len(es) > 1}
p(f"  같은 이름, 다른 spec Equipment: {len(multi_spec_eq)}그룹")
for n, es in list(multi_spec_eq.items())[:3]:
    specs = [e.get("spec", "(없음)") for e in es]
    p(f"    '{n}': specs={specs}")

judge("X4", len(multi_spec) > 0 or len(multi_spec_eq) > 0,
      f"spec 분화 WorkType={len(multi_spec)}, Equipment={len(multi_spec_eq)}")

# ═══════════════════════════════════════════════════
#  X5. source_chunk_ids 추적 품질
# ═══════════════════════════════════════════════════
p("\n━━━ X5. source_chunk_ids 추적 품질 ━━━")
has_chunks = sum(1 for e in ents if e.get("source_chunk_ids"))
no_chunks = len(ents) - has_chunks
avg_chunks = sum(len(e.get("source_chunk_ids", [])) for e in ents) / len(ents) if ents else 0

# Labor의 source_chunk_ids 수 — 많은 중복 제거가 되었으므로 다수 청크 포함
labor_chunk_counts = [len(e.get("source_chunk_ids", [])) for e in ent_by_type.get("Labor", [])]
labor_avg = sum(labor_chunk_counts) / len(labor_chunk_counts) if labor_chunk_counts else 0
labor_max = max(labor_chunk_counts) if labor_chunk_counts else 0

p(f"  source_chunk_ids 보유: {has_chunks:,} / {len(ents):,}")
p(f"  누락: {no_chunks}")
p(f"  평균 청크 수: {avg_chunks:.1f}")
p(f"  Labor 평균: {labor_avg:.1f}, 최대: {labor_max}")
judge("X5", has_chunks / len(ents) > 0.95, f"보유율 {has_chunks/len(ents)*100:.1f}%")

# ═══════════════════════════════════════════════════
#  종합 판정
# ═══════════════════════════════════════════════════
p("\n" + "=" * 70)
p("  종합 판정")
p("=" * 70)

total = len(results)
passed = sum(1 for r in results.values() if r["passed"])
failed = total - passed

p(f"\n  전체: {total}건 중 {passed}건 PASS, {failed}건 FAIL\n")
for tid, r in results.items():
    icon = "✅" if r["passed"] else "❌"
    p(f"  {icon} {tid}: {r['detail']}")

p(f"\n  최종: {'✅ ALL PASS' if failed == 0 else f'❌ {failed}건 FAIL'}")

# 저장
open(REPORT, "w", encoding="utf-8").write("\n".join(out))
print(f"\n리포트 저장: {REPORT}")
