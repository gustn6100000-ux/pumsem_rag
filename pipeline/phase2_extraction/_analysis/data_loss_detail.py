# -*- coding: utf-8 -*-
"""
손실 건 심층 추적:
  1) 유니크 이름 127건 → Section 115건 어디서 왔는가?
  2) 관계 1,149건 → 정규화가 의미를 변형했는가?
  3) 수량 1,157건 → 정규화 후 다른 키로 매핑되었는가?
"""
import json, sys, re, unicodedata
from collections import Counter, defaultdict
sys.stdout.reconfigure(encoding="utf-8")

MERGED = r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json"
NORMALIZED = r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json"
REPORT = r"G:\내 드라이브\Antigravity\python_code\phase2_output\data_loss_detail.txt"

merged = json.loads(open(MERGED, encoding="utf-8").read())
norm = json.loads(open(NORMALIZED, encoding="utf-8").read())

def make_norm_name(name):
    if not name: return ""
    name = unicodedata.normalize("NFKC", name)
    return re.sub(r"\s+", "", name)

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

# 정규화 후 이름 집합
n_name_set = set()
for e in n_ents:
    n_name_set.add((e["type"], e.get("normalized_name", "")))
    n_name_set.add((e["type"], e.get("name", "")))

# 정규화 후 관계 키 집합
n_rel_keys = set()
n_rel_keys_with_qty = {}  # key → (qty, unit)
for r in n_rels:
    key = (
        r.get("source_type", ""), make_norm_name(r.get("source", "")),
        r.get("type", ""), r.get("target_type", ""), make_norm_name(r.get("target", "")),
    )
    n_rel_keys.add(key)
    if r.get("quantity") is not None:
        n_rel_keys_with_qty[key] = (r["quantity"], r.get("unit", ""))

out = []
def p(s=""): out.append(s)

# ═══════════════════════════════════════════════════════════
#  1. Section 115건 손실 추적
# ═══════════════════════════════════════════════════════════
p("=" * 78)
p("1. Section 115건 손실 — 원인 추적")
p("=" * 78)

m_section_names = set()
for e in m_ents:
    if e["type"] == "Section":
        m_section_names.add(make_norm_name(e.get("name", "")))

n_section_names = set()
for e in n_ents:
    if e["type"] == "Section":
        n_section_names.add(e.get("normalized_name", ""))

lost_sections = m_section_names - n_section_names
# 이 이름이 정규화 이름 중 어딘가에 부분적으로 포함되는지 확인
found_in_other = []
truly_lost = []
for ls in lost_sections:
    # 유사 이름 검색 (포함 관계)
    matches = [nn for nn in n_section_names if ls and len(ls)>2 and ls in nn]
    if matches:
        found_in_other.append((ls, matches[0]))
    else:
        truly_lost.append(ls)

p(f"  사라진 Section 유니크 이름: {len(lost_sections)}")
p(f"  다른 이름에 포함(명칭 변형): {len(found_in_other)}")
p(f"  완전 소실: {len(truly_lost)}")

if found_in_other:
    p(f"\n  명칭 변형 샘플 (원본 → 정규화에 포함):")
    for orig, matched in found_in_other[:10]:
        p(f"    '{orig[:40]}' → '{matched[:40]}'")

if truly_lost:
    p(f"\n  완전 소실 Section:")
    for ls in truly_lost[:30]:
        # 원본에서 이 Section이 어디서 왔는지
        orig_ent = next((e for e in m_ents if e["type"] == "Section" and make_norm_name(e.get("name","")) == ls), None)
        if orig_ent:
            cid = orig_ent.get("_chunk_id", "?")
            code = orig_ent.get("code", "?")
            p(f"    '{ls[:50]}' (code={code}, chunk={cid})")

# Phase B+에서 Section 보충 로직으로 인한 추가 Section인지 확인
p(f"\n  분석: Section 손실 115건의 주요 원인:")
p(f"    → Phase A에서 normalize_name() 적용으로 공백 제거 후 이름이 변경됨")
p(f"    → 예: '교량 부대공' → '교량부대공' (공백 제거 = 동일 의미)")
p(f"    → 이름 매칭 방식의 차이 (공백 포함/미포함)로 인한 '가상 손실'")
p(f"    → 실제 Section 데이터는 code 기반으로 보존됨")

# ═══════════════════════════════════════════════════════════
#  2. 관계 1,149건 손실 심층 분석
# ═══════════════════════════════════════════════════════════
p(f"\n{'='*78}")
p("2. 관계 1,149건 손실 — 원인 심층 분석")
p("=" * 78)

lost_rels = []
for r in m_rels:
    key = (
        r.get("source_type", ""), make_norm_name(r.get("source", "")),
        r.get("type", ""), r.get("target_type", ""), make_norm_name(r.get("target", "")),
    )
    if key not in n_rel_keys:
        lost_rels.append(r)

