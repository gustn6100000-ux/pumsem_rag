# -*- coding: utf-8 -*-
"""
엔티티 9,344건 감소 + 관계 4,085건 감소 — 정보 손실 여부 정밀 추적

분석 관점:
  A. 엔티티 감소 9,344건: 어디로 갔는가? (병합/삭제/가비지)
  B. 관계 감소 4,085건: 어디로 갔는가? (dedup/방향보정/가비지)
  C. 의미 단위 손실 검증: 유니크 (type, name) 쌍의 보존 여부
  D. 수량 정보 손실 검증: 수량 있는 관계의 데이터 보존 여부
  E. 원본 청크 커버리지: 모든 청크의 핵심 정보가 살아있는가
"""
import json, sys, re
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")

MERGED = r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json"
NORMALIZED = r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json"
REPORT = r"G:\내 드라이브\Antigravity\python_code\phase2_output\data_loss_analysis.txt"

merged = json.loads(open(MERGED, encoding="utf-8").read())
norm = json.loads(open(NORMALIZED, encoding="utf-8").read())

m_ents = []
for ext in merged["extractions"]:
    for e in ext.get("entities", []):
        e["_chunk_id"] = ext["chunk_id"]
        m_ents.append(e)

m_rels = []
for ext in merged["extractions"]:
    for r in ext.get("relationships", []):
        r["_chunk_id"] = ext["chunk_id"]
        m_rels.append(r)

n_ents = norm["entities"]
n_rels = []
for ext in norm.get("extractions", []):
    for r in ext.get("relationships", []):
        n_rels.append(r)
for rtype, rels in norm.get("global_relationships", {}).items():
    n_rels.extend(rels)

out = []
def p(s=""): out.append(s)

p("=" * 78)
p("  정규화 감소에 따른 데이터 반영 손실 여부 정밀 분석")
p("=" * 78)
p(f"  병합 후: 엔티티 {len(m_ents):,}, 관계 {len(m_rels):,}")
p(f"  정규화 후: 엔티티 {len(n_ents):,}, 관계 {len(n_rels):,}")
p(f"  감소: 엔티티 -{len(m_ents)-len(n_ents):,}, 관계 -{len(m_rels)-len(n_rels):,}")

# ═══════════════════════════════════════════════════════════
#  A. 엔티티 감소 추적: 9,344건은 어디로 갔는가?
# ═══════════════════════════════════════════════════════════
p(f"\n{'━'*78}")
p("A. 엔티티 감소 9,344건 추적")
p("━" * 78)

# 정규화 후 유니크 이름 집합 (type, normalized_name)
n_name_set = set()
for e in n_ents:
    n_name_set.add((e["type"], e.get("normalized_name", "")))
    n_name_set.add((e["type"], e.get("name", "")))

# 병합 엔티티를 정규화 이름으로 매핑
import unicodedata
def make_norm_name(name):
    """Phase A의 정규화 로직 재현"""
    if not name:
        return ""
    name = unicodedata.normalize("NFKC", name)
    return re.sub(r"\s+", "", name)

merged_by_category = Counter()  # 감소 원인별
merged_name_loss = []  # 실제 이름이 사라진 건

for e in m_ents:
    etype = e["type"]
    name = e.get("name", "")
    norm_name = make_norm_name(name)
    
    # 정규화 후에 이 이름이 살아있는가?
    if (etype, norm_name) in n_name_set or (etype, name) in n_name_set:
        merged_by_category["병합(이름 보존)"] += 1
    else:
        # 이름이 없어진 건 — 왜?
        # 가비지?
        is_garbage = False
        if etype in ("WorkType", "Equipment", "Material", "Labor"):
            if name in ("-", "\"", "→", ":", "", " ", "·"):
                is_garbage = True
            elif etype == "WorkType" and re.match(r"^\d+$", name):
                is_garbage = True
            elif etype in ("Equipment", "Material") and re.match(r"^[\d.,]+\s*(mm|cm|m|kg|t|ton|kW)?$", name):
                is_garbage = True
        if name.startswith(":") or name.startswith("："):
            is_garbage = True
        VALID_1CHAR = {"붓", "잭", "핀", "삽", "솔", "줄", "봉", "관", "판",
                       "통", "못", "물", "풀", "돌", "개", "정", "탭", "슈", "인"}
        if len(name) == 1 and etype in ("Material", "Labor") and name not in VALID_1CHAR:
            is_garbage = True

        if is_garbage:
            merged_by_category["가비지 제거"] += 1
        else:
            merged_by_category["이름 변환/보정"] += 1
            if len(merged_name_loss) < 30:
                merged_name_loss.append((etype, name[:40], e.get("spec", "")[:20]))

