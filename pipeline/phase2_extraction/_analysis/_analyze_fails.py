# -*- coding: utf-8 -*-
"""4건 FAIL 원인 상세 분석"""
import json, sys
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())

ents = norm["entities"]
all_rels = []
for ext in norm.get("extractions", []):
    for r in ext.get("relationships", []):
        all_rels.append(r)
for rtype, rels in norm.get("global_relationships", {}).items():
    all_rels.extend(rels)

out = []
def p(s=""):
    out.append(s)

# ═══════════════════════════════════════
#  F1: N2 방향 오류 5건 (Section→Labor)
# ═══════════════════════════════════════
p("=" * 60)
p("F1: N2 방향 오류 (Section→Labor)")
p("=" * 60)
errs = [r for r in all_rels 
        if r.get("type") == "REQUIRES_LABOR" 
        and r.get("source_type") == "Section"]
p(f"\n총: {len(errs)}건")
for r in errs[:5]:
    p(f"  src={r.get('source')}, tgt={r.get('target')}, chunk={r.get('source_chunk_id','')}")
# 이것은 Phase C fallback에서 Section이 source가 되었는데
# VALID_DIRECTIONS에서 REQUIRES_LABOR의 valid_sources = {WorkType}만 허용
# → fallback 시 REQUIRES_LABOR에는 Section 사용 안 하도록 수정 필요
# 또는 VALID_DIRECTIONS 검증에 Section 추가

# ═══════════════════════════════════════
#  F2: N6 중복 2987건
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("F2: N6 중복 관계 2987건")
p("=" * 60)
# 중복 키 분석
rel_keys = defaultdict(list)
for i, r in enumerate(all_rels):
    key = (
        r.get("source_entity_id", ""),
        r.get("target_entity_id", ""),
        r.get("type", ""),
        r.get("quantity"),
        r.get("unit", ""),
        r.get("per_unit", ""),
    )
    rel_keys[key].append(i)

dups = {k: v for k, v in rel_keys.items() if len(v) > 1}

# 중복이 발생하는 관계 유형
dup_by_type = Counter()
for k, indices in dups.items():
    dup_by_type[k[2]] += len(indices) - 1

p(f"\n중복 관계 유형별:")
for t, c in dup_by_type.most_common():
    p(f"  {t}: {c}")

# 원인: 서로 다른 청크에서 같은 entity_id 쌍이 동일 관계를 갖는 경우
# 이는 Phase E의 dedup이 청크 내에서만 작동하기 때문
# → Phase E 이후 글로벌 dedup 필요
p(f"\n원인: Phase E의 dedup이 청크 내에서만 작동 → 글로벌 dedup 미적용")

# ═══════════════════════════════════════
#  F3: X2 고아 엔티티 3991건
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("F3: X2 고아 엔티티 3991건")
p("=" * 60)
ref_ids = set()
for r in all_rels:
    ref_ids.add(r.get("source_entity_id", ""))
    ref_ids.add(r.get("target_entity_id", ""))
ref_ids.discard("")

orphans = [e for e in ents if e["entity_id"] not in ref_ids]
orphan_types = Counter(e["type"] for e in orphans)
p(f"\n참조 ID: {len(ref_ids):,}")
p(f"총 엔티티: {len(ents):,}")
p(f"고아: {len(orphans):,}")
p(f"타입별: {dict(orphan_types)}")

# 고아의 source_method 분포
orphan_methods = Counter(e.get("source_method", "unknown") for e in orphans)
p(f"source_method: {dict(orphan_methods)}")

# 원인: 중복 제거 후 관계의 source/target 이름이 갱신되었지만
# 동일 이름의 다른 spec 엔티티가 존재하면 관계가 원래 spec 없는 엔티티를 가리킴
# → 고아는 spec이 다른 변종이거나, 관계가 없는 단독 엔티티

# 고아 중 spec 있는 것/없는 것 비율
has_spec = sum(1 for e in orphans if e.get("spec"))
no_spec = len(orphans) - has_spec
p(f"\n고아 중 spec 있음: {has_spec}, 없음: {no_spec}")

# 고아 Equipment 샘플
orphan_eq = [e for e in orphans if e["type"] == "Equipment"]
p(f"\n고아 Equipment 샘플:")
for e in orphan_eq[:5]:
    p(f"  name='{e['name'][:30]}' spec='{e.get('spec', '')[:30]}' id={e['entity_id']}")
    
# 고아 Note 샘플
orphan_note = [e for e in orphans if e["type"] == "Note"]
p(f"\n고아 Note 샘플:")
for e in orphan_note[:5]:
    p(f"  name='{e['name'][:50]}' id={e['entity_id']}")

# 고아 WorkType 샘플
orphan_wt = [e for e in orphans if e["type"] == "WorkType"]
p(f"\n고아 WorkType 샘플:")
for e in orphan_wt[:5]:
    p(f"  name='{e['name'][:40]}' spec='{e.get('spec', '')[:30]}' id={e['entity_id']}")

# ═══════════════════════════════════════
#  F4: X3 보통인부 22건
# ═══════════════════════════════════════
p("\n" + "=" * 60)
p("F4: X3 보통인부 22건")
p("=" * 60)
labors = [e for e in ents if e["type"] == "Labor" and "보통인부" in e.get("name", "")]
p(f"\n'보통인부' 포함 Labor: {len(labors)}건")
for e in labors:
    p(f"  name='{e['name']}' norm='{e.get('normalized_name')}' spec='{e.get('spec', '')}' chunks={len(e.get('source_chunk_ids', []))}")

# 원인: "보통인부"를 normalized_name으로 가지지만 name이 다른 변종이 존재
# normalized_name이 다를 수 있음
norm_names = Counter(e.get("normalized_name", "") for e in labors)
p(f"\nnormalized_name 분포: {dict(norm_names)}")

report = "\n".join(out)
print(report)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\fail_analysis.txt", 
     "w", encoding="utf-8").write(report)
