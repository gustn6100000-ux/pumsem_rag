# -*- coding: utf-8 -*-
"""전수 이슈 종합 분석 + 수정 대상 식별"""
import json, sys, re, unicodedata
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
    print(s)

# ═══════════════════════ 1. 가비지 이름 전체 식별 ═══════════════════════
p("=" * 70)
p("1. 가비지 엔티티 전수 식별")
p("=" * 70)

garbage_ids = set()
garbage_reasons = {}

for e in ents:
    name = e.get("name", "")
    etype = e["type"]
    eid = e.get("entity_id", "")

    # 1) 특수문자만 이름
    if etype in ("WorkType", "Equipment", "Material", "Labor"):
        if name in ("-", "\"", "→", ":", "", " ", "·"):
            garbage_ids.add(eid)
            garbage_reasons[eid] = f"특수문자이름: '{name}'"

        # 2) 숫자만 WorkType (1,2,3,4,5,8 등)
        if etype == "WorkType" and re.match(r"^\d+$", name):
            garbage_ids.add(eid)
            garbage_reasons[eid] = f"숫자만WorkType: '{name}'"

    # 3) LLM 환각 패턴: "note_" 접두사인데 Note가 아닌 다른 타입
    if name.startswith("note_") and etype != "Note":
        garbage_ids.add(eid)
        garbage_reasons[eid] = f"note_접두사가{etype}: '{name}'"

    # 4) 이름이 순수 숫자+단위만 (자재/장비로 부적합)
    if etype in ("Equipment", "Material") and re.match(r"^[\d.,]+\s*(mm|cm|m|kg|t|ton|kW)?$", name):
        garbage_ids.add(eid)
        garbage_reasons[eid] = f"숫자+단위만: '{name}'"

    # 5) ':' 시작하는 이름
    if name.startswith(":") or name.startswith("："):
        garbage_ids.add(eid)
        garbage_reasons[eid] = f"콜론시작: '{name}'"

p(f"\n  가비지 식별: {len(garbage_ids)}건")
reason_counts = Counter(r.split(":")[0] for r in garbage_reasons.values())
for r, c in reason_counts.most_common():
    p(f"    {r}: {c}")
for eid, reason in list(garbage_reasons.items())[:20]:
    p(f"    {eid}: {reason}")

# ═══════════════════════ 2. 비정상 단위 상세 ═══════════════════════
p("\n" + "=" * 70)
p("2. 비정상 단위 패턴 분석")
p("=" * 70)

unit_counter = Counter()
for r in all_rels:
    u = r.get("unit", "")
    if u:
        unit_counter[u] += 1

p(f"\n  유니크 단위: {len(unit_counter)}")
p(f"  TOP 30:")
for u, c in unit_counter.most_common(30):
    p(f"    '{u}': {c}")

# 비정상 단위 (유효 목록 외)
valid_units = {"인", "대", "개", "조", "식", "본", "매", "일", "공", "톤",
               "m", "m²", "m³", "km", "cm", "mm", "ha",
               "kg", "t", "ton", "L", "ℓ",
               "kW", "HP", "kVA",
               "EA", "set", "hr", "시간", "M/D",
               "m2", "m3", "㎡", "㎥", "㎜", "㎝", "㎞",
               "회", "주", "소", "%", "℃", "장", "건",
               "부", "권", "도", "쌍", "벌", "세트", "묶음",
               "가닥", "병", "통", "포", "루", "면", "쪽", "점",
               "호", "번", "량", "편", "자루"}

weird_units = {}
for u, c in unit_counter.items():
    if u not in valid_units:
        weird_units[u] = c

p(f"\n  비정상 단위: {len(weird_units)}종, 총 {sum(weird_units.values())}건")
p(f"  TOP 20:")
for u, c in sorted(weird_units.items(), key=lambda x: -x[1])[:20]:
    p(f"    '{u}': {c}")

# ═══════════════════════ 3. 63 전손실 청크 원인 ═══════════════════════
p("\n" + "=" * 70)
p("3. 관계 전손실 63청크 원인 분석")
p("=" * 70)

merged = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json",
    encoding="utf-8"
).read())

merged_ext_map = {ext["chunk_id"]: ext for ext in merged["extractions"]}
norm_ext_map = {ext["chunk_id"]: ext for ext in norm["extractions"]}

lost_chunks = []
for cid, ext in merged_ext_map.items():
    orig_rels = len(ext.get("relationships", []))
    norm_rels = len(norm_ext_map.get(cid, {}).get("relationships", []))
    if orig_rels > 0 and norm_rels == 0:
        lost_chunks.append(cid)

p(f"\n  전손실 청크: {len(lost_chunks)}개")