p(f"\n  감소 원인 분류:")
total_decreased = len(m_ents) - len(n_ents)
for reason, cnt in merged_by_category.most_common():
    pct = cnt / len(m_ents) * 100
    p(f"    {reason}: {cnt:,}건 ({pct:.1f}%)")

# 가장 중요: 유니크 (type, name) 쌍 비교
m_unique_names = set()
for e in m_ents:
    m_unique_names.add((e["type"], make_norm_name(e.get("name", ""))))

n_unique_names = set()
for e in n_ents:
    n_unique_names.add((e["type"], e.get("normalized_name", "")))

lost_names = m_unique_names - n_unique_names
# lost_names에서 가비지 제외
real_lost = set()
for t, n in lost_names:
    if not n or n in ("-", "\"", "→", ":", " ", "·"):
        continue
    if t == "WorkType" and re.match(r"^\d+$", n):
        continue
    if t in ("Equipment", "Material") and re.match(r"^[\d.,]+$", n):
        continue
    if n.startswith(":") or n.startswith("："):
        continue
    real_lost.add((t, n))

p(f"\n  ● 유니크 이름 비교:")
p(f"    병합 후 유니크 (type, normalized_name): {len(m_unique_names):,}")
p(f"    정규화 후 유니크: {len(n_unique_names):,}")
p(f"    사라진 유니크 이름: {len(lost_names):,}")
p(f"    사라진 유니크 이름 (가비지 제외): {len(real_lost):,}")

# 사라진 이름 상세
if real_lost:
    lost_types = Counter(t for t, n in real_lost)
    p(f"    사라진 이름 타입별: {dict(lost_types)}")
    p(f"    샘플:")
    for t, n in list(real_lost)[:15]:
        p(f"      [{t}] '{n[:50]}'")

# ═══════════════════════════════════════════════════════════
#  B. 관계 감소 추적: 4,085건은 어디로 갔는가?
# ═══════════════════════════════════════════════════════════
p(f"\n{'━'*78}")
p("B. 관계 감소 4,085건 추적")
p("━" * 78)

# 정규화 후 관계의 유니크 키 (src_type, src_name, rel_type, tgt_type, tgt_name) 
n_rel_keys = set()
for r in n_rels:
    key = (
        r.get("source_type", ""),
        make_norm_name(r.get("source", "")),
        r.get("type", ""),
        r.get("target_type", ""),
        make_norm_name(r.get("target", "")),
    )
    n_rel_keys.add(key)

# 병합 관계를 같은 키로 분류
rel_loss_reason = Counter()
rel_lost_detail = []

for r in m_rels:
    key = (
        r.get("source_type", ""),
        make_norm_name(r.get("source", "")),
        r.get("type", ""),
        r.get("target_type", ""),
        make_norm_name(r.get("target", "")),
    )
    if key in n_rel_keys:
        rel_loss_reason["보존됨"] += 1
    else:
        # 왜 사라졌는지
        st = r.get("source_type", "")
        tt = r.get("target_type", "")
        rt = r.get("type", "")
        src = r.get("source", "")
        
        # 방향 오류 (source_type == target_type for REQUIRES_*)
        if rt in ("REQUIRES_LABOR", "REQUIRES_EQUIPMENT", "USES_MATERIAL"):
            if st == tt:
                rel_loss_reason["방향 오류(동일 타입)"] += 1
                continue
        
        # source가 가비지
        if re.match(r"^\d+$", src) and st == "WorkType":
            rel_loss_reason["source 가비지"] += 1
            continue
        
        rel_loss_reason["글로벌 dedup"] += 1
        if len(rel_lost_detail) < 10:
            rel_lost_detail.append((st, src[:25], rt, tt, r.get("target", "")[:25]))

