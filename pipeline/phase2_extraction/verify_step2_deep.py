# -*- coding: utf-8 -*-
"""Step 2.2 심층 품질 검증

추가 검증:
  Q1-R. 할루시네이션 재검증 (정교한 매칭 로직)
  Q7.   핵심 품셈 항목 대조 (원본 텍스트 ↔ 추출 결과)
  Q8.   Step 2.1 ↔ 2.2 교차 비교 (같은 청크, 다른 추출법)
  Q9.   관계 방향 검증 (source/target 타입 조합이 올바른가?)
"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = Path(__file__).resolve().parent.parent
CHUNKS_FILE = BASE / "phase1_output" / "chunks.json"
LLM_FILE = BASE / "phase2_output" / "llm_entities.json"
TABLE_FILE = BASE / "phase2_output" / "table_entities.json"
REPORT_FILE = BASE / "phase2_output" / "quality_report_deep.txt"

chunks_data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
chunk_map = {c["chunk_id"]: c for c in chunks_data["chunks"]}

llm_data = json.loads(LLM_FILE.read_text(encoding="utf-8"))
llm_exts = {e["chunk_id"]: e for e in llm_data["extractions"]}

table_data = json.loads(TABLE_FILE.read_text(encoding="utf-8"))
table_exts = {e["chunk_id"]: e for e in table_data["extractions"]}

lines = []
def log(msg=""):
    print(msg)
    lines.append(msg)


# ═══════════════════════════════════════════════════════════════
log("=" * 70)
log("  Step 2.2 심층 품질 검증")
log("=" * 70)


# ━━━ Q1-R. 할루시네이션 재검증 (정교한 매칭) ━━━━━━━━━━━━━━━━━
log("\n━━━ Q1-R. 할루시네이션 재검증 (개선된 매칭 로직) ━━━")

def build_source_text(chunk):
    """청크에서 모든 텍스트를 추출하여 공백 제거 후 소문자화"""
    parts = []
    parts.append(chunk.get("text", ""))
    parts.append(chunk.get("title", ""))
    parts.append(chunk.get("chapter", ""))
    parts.append(chunk.get("department", ""))
    # 테이블 제목, 헤더, 셀 값
    for t in chunk.get("tables", []):
        for h in t.get("headers", []):
            parts.append(str(h))
        for row in t.get("rows", []):
            for v in row.values():
                parts.append(str(v))
        for n in t.get("notes_in_table", []):
            parts.append(str(n))
    # 주석
    for n in chunk.get("notes", []):
        parts.append(str(n))
    # cross_references
    for xr in chunk.get("cross_references", []):
        parts.append(xr.get("context", ""))

    full = " ".join(parts)
    return full.replace(" ", "").lower()


def is_entity_in_source(ent_name, source_clean):
    """엔티티 이름이 원본에 존재하는지 정교하게 판단"""
    name_clean = ent_name.replace(" ", "").lower()

    # 1. 정확 매칭
    if name_clean in source_clean:
        return True

    # 2. 괄호 제거 후 매칭 (LLM이 규격을 이름에 포함시킨 경우)
    import re
    name_no_paren = re.sub(r'\([^)]*\)', '', name_clean).strip()
    if len(name_no_paren) >= 2 and name_no_paren in source_clean:
        return True

    # 3. 숫자+단위 조합 제거 후 매칭
    name_no_num = re.sub(r'[\d.,]+\s*(m³|m²|m|ton|kw|kwh|hr|mm|cm|대|인|본)', '', name_clean, flags=re.IGNORECASE).strip()
    if len(name_no_num) >= 2 and name_no_num in source_clean:
        return True

    # 4. 핵심 단어(2글자 이상) 추출 후 모두 존재 여부
    words = re.findall(r'[가-힣a-zA-Z]{2,}', ent_name)
    if words and all(w.lower() in source_clean for w in words):
        return True

    # 5. 이름의 앞 4글자 이상이 원본에 있으면 허용
    if len(name_clean) >= 4 and name_clean[:4] in source_clean:
        return True

    return False


total_ent = 0
miss_ent = 0
miss_by_type = Counter()
miss_examples = []

for ext in llm_data["extractions"]:
    chunk = chunk_map.get(ext["chunk_id"])
    if not chunk:
        continue

    source_clean = build_source_text(chunk)

    for ent in ext["entities"]:
        total_ent += 1
        if not is_entity_in_source(ent["name"], source_clean):
            miss_ent += 1
            miss_by_type[ent["type"]] += 1
            if len(miss_examples) < 15:
                miss_examples.append({
                    "chunk_id": ext["chunk_id"],
                    "type": ent["type"],
                    "name": ent["name"],
                    "title": ext.get("title", ""),
                })

miss_rate = miss_ent / total_ent * 100 if total_ent else 0
status1 = "✅" if miss_rate < 5 else "⚠️" if miss_rate < 10 else "❌"
log(f"  전체 엔티티: {total_ent:,}")
log(f"  매칭 실패: {miss_ent:,} ({miss_rate:.2f}%)")
log(f"  판정: {status1}")
log(f"  유형별 실패:")
for t, c in miss_by_type.most_common():
    log(f"    {t}: {c}")
log(f"\n  실패 샘플 (상위 15건):")
for ex in miss_examples:
    log(f"    [{ex['chunk_id']}] {ex['type']:10s} \"{ex['name']}\"  (섹션: {ex['title']})")


# ━━━ Q7. 핵심 품셈 항목 대조 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log("\n\n━━━ Q7. 핵심 품셈 항목 검색 & 대조 ━━━")

# 잘 알려진 품셈 공종명 리스트
KNOWN_WORK_TYPES = [
    "콘크리트 타설", "철근 가공", "거푸집", "터파기",
    "되메우기", "잡석다짐", "인력운반", "비계",
    "방수", "미장", "타일", "도장",
    "철근배근", "H-Beam", "용접",
]

found_items = {}
for ext in llm_data["extractions"]:
    for ent in ext["entities"]:
        if ent["type"] == "WorkType":
            for kw in KNOWN_WORK_TYPES:
                kw_clean = kw.replace(" ", "").lower()
                if kw_clean in ent["name"].replace(" ", "").lower():
                    if kw not in found_items:
                        found_items[kw] = []
                    found_items[kw].append({
                        "chunk_id": ext["chunk_id"],
                        "name": ent["name"],
                        "rels": len(ext["relationships"]),
                        "ents": len(ext["entities"]),
                    })

log(f"  검색 대상: {len(KNOWN_WORK_TYPES)}개 핵심 공종")
log(f"  발견된 공종: {len(found_items)}개\n")

for kw in KNOWN_WORK_TYPES:
    items = found_items.get(kw, [])
    if items:
        log(f"  ✅ \"{kw}\" → {len(items)}건 발견")
        for it in items[:2]:
            log(f"       [{it['chunk_id']}] {it['name']} (엔티티 {it['ents']}개, 관계 {it['rels']}개)")
    else:
        log(f"  ❌ \"{kw}\" → 미발견")


# ━━━ Q8. Step 2.1 ↔ 2.2 교차 비교 ━━━━━━━━━━━━━━━━━━━━━━━━━━
log("\n\n━━━ Q8. Step 2.1(테이블) ↔ Step 2.2(LLM) 교차 비교 ━━━")

# 양쪽 모두 존재하는 청크 찾기
overlap_ids = set(table_exts.keys()) & set(llm_exts.keys())
log(f"  Step 2.1 청크: {len(table_exts):,}")
log(f"  Step 2.2 청크: {len(llm_exts):,}")
log(f"  교차 청크: {len(overlap_ids):,}\n")

# 교차 청크에서 엔티티/관계 수 비교
consistent = 0
enrich_count = 0
degrade_count = 0
comparison_samples = []

for cid in sorted(list(overlap_ids))[:500]:
    t = table_exts[cid]
    l = llm_exts[cid]

    t_ents = len(t["entities"])
    l_ents = len(l["entities"])
    t_rels = len(t["relationships"])
    l_rels = len(l["relationships"])

    # LLM이 테이블 결과를 보강했는지
    if l_ents >= t_ents and l_rels >= t_rels:
        enrich_count += 1
    elif l_ents < t_ents * 0.5:  # LLM이 50% 미만
        degrade_count += 1
    else:
        consistent += 1

    if len(comparison_samples) < 3 and t_ents > 0 and l_ents > 0:
        # 테이블에서 추출한 엔티티 이름
        t_names = {e["name"] for e in t["entities"]}
        l_names = {e["name"] for e in l["entities"]}

        comparison_samples.append({
            "chunk_id": cid,
            "title": t.get("title", ""),
            "table_ents": t_ents, "llm_ents": l_ents,
            "table_rels": t_rels, "llm_rels": l_rels,
            "t_only": list(t_names - l_names)[:3],
            "l_only": list(l_names - t_names)[:3],
            "both": list(t_names & l_names)[:3],
        })

total_cmp = enrich_count + degrade_count + consistent
log(f"  LLM 보강 (≥ 테이블): {enrich_count:,} ({enrich_count/total_cmp*100:.1f}%)")
log(f"  일관적: {consistent:,} ({consistent/total_cmp*100:.1f}%)")
log(f"  LLM 저하 (< 테이블 50%): {degrade_count:,} ({degrade_count/total_cmp*100:.1f}%)")

log(f"\n  교차 비교 샘플:")
for s in comparison_samples:
    log(f"\n  ── {s['chunk_id']} ({s['title']}) ──")
    log(f"    테이블: {s['table_ents']}개 엔티티, {s['table_rels']}개 관계")
    log(f"    LLM:    {s['llm_ents']}개 엔티티, {s['llm_rels']}개 관계")
    if s["both"]:
        log(f"    공통 엔티티: {', '.join(s['both'])}")
    if s["t_only"]:
        log(f"    테이블에만: {', '.join(s['t_only'])}")
    if s["l_only"]:
        log(f"    LLM에만: {', '.join(s['l_only'])}")


# ━━━ Q9. 관계 방향(타입 조합) 검증 ━━━━━━━━━━━━━━━━━━━━━━━━━━
log("\n\n━━━ Q9. 관계 방향 검증 (source_type → target_type 올바른가?) ━━━")

# 올바른 조합 정의
VALID_COMBOS = {
    "REQUIRES_LABOR": ("WorkType", "Labor"),
    "REQUIRES_EQUIPMENT": ("WorkType", "Equipment"),
    "USES_MATERIAL": ("WorkType", "Material"),
    "HAS_NOTE": (None, "Note"),  # source는 여러 타입 가능
    "APPLIES_STANDARD": (None, "Standard"),
}

type_combos = Counter()
invalid_combos = Counter()
invalid_examples = []

for ext in llm_data["extractions"]:
    for rel in ext["relationships"]:
        combo = (rel["type"], rel.get("source_type", "?"), rel.get("target_type", "?"))
        type_combos[combo] += 1

        valid = VALID_COMBOS.get(rel["type"])
        if valid:
            expected_src, expected_tgt = valid
            ok = True
            if expected_src and rel.get("source_type") != expected_src:
                ok = False
            if expected_tgt and rel.get("target_type") != expected_tgt:
                ok = False
            if not ok:
                invalid_combos[combo] += 1
                if len(invalid_examples) < 5:
                    invalid_examples.append({
                        "chunk_id": ext["chunk_id"],
                        "rel": rel["type"],
                        "src": f"{rel['source']} ({rel.get('source_type', '?')})",
                        "tgt": f"{rel['target']} ({rel.get('target_type', '?')})",
                    })

log(f"  전체 관계: {sum(type_combos.values()):,}")
log(f"  유효하지 않은 타입 조합: {sum(invalid_combos.values()):,}")
invalid_rate = sum(invalid_combos.values()) / sum(type_combos.values()) * 100 if type_combos else 0
log(f"  비율: {invalid_rate:.1f}%")
status9 = "✅" if invalid_rate < 5 else "⚠️"
log(f"  판정: {status9}")

if invalid_combos:
    log(f"\n  잘못된 조합 TOP 5:")
    for combo, cnt in invalid_combos.most_common(5):
        log(f"    {combo[1]} ──{combo[0]}──→ {combo[2]} : {cnt}건")

if invalid_examples:
    log(f"\n  잘못된 관계 샘플:")
    for ex in invalid_examples[:3]:
        log(f"    [{ex['chunk_id']}] {ex['src']} ──{ex['rel']}──→ {ex['tgt']}")


# ━━━ 종합 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
log("\n" + "=" * 70)
log("  심층 검증 종합")
log("=" * 70)
log(f"  Q1-R 할루시네이션(보정) : {miss_rate:.2f}%  {status1}")
log(f"  Q7   핵심 공종 발견    : {len(found_items)}/{len(KNOWN_WORK_TYPES)}")
log(f"  Q8   LLM 보강율       : {enrich_count/total_cmp*100:.1f}%")
log(f"  Q8   LLM 저하율       : {degrade_count/total_cmp*100:.1f}%")
log(f"  Q9   관계 방향 오류    : {invalid_rate:.1f}%  {status9}")
log("=" * 70)

# 저장
REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
print(f"\n  리포트 저장: {REPORT_FILE}")