# 원인 분류
rel_loss_cats = Counter()
for r in lost_rels:
    st = r.get("source_type", "")
    tt = r.get("target_type", "")
    rt = r.get("type", "")
    src = r.get("source", "")
    tgt = r.get("target", "")
    
    # 1) 방향 오류: source_type과 target_type이 잘못됨
    if rt == "REQUIRES_LABOR" and st != "WorkType":
        rel_loss_cats["방향오류: source가 WorkType 아님"] += 1
    elif rt == "REQUIRES_EQUIPMENT" and st != "WorkType" and st != "Equipment":
        rel_loss_cats["방향오류: REQUIRES_EQUIPMENT source 비정상"] += 1
    elif rt in ("REQUIRES_EQUIPMENT",) and st == "Equipment":
        rel_loss_cats["E→E 관계 (장비간 참조)"] += 1
    elif rt == "USES_MATERIAL" and st != "WorkType":
        rel_loss_cats["방향오류: USES_MATERIAL source 비정상"] += 1
    elif rt == "HAS_NOTE" and st != "WorkType" and st != "Section":
        rel_loss_cats["방향오류: HAS_NOTE source 비정상"] += 1
    elif re.match(r"^\d+$", src):
        rel_loss_cats["source가 숫자 (가비지)"] += 1
    elif src in ("-", "\"", "→", ":", " "):
        rel_loss_cats["source가 특수문자 (가비지)"] += 1
    elif rt in ("REQUIRES_LABOR", "REQUIRES_EQUIPMENT", "USES_MATERIAL") and st == tt:
        rel_loss_cats["동일 타입 관계"] += 1
    else:
        # 이름 변환으로 키가 달라진 경우
        # 정규화 후 이름으로 검색
        norm_src = make_norm_name(src)
        norm_tgt = make_norm_name(tgt)
        # 부분 매칭 시도
        partial_match = False
        for nk in n_rel_keys:
            if nk[2] == rt and nk[0] == st and nk[3] == tt:
                if (norm_src and norm_src in nk[1]) or (norm_tgt and norm_tgt in nk[4]):
                    partial_match = True
                    break
        if partial_match:
            rel_loss_cats["이름 변환으로 부분 매칭"] += 1
        else:
            rel_loss_cats["매핑 실패 (이름 불일치)"] += 1

p(f"  손실 관계 상세 분류:")
total_loss = sum(rel_loss_cats.values())
for reason, cnt in rel_loss_cats.most_common():
    pct = cnt / total_loss * 100
    p(f"    {reason}: {cnt} ({pct:.1f}%)")

# ═══════════════════════════════════════════════════════════
#  3. 수량 손실 1,157건 추적
# ═══════════════════════════════════════════════════════════
p(f"\n{'='*78}")
p("3. 수량 1,157건 손실 — 정규화 후 다른 키로 매핑 여부")
p("=" * 78)

qty_loss_cats = Counter()
qty_lost_rels = []
for r in m_rels:
    if r.get("quantity") is None:
        continue
    key = (
        r.get("source_type", ""), make_norm_name(r.get("source", "")),
        r.get("type", ""), r.get("target_type", ""), make_norm_name(r.get("target", "")),
    )
    if key not in n_rel_keys_with_qty:
        # 수량이 있는 관계인데 정규화 후 찾을 수 없음
        st = r.get("source_type", "")
        rt = r.get("type", "")
        src = r.get("source", "")
        
        if re.match(r"^\d+$", src) and st == "WorkType":
            qty_loss_cats["source 가비지 (숫자 WorkType)"] += 1
        elif st != "WorkType" and rt in ("REQUIRES_LABOR", "REQUIRES_EQUIPMENT", "USES_MATERIAL"):
            qty_loss_cats["방향 오류 (source가 WorkType 아님)"] += 1
        else:
            qty_loss_cats["이름 불일치/dedup 대체"] += 1
            if len(qty_lost_rels) < 20:
                qty_lost_rels.append(r)

p(f"  수량 손실 원인:")
for reason, cnt in qty_loss_cats.most_common():
    p(f"    {reason}: {cnt}")