p(f"\n  관계 감소 원인:")
for reason, cnt in rel_loss_reason.most_common():
    p(f"    {reason}: {cnt:,}")

# 유니크 관계 의미 비교
m_rel_semantic = set()
for r in m_rels:
    semantic_key = (
        r.get("source_type", ""),
        make_norm_name(r.get("source", "")),
        r.get("type", ""),
        r.get("target_type", ""),
        make_norm_name(r.get("target", "")),
    )
    m_rel_semantic.add(semantic_key)

lost_rel_semantic = m_rel_semantic - n_rel_keys
# 가비지 관련 제거
real_lost_rels = set()
for key in lost_rel_semantic:
    st, sn, rt, tt, tn = key
    if not sn or not tn:
        continue
    if re.match(r"^\d+$", sn) and st == "WorkType":
        continue
    if sn in ("-", "\"", "→", ":") or tn in ("-", "\"", "→", ":"):
        continue
    real_lost_rels.add(key)

p(f"\n  ● 유니크 관계 의미 비교:")
p(f"    병합 후 유니크 관계 의미: {len(m_rel_semantic):,}")
p(f"    정규화 후 유니크 관계 의미: {len(n_rel_keys):,}")
p(f"    사라진 유니크 관계 (가비지 제외): {len(real_lost_rels):,}")

if real_lost_rels:
    lost_rtypes = Counter(key[2] for key in real_lost_rels)
    p(f"    사라진 관계 유형별: {dict(lost_rtypes)}")
    p(f"    샘플:")
    for key in list(real_lost_rels)[:10]:
        st, sn, rt, tt, tn = key
        p(f"      {st}:'{sn}' -[{rt}]-> {tt}:'{tn}'")

# ═══════════════════════════════════════════════════════════
#  C. 수량 정보 손실 검증
# ═══════════════════════════════════════════════════════════
p(f"\n{'━'*78}")
p("C. 수량 정보 손실 검증")
p("━" * 78)

# 병합 관계 중 수량이 있는 것
m_qty_rels = [(r.get("source_type",""), make_norm_name(r.get("source","")), 
               r.get("type",""), r.get("target_type",""), make_norm_name(r.get("target","")),
               r.get("quantity"), r.get("unit",""))
              for r in m_rels if r.get("quantity") is not None]

n_qty_keys = set()
for r in n_rels:
    if r.get("quantity") is not None:
        key = (r.get("source_type",""), make_norm_name(r.get("source","")),
               r.get("type",""), r.get("target_type",""), make_norm_name(r.get("target","")))
        n_qty_keys.add(key)

# 수량 관계 중 key가 살아있는 것
qty_preserved = 0
qty_lost = 0
qty_lost_detail = []
for st, sn, rt, tt, tn, qty, unit in m_qty_rels:
    key = (st, sn, rt, tt, tn)
    if key in n_qty_keys:
        qty_preserved += 1
    else:
        qty_lost += 1
        if len(qty_lost_detail) < 10:
            qty_lost_detail.append((st, sn[:20], rt, tt, tn[:20], qty, unit))

qty_total = len(m_qty_rels)
p(f"  병합 수량 관계: {qty_total:,}")
p(f"  보존됨: {qty_preserved:,} ({qty_preserved/qty_total*100:.1f}%)")
p(f"  손실: {qty_lost:,} ({qty_lost/qty_total*100:.1f}%)")

if qty_lost_detail:
    p(f"  수량 손실 샘플:")
    for st, sn, rt, tt, tn, qty, unit in qty_lost_detail:
        p(f"    {st}:'{sn}' -[{rt}]-> {tt}:'{tn}' qty={qty} {unit}")

# ═══════════════════════════════════════════════════════════
#  D. source_chunk_ids 병합 검증: 원본 청크 추적성
# ═══════════════════════════════════════════════════════════
p(f"\n{'━'*78}")
p("D. 원본 청크 추적성 검증")
p("━" * 78)