# 각 청크의 원본 관계 분석
loss_rel_types = Counter()
loss_reasons = Counter()
for cid in lost_chunks[:20]:
    orig = merged_ext_map[cid]
    orig_rels = orig.get("relationships", [])
    p(f"\n  [{cid}] 원본 관계 {len(orig_rels)}건:")
    for r in orig_rels[:5]:
        rt = r.get("type", "")
        st = r.get("source_type", "")
        tt = r.get("target_type", "")
        src = r.get("source", "")[:25]
        tgt = r.get("target", "")[:25]
        loss_rel_types[rt] += 1
        p(f"    {st}:'{src}' -[{rt}]-> {tt}:'{tgt}'")

p(f"\n  손실 관계 유형:")
for t, c in loss_rel_types.most_common():
    p(f"    {t}: {c}")

# ═══════════════════════ 4. 고아 엔티티 상세 ═══════════════════════
p("\n" + "=" * 70)
p("4. 고아 엔티티 심층 분석")
p("=" * 70)

ref_ids = set()
for r in all_rels:
    ref_ids.add(r.get("source_entity_id", ""))
    ref_ids.add(r.get("target_entity_id", ""))
ref_ids.discard("")

orphans = [e for e in ents if e["entity_id"] not in ref_ids]
orphan_types = Counter(e["type"] for e in orphans)

# 고아 중 가비지와 겹치는 건
orphan_garbage = [e for e in orphans if e["entity_id"] in garbage_ids]
p(f"  고아: {len(orphans)}건")
p(f"  고아 중 가비지: {len(orphan_garbage)}건")
p(f"  고아 타입별: {dict(orphan_types)}")

# 고아 WorkType 703건 — 왜 관계가 없는지?
orphan_wt = [e for e in orphans if e["type"] == "WorkType"]
# spec 유무
has_spec = sum(1 for e in orphan_wt if e.get("spec"))
p(f"\n  고아 WorkType {len(orphan_wt)}건: spec 있음={has_spec}, 없음={len(orphan_wt)-has_spec}")
# source_method
orphan_methods = Counter(e.get("source_method", "?") for e in orphan_wt)
p(f"  source_method: {dict(orphan_methods)}")

# ═══════════════════════ 5. 관계 로직 전수 검증 ═══════════════════════
p("\n" + "=" * 70)
p("5. 관계 로직 전수 검증")
p("=" * 70)

# self-referencing (source_entity_id == target_entity_id)
self_refs = [r for r in all_rels if r.get("source_entity_id") == r.get("target_entity_id") and r.get("source_entity_id")]
p(f"  자기참조: {len(self_refs)}건")
if self_refs:
    for r in self_refs[:5]:
        p(f"    {r.get('source_entity_id')} -[{r.get('type')}]-> self")

# HAS_CHILD 순환 참조 (A→B, B→A)
haschild_pairs = set()
haschild_cycles = []
for r in all_rels:
    if r.get("type") == "HAS_CHILD":
        pair = (r.get("source_entity_id"), r.get("target_entity_id"))
        reverse = (r.get("target_entity_id"), r.get("source_entity_id"))
        if reverse in haschild_pairs:
            haschild_cycles.append(pair)
        haschild_pairs.add(pair)
p(f"  HAS_CHILD 순환: {len(haschild_cycles)}건")

# BELONGS_TO: WorkType이 여러 Section에 속하는 경우
belongs = defaultdict(set)
for r in all_rels:
    if r.get("type") == "BELONGS_TO":
        belongs[r.get("source_entity_id")].add(r.get("target_entity_id"))
multi_belongs = {k: v for k, v in belongs.items() if len(v) > 1}
p(f"  BELONGS_TO 다중 소속: {len(multi_belongs)}건 (WorkType → 2+ Section)")

# 어떤 엔티티가 source이면서 target인 관계 (이상은 아니지만 통계)
source_ids = Counter(r.get("source_entity_id") for r in all_rels)
target_ids = Counter(r.get("target_entity_id") for r in all_rels)
both = set(source_ids) & set(target_ids) - {""}
p(f"  source이면서 target: {len(both)}건")

# ═══════════════════════ 6. NFKC 수정 대상 ═══════════════════════
p("\n" + "=" * 70)
p("6. NFKC 정규화 수정 영향도")
p("=" * 70)
nfkc_targets = []
for e in ents:
    name = e.get("name", "")
    nfkc = unicodedata.normalize("NFKC", name)
    if nfkc != name:
        nfkc_targets.append((e["entity_id"], name, nfkc))

p(f"  수정 대상: {len(nfkc_targets)}건")
# 이 엔티티가 관계에서 참조되는지
nfkc_ids = {t[0] for t in nfkc_targets}
nfkc_in_rels = sum(1 for r in all_rels 
                   if r.get("source_entity_id") in nfkc_ids 
                   or r.get("target_entity_id") in nfkc_ids)
p(f"  관계 참조: {nfkc_in_rels}건")
p(f"  → NFKC 적용 시 관계의 source/target 이름도 함께 갱신 필요")

for eid, orig, converted in nfkc_targets:
    p(f"    {eid}: '{orig[:50]}' → '{converted[:50]}'")

# 종합
report = "\n".join(out)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\full_analysis.txt", 
     "w", encoding="utf-8").write(report)