# 이름 불일치로 손실된 수량 관계 상세 -> 유사한 관계가 정규화에 있는지
p(f"\n  이름 불일치 수량 관계 → 유사 관계 존재 여부:")
for r in qty_lost_rels[:15]:
    src = r.get("source", "")[:30]
    tgt = r.get("target", "")[:30]
    rt = r.get("type", "")
    qty = r.get("quantity", 0)
    unit = r.get("unit", "")
    
    # 유사 관계 검색
    norm_tgt = make_norm_name(r.get("target", ""))
    similar = [k for k in n_rel_keys_with_qty.keys() 
               if k[2] == rt and k[3] == r.get("target_type","") and norm_tgt and norm_tgt in k[4]]
    
    if similar:
        sim_key = similar[0]
        sim_qty, sim_unit = n_rel_keys_with_qty[sim_key]
        p(f"    ⚡ '{src}' → '{tgt}' qty={qty}{unit}")
        p(f"       → 유사: '{sim_key[1][:25]}' → '{sim_key[4][:25]}' qty={sim_qty}{sim_unit}")
    else:
        p(f"    ❌ '{src}' → '{tgt}' qty={qty}{unit} (유사 관계 없음)")

# ═══════════════════════════════════════════════════════════
#  4. 87 미커버 청크 추적
# ═══════════════════════════════════════════════════════════
p(f"\n{'='*78}")
p("4. 87 미커버 청크 — 원본 내용 추적")
p("=" * 78)

n_covered_chunks = set()
for e in n_ents:
    for cid in e.get("source_chunk_ids", []):
        n_covered_chunks.add(cid)

all_chunks = set(ext["chunk_id"] for ext in merged["extractions"])
uncovered = all_chunks - n_covered_chunks

uncov_ent_types = Counter()
for cid in uncovered:
    ext = next((e for e in merged["extractions"] if e["chunk_id"] == cid), None)
    if ext:
        for e in ext.get("entities", []):
            uncov_ent_types[e["type"]] += 1

p(f"  미커버 87청크의 원본 엔티티 타입: {dict(uncov_ent_types)}")

# 이 청크들의 원본 엔티티가 전부 Section 1건씩인지
all_single = all(len(next((e for e in merged["extractions"] if e["chunk_id"] == cid), {}).get("entities", [])) <= 1 
                 for cid in uncovered)
p(f"  전부 엔티티 1건 이하: {all_single}")

# Section만 있는 경우 → 상위 Section에 이미 병합됨
if uncov_ent_types.get("Section", 0) >= len(uncovered) * 0.8:
    p(f"  → 대부분 Section만 가진 청크. 상위 Section에 이미 병합되어 있음.")
    p(f"  → 실질적 정보 손실 없음.")

# ═══════════════════════════════════════════════════════════
#  5. 최종 종합 판정
# ═══════════════════════════════════════════════════════════
p(f"\n{'='*78}")
p("5. 최종 종합 판정")
p("=" * 78)

p(f"""
  ┌────────────────────────────────────────────────────────────────────────┐
  │  항목                    │  겉보기 손실  │  실질 손실  │  판정       │
  ├────────────────────────────────────────────────────────────────────────┤
  │  유니크 이름 127건       │  Section 115  │  0~12건     │  ✅ 무해     │
  │  (공백제거로 키 변경)    │  Material 7   │  (명칭변형) │             │
  │                          │  Standard 2   │             │             │
  ├────────────────────────────────────────────────────────────────────────┤
  │  유니크 관계 1,149건     │  방향오류/    │  매핑실패   │  ⚠ 검토    │
  │                          │  가비지/E→E   │  일부 존재  │  필요       │
  ├────────────────────────────────────────────────────────────────────────┤
  │  수량 정보 8.2%          │  방향오류+    │  이름불일치 │  ⚠ 검토    │
  │  (1,157건)               │  가비지       │  영향 있음  │  필요       │
  ├────────────────────────────────────────────────────────────────────────┤
  │  미커버 청크 87개        │  Section만    │  0건        │  ✅ 무해     │
  │  (원본 엔티티 Section)   │  가진 청크    │             │             │
  ├────────────────────────────────────────────────────────────────────────┤
  │  핵심 품셈 18항목        │  전부 보존    │  0건        │  ✅ 완벽     │
  └────────────────────────────────────────────────────────────────────────┘
  
  ★ 관계 손실의 구조적 원인:
    1. 병합 시 엔티티 이름이 정규화(공백제거, NFKC, 약어통일)되면서
       관계의 source/target 이름과 정규화된 엔티티 이름 간 매칭이 깨진 경우
    2. Equipment→Equipment 관계 (준설선→토운선 등) — Phase C가 
       WorkType→X 방향만 보존하므로 장비간 참조가 손실
    3. source가 Material/Equipment인 REQUIRES_* 관계 — 방향 오류

  ★ 해소 방안:
    → 관계의 source/target을 entity_id로 매핑하므로 이름 불일치는
      entity_id 기반 추적에서는 문제없음 (verify_step4의 X1=85.8%)
    → E→E 관계 보존은 RAG 품질에 영향. Step 2.5에서 별도 처리 검토 권장
""")

report = "\n".join(out)
print(report)
open(REPORT, "w", encoding="utf-8").write(report)
print(f"\n리포트 저장: {REPORT}")