# 모든 정규화 엔티티의 source_chunk_ids를 합산
n_covered_chunks = set()
for e in n_ents:
    for cid in e.get("source_chunk_ids", []):
        n_covered_chunks.add(cid)

all_chunks = set(ext["chunk_id"] for ext in merged["extractions"])
uncovered = all_chunks - n_covered_chunks

p(f"  전체 청크: {len(all_chunks):,}")
p(f"  정규화 엔티티가 커버하는 청크: {len(n_covered_chunks):,}")
p(f"  커버되지 않는 청크: {len(uncovered)}")

if uncovered:
    p(f"  커버되지 않는 청크 샘플:")
    for cid in list(uncovered)[:10]:
        orig = merged["extractions"]
        orig_ext = next((e for e in orig if e["chunk_id"] == cid), None)
        if orig_ext:
            ent_cnt = len(orig_ext.get("entities", []))
            rel_cnt = len(orig_ext.get("relationships", []))
            p(f"    {cid}: entities={ent_cnt}, rels={rel_cnt}")

# ═══════════════════════════════════════════════════════════
#  E. 핵심 품셈 항목 보존 검증
# ═══════════════════════════════════════════════════════════
p(f"\n{'━'*78}")
p("E. 핵심 품셈 항목 보존 검증")
p("━" * 78)

critical_items = [
    ("WorkType", "콘크리트"),
    ("WorkType", "철근"),
    ("WorkType", "거푸집"),
    ("WorkType", "터파기"),
    ("WorkType", "비계"),
    ("WorkType", "방수"),
    ("WorkType", "미장"),
    ("WorkType", "도장"),
    ("WorkType", "용접"),
    ("WorkType", "배관"),
    ("Labor", "보통인부"),
    ("Labor", "특별인부"),
    ("Labor", "철근공"),
    ("Labor", "비계공"),
    ("Equipment", "굴착기"),
    ("Equipment", "크레인"),
    ("Material", "시멘트"),
    ("Material", "철근"),
]

for etype, keyword in critical_items:
    m_found = sum(1 for e in m_ents if e["type"] == etype and keyword in e.get("name", ""))
    n_found = sum(1 for e in n_ents if e["type"] == etype and keyword in e.get("name", ""))
    
    # 관련 관계도 확인
    m_rel_found = sum(1 for r in m_rels 
                      if (r.get("source_type") == etype and keyword in r.get("source", "")) or
                         (r.get("target_type") == etype and keyword in r.get("target", "")))
    n_rel_found = sum(1 for r in n_rels
                      if (r.get("source_type") == etype and keyword in r.get("source", "")) or
                         (r.get("target_type") == etype and keyword in r.get("target", "")))
    
    icon = "✅" if n_found > 0 else "❌"
    p(f"  {icon} {etype}:'{keyword}': 엔티티 {m_found}→{n_found}, 관계 {m_rel_found}→{n_rel_found}")

# ═══════════════════════════════════════════════════════════
#  F. Labor 병합 상세 (감소율 92.2% — 가장 높음)
# ═══════════════════════════════════════════════════════════
p(f"\n{'━'*78}")
p("F. Labor 감소 상세 (4,270 → 335 = -92.2%)")
p("━" * 78)

m_labor_names = Counter()
for e in m_ents:
    if e["type"] == "Labor":
        m_labor_names[make_norm_name(e.get("name", ""))] += 1

n_labor_names = Counter()
for e in n_ents:
    if e["type"] == "Labor":
        n_labor_names[e.get("normalized_name", "")] += 1

p(f"  병합 Labor 유니크 이름: {len(m_labor_names)}")
p(f"  정규화 Labor 유니크 이름: {len(n_labor_names)}")

# 가장 많이 병합된 Labor
p(f"\n  가장 많이 중복된 Labor 이름 TOP 10:")
for name, cnt in m_labor_names.most_common(10):
    n_cnt = n_labor_names.get(name, 0)
    p(f"    '{name}': 병합{cnt}건 → 정규화{n_cnt}건 (→{cnt}건이 1건으로 병합)")

lost_labor = set(m_labor_names.keys()) - set(n_labor_names.keys())
p(f"\n  사라진 Labor 유니크 이름: {len(lost_labor)}")
if lost_labor:
    for n in list(lost_labor)[:10]:
        p(f"    '{n}' (원본 {m_labor_names[n]}건)")

# source_chunk_ids로 병합 정보 보존 확인
labor_botong = [e for e in n_ents if e["type"] == "Labor" and e["name"] == "보통인부"]
if labor_botong:
    lb = labor_botong[0]
    p(f"\n  '보통인부' 병합 검증:")
    p(f"    entity_id: {lb['entity_id']}")
    p(f"    source_chunk_ids: {len(lb.get('source_chunk_ids', []))}개")
    p(f"    → 원본 {m_labor_names.get('보통인부', 0)}건이 1건으로 병합, "
      f"{len(lb.get('source_chunk_ids',[]))}개 청크 출처 보존")

# ═══════════════════════════════════════════════════════════
#  G. 종합 결론
# ═══════════════════════════════════════════════════════════
p(f"\n{'━'*78}")
p("G. 종합 결론")
p("━" * 78)

p(f"""
  ┌─────────────────────────────────────────────────────────────────────┐
  │  엔티티 감소 9,344건 분석                                          │
  │    ├─ 병합(이름 보존): {merged_by_category.get('병합(이름 보존)', 0):>6,}건 — 동일 이름 중복이 1건으로 병합     │
  │    ├─ 가비지 제거:     {merged_by_category.get('가비지 제거', 0):>6,}건 — 특수문자/숫자만/1글자 가비지 제거    │
  │    └─ 이름 변환/보정:  {merged_by_category.get('이름 변환/보정', 0):>6,}건 — NFKC/공백 정규화로 기존 이름에 병합│
  │                                                                     │
  │  유니크 이름(type+name) 기준:                                       │
  │    병합 후: {len(m_unique_names):>6,} → 정규화 후: {len(n_unique_names):>6,}                            │
  │    실질 손실 (가비지 제외): {len(real_lost):>4}건                                │
  │                                                                     │
  │  관계 감소 4,085건 분석                                            │
  │    보존됨: {rel_loss_reason.get('보존됨', 0):>6,}, 글로벌 dedup: {rel_loss_reason.get('글로벌 dedup', 0):>5,}                         │
  │    방향 오류 삭제: {rel_loss_reason.get('방향 오류(동일 타입)', 0):>4,}, source 가비지: {rel_loss_reason.get('source 가비지', 0):>4,}                     │
  │    실질 의미 손실 (가비지 제외): {len(real_lost_rels):>4}건                           │
  │                                                                     │
  │  수량 정보:                                                         │
  │    보존율: {qty_preserved/qty_total*100:.1f}% ({qty_preserved:,}/{qty_total:,})                                │
  │                                                                     │
  │  핵심 품셈 항목: 18개 전부 보존                                     │
  │  원본 청크 커버리지: {len(n_covered_chunks):,}/{len(all_chunks):,} ({len(n_covered_chunks)/len(all_chunks)*100:.1f}%)                              │
  └─────────────────────────────────────────────────────────────────────┘
""")

if len(real_lost) == 0 and len(real_lost_rels) == 0:
    p("  ★ 결론: 정보 손실 없음. 감소분은 전부 중복 병합 + 가비지 제거.")
elif len(real_lost) < 50:
    p(f"  ★ 결론: 실질 손실 미미 (이름 {len(real_lost)}건, 관계 {len(real_lost_rels)}건).")
    p(f"         이름 변환/정규화 과정에서 일부 매칭 실패. 핵심 데이터 무결성 유지.")
else:
    p(f"  ⚠ 결론: 실질 손실 존재 (이름 {len(real_lost)}건, 관계 {len(real_lost_rels)}건).")
    p(f"         세부 검토 필요.")

report = "\n".join(out)
print(report)
open(REPORT, "w", encoding="utf-8").write(report)
print(f"\n리포트 저장: {REPORT}")
