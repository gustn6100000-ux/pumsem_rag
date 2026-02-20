# -*- coding: utf-8 -*-
"""Step 2.4 엔티티 정규화 & 중복 제거 (v1.2)

Phase A: 문자열 정규화 (NFKC, 공백, LABOR_MAP, 단위)
Phase B: 규칙 기반 중복 제거 (type + normalized_name + spec)
Phase C: 관계 방향 보정 (725건)
Phase D: 이상치 필터링 (P95×3)
Phase E: 관계 참조 갱신 (타입 안전 키)
Phase F: 엔티티 ID 부여 (W-0001 형식)

입력: merged_entities.json (25,708 엔티티)
출력: normalized_entities.json (~15,171 엔티티)
"""
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from config import (
    BASE_DIR,
    LABOR_NORMALIZE_MAP,
    MERGED_ENTITIES_FILE,
    PHASE2_OUTPUT,
)

NORMALIZED_FILE = PHASE2_OUTPUT / "normalized_entities.json"

# ════════════════════════════════════════════════════════════════
#  Labor 확장 매핑 (config.py 매핑에 추가)
# ════════════════════════════════════════════════════════════════
LABOR_NORMALIZE_MAP_EXT = {
    "중 급 기 술 자": "중급기술자",
    "고 급 기 술 자": "고급기술자",
    "초 급 기 술 자": "초급기술자",
    "중 급 기 능 사": "중급기능사",
    "초 급 기 능 사": "초급기능사",
    "특 수 용 접 공": "특수용접공",
    "건 축 목 공": "건축목공",
    "내 장 공": "내장공",
    "취 부 공": "취부공",
}
LABOR_MAP = {**LABOR_NORMALIZE_MAP, **LABOR_NORMALIZE_MAP_EXT}

# 단위 정규화
UNIT_NORMALIZE = {
    "㎥": "m³", "㎡": "m²", "㎝": "cm", "㎜": "mm",
    "㎞": "km", "㏊": "ha", "ℓ": "L",
    "KW": "kW", "Kw": "kW",
}

# 관계 방향 규칙: rel_type → (허용 source types, target_type)
# Why: HAS_NOTE는 Section/Equipment 등에서도 Note를 가질 수 있음
#      Section→Note(1,942건), Equipment→Note(383건) 등은 정상 관계
VALID_DIRECTIONS: dict[str, tuple[set[str], str]] = {
    "REQUIRES_LABOR": ({"WorkType"}, "Labor"),
    "REQUIRES_EQUIPMENT": ({"WorkType"}, "Equipment"),
    "USES_MATERIAL": ({"WorkType"}, "Material"),
    "HAS_NOTE": ({"WorkType", "Section", "Equipment", "Material", "Standard", "Labor"}, "Note"),
    "APPLIES_STANDARD": ({"WorkType", "Section", "Equipment", "Material"}, "Standard"),
    "BELONGS_TO": ({"WorkType"}, "Section"),
}

# 공종 패턴 (Equipment → WorkType 재분류용)
WORKTYPE_PATTERNS = re.compile(
    r"(타설|설치|해체|가공|조립|시공|운반|포설|배합|절단|천공|"
    r"굴착|굴삭|매설|되메우기|잔토처리|다짐|양생|결속|세우기|"
    r"인양|적재|하역|세척|도장|방수|미장|도배|타일붙이기)$"
)

# 주석 패턴 (WorkType → Note 재분류용)
NOTE_PATTERNS = re.compile(
    r"(^\[주\]|^①|^②|^③|^④|^⑤|^※|별도\s*계상|포함|제외|"
    r"할증|감소|적용|기준|비고|^주\)|주\d+\))"
)

TYPE_PREFIX = {
    "WorkType": "W", "Labor": "L", "Equipment": "E",
    "Material": "M", "Section": "S", "Note": "N", "Standard": "ST",
}


# ════════════════════════════════════════════════════════════════
#  Phase A: 문자열 정규화
# ════════════════════════════════════════════════════════════════
def normalize_name(name: str, entity_type: str) -> str:
    """이름 정규화. 순서: φ제거 → NFKC → LABOR_MAP → 공백 → 단위."""
    if not name:
        return ""

    # 0. 구경 φ 접두사 정규화 (NFKC 이전에 적용)

    
    # Why: step1은 "강관용접(200, SCH 40)"으로 추출하고
    #       step2 LLM은 "강관용접(φ200, SCH 40)"으로 추출하여
    #       동일 엔티티가 2벌로 분리되는 문제 해결
    # 패턴: φ/Φ/ø/∅/ɸ + (공백?) + 숫자 → 숫자만 (φ200 → 200, φ 15 → 15)
    name = re.sub(r'[φΦø∅ɸ]\s*(?=\d)', '', name)

    # 1. NFKC
    name = unicodedata.normalize("NFKC", name)

    # 2. Labor 전용 매핑
    if entity_type == "Labor":
        for old, new in LABOR_MAP.items():
            if old in name:
                name = name.replace(old, new)

    # 3. 공백 정규화
    name = re.sub(r"\s+", " ", name).strip()
    # 괄호 앞뒤 공백 제거: "굴착기 ( 0.6 )" → "굴착기(0.6)"
    name = re.sub(r"\s*\(\s*", "(", name)
    name = re.sub(r"\s*\)\s*", ")", name)

    # 4. 단위 정규화 (spec/name에 포함된 단위 기호)
    for old, new in UNIT_NORMALIZE.items():
        name = name.replace(old, new)

    return name


def normalize_spec(spec: str | None) -> str:
    """spec 정규화. None/""/"-"/"없음" → 빈 문자열."""
    if not spec or spec.strip() in ("", "-", "없음", "—", "―"):
        return ""
    # 0. φ 접두사 제거 (name과 동일 정규화)
    spec = re.sub(r'[φΦø∅ɸ]\s*(?=\d)', '', spec)
    spec = unicodedata.normalize("NFKC", spec)
    spec = re.sub(r"\s+", " ", spec).strip()
    spec = re.sub(r"\s*\(\s*", "(", spec)
    spec = re.sub(r"\s*\)\s*", ")", spec)
    for old, new in UNIT_NORMALIZE.items():
        spec = spec.replace(old, new)
    return spec


def normalize_entity(ent: dict) -> dict:
    """엔티티 1건 정규화."""
    ent["name"] = normalize_name(ent.get("name", ""), ent["type"])
    ent["normalized_name"] = normalize_name(
        ent.get("normalized_name", ent["name"]), ent["type"]
    ).replace(" ", "")
    ent["spec"] = normalize_spec(ent.get("spec"))
    if ent.get("unit"):
        for old, new in UNIT_NORMALIZE.items():
            ent["unit"] = ent["unit"].replace(old, new)
    return ent


def phase_a(extractions: list[dict]) -> dict:
    """Phase A: 모든 엔티티/관계에 문자열 정규화 적용."""
    unicode_cnt = 0
    space_cnt = 0

    for ext in extractions:
        for ent in ext.get("entities", []):
            old_name = ent.get("name", "")
            normalize_entity(ent)
            new_name = ent["name"]

            if old_name != new_name:
                if unicodedata.normalize("NFKC", old_name) != old_name:
                    unicode_cnt += 1
                else:
                    space_cnt += 1

        # 관계의 source/target 이름도 정규화
        for rel in ext.get("relationships", []):
            if rel.get("source"):
                rel["source"] = normalize_name(
                    rel["source"], rel.get("source_type", "")
                )
            if rel.get("target"):
                rel["target"] = normalize_name(
                    rel["target"], rel.get("target_type", "")
                )
            if rel.get("unit"):
                for old, new in UNIT_NORMALIZE.items():
                    rel["unit"] = rel["unit"].replace(old, new)

    return {"unicode_normalized": unicode_cnt, "space_normalized": space_cnt}


# ════════════════════════════════════════════════════════════════
#  Phase B: 규칙 기반 중복 제거
# ════════════════════════════════════════════════════════════════
EntityKey = tuple[str, ...]
NameMap = dict[tuple[str, str], str]  # (type, old_name) → new_name


def make_entity_key(ent: dict) -> EntityKey:
    """v1.2 키: type별 다른 그룹핑 전략."""
    etype = ent["type"]
    norm = ent.get("normalized_name", ent["name"].replace(" ", "")).lower()
    spec = normalize_spec(ent.get("spec"))

    if etype in ("WorkType", "Equipment", "Material"):
        # Codex #2: spec 포함으로 과잉 병합 방지
        return (etype, norm, spec.lower())
    elif etype == "Note":
        sid = ent.get("source_section_id", "unknown")
        return (etype, norm, sid)
    elif etype == "Section":
        code = ent.get("code", norm)
        return (etype, code)
    else:
        # Labor, Standard
        return (etype, norm)


def pick_representative(group: list[dict]) -> dict:
    """그룹에서 대표 엔티티 1건 선정."""
    method_order = {"merged": 0, "llm": 1, "table_rule": 2, "auto": 3}
    group.sort(key=lambda e: (
        -e.get("confidence", 0),
        method_order.get(e.get("source_method", "auto"), 9),
    ))
    rep = {**group[0]}

    # 모든 source_chunk_ids 수집
    all_chunks = set()
    for e in group:
        cid = e.get("source_chunk_id")
        if cid:
            all_chunks.add(cid)
        for c in e.get("source_chunk_ids", []):
            all_chunks.add(c)
    rep["source_chunk_ids"] = sorted(all_chunks)

    # spec: 가장 긴 것 채택
    best_spec = ""
    for e in group:
        s = e.get("spec", "")
        if s and len(s) > len(best_spec):
            best_spec = s
    if best_spec:
        rep["spec"] = best_spec

    # confidence: 최대값
    rep["confidence"] = max(e.get("confidence", 0) for e in group)

    return rep


def phase_b(extractions: list[dict]) -> tuple[list[dict], NameMap, dict]:
    """Phase B: 전체 엔티티 중복 제거.

    Returns:
      - deduped_entities: 대표 엔티티 리스트
      - name_map: {(type, 원래이름) → 대표이름}
      - stats: 통계
    """
    # 모든 엔티티 수집
    all_ents = []
    for ext in extractions:
        for ent in ext.get("entities", []):
            all_ents.append(ent)

    # 그룹화
    groups: dict[EntityKey, list[dict]] = defaultdict(list)
    for ent in all_ents:
        key = make_entity_key(ent)
        groups[key].append(ent)

    # 대표 선정 + name_map 생성
    deduped = []
    name_map: NameMap = {}

    for key, group in groups.items():
        rep = pick_representative(group)
        deduped.append(rep)

        # 그룹 내 모든 이름 → 대표 이름 매핑
        for ent in group:
            ent_type = ent["type"]
            ent_name = ent["name"]
            name_map[(ent_type, ent_name)] = rep["name"]

    stats = {
        "input_entities": len(all_ents),
        "output_entities": len(deduped),
        "dedup_removed": len(all_ents) - len(deduped),
        "dedup_groups": len(groups),
    }

    return deduped, name_map, stats


# ════════════════════════════════════════════════════════════════
#  Phase C: 관계 방향 보정
# ════════════════════════════════════════════════════════════════
def phase_c(
    extractions: list[dict],
    entity_map: dict[tuple[str, str], dict],
) -> dict:
    """관계 방향 보정. 직접 extractions를 수정.

    Returns: stats
    """
    fixed = 0
    deleted = 0
    warnings = []

    for ext in extractions:
        chunk_id = ext["chunk_id"]
        rels = ext.get("relationships", [])
        work_types = [e["name"] for e in ext.get("entities", []) if e["type"] == "WorkType"]
        first_wt = work_types[0] if work_types else None

        # Why: WorkType 부재 청크("기계경비" 테이블 등)에서 Section 이름을 fallback source로 사용
        # 이는 125건 방향 경고 중 Equipment→Material/Labor 관계 복구에 활용
        section_name = None
        if not first_wt:
            for ent in ext.get("entities", []):
                if ent["type"] == "Section":
                    section_name = ent["name"]
                    break

        new_rels = []
        for rel in rels:
            st = rel.get("source_type", "")
            tt = rel.get("target_type", "")
            rt = rel.get("type", "")

            rule = VALID_DIRECTIONS.get(rt)
            if not rule:
                # 규칙 없는 관계 (HAS_CHILD, REFERENCES 등) → 보존
                new_rels.append(rel)
                continue

            valid_sources, exp_tgt = rule

            # ── 이미 올바름 ──
            if st in valid_sources and tt == exp_tgt:
                new_rels.append(rel)
                continue

            # ── target 타입만 틀린 경우 → 재분류 시도 ──
            if st in valid_sources and tt != exp_tgt:
                # WorkType→WorkType (HAS_NOTE) → target을 Note로
                if rt == "HAS_NOTE" and tt == "WorkType":
                    if NOTE_PATTERNS.search(rel.get("target", "")):
                        rel["target_type"] = "Note"
                        new_rels.append(rel)
                        fixed += 1
                    else:
                        deleted += 1
                # WorkType→WorkType (APPLIES_STANDARD) → 삭제
                elif rt == "APPLIES_STANDARD" and tt != "Standard":
                    deleted += 1
                else:
                    deleted += 1
                continue

            # ── source 타입이 틀린 경우 ──
            # REQUIRES_LABOR/EQUIPMENT/USES_MATERIAL → source를 WorkType으로
            if rt in ("REQUIRES_LABOR", "REQUIRES_EQUIPMENT", "USES_MATERIAL"):
                if first_wt:
                    rel["source"] = first_wt
                    rel["source_type"] = "WorkType"
                    new_rels.append(rel)
                    fixed += 1
                elif section_name and rt == "USES_MATERIAL":
                    # Fallback: Section은 USES_MATERIAL에서만 source로 허용
                    # Why: REQUIRES_LABOR/EQUIPMENT에서 Section→Labor/Equipment는 의미적 부적합
                    rel["source"] = section_name
                    rel["source_type"] = "Section"
                    new_rels.append(rel)
                    fixed += 1
                else:
                    warnings.append({
                        "type": "direction_delete",
                        "chunk_id": chunk_id,
                        "detail": f"No WorkType for {st}→{tt} ({rt})",
                    })
                    deleted += 1
                continue

            # Equipment→Equipment (REQUIRES_EQUIPMENT) → source를 WorkType
            if rt == "REQUIRES_EQUIPMENT" and st == "Equipment" and tt == "Equipment":
                if WORKTYPE_PATTERNS.search(rel.get("source", "")):
                    rel["source_type"] = "WorkType"
                    new_rels.append(rel)
                    fixed += 1
                elif first_wt:
                    rel["source"] = first_wt
                    rel["source_type"] = "WorkType"
                    new_rels.append(rel)
                    fixed += 1
                else:
                    deleted += 1
                continue

            # Note→Note (HAS_NOTE) → 자기참조 무의미 삭제
            if rt == "HAS_NOTE" and st == tt == "Note":
                deleted += 1
                continue

            # 기타: source를 WorkType으로 교체 시도
            if first_wt and exp_tgt == tt:
                rel["source"] = first_wt
                rel["source_type"] = "WorkType"
                new_rels.append(rel)
                fixed += 1
            else:
                warnings.append({
                    "type": "direction_delete",
                    "chunk_id": chunk_id,
                    "detail": f"Unhandled {st}→{tt} ({rt})",
                })
                deleted += 1

        ext["relationships"] = new_rels

    return {
        "direction_fixed": fixed,
        "direction_deleted": deleted,
        "direction_warnings": len(warnings),
        "_warnings": warnings,
    }


# ════════════════════════════════════════════════════════════════
#  Phase D: 이상치 필터링
# ════════════════════════════════════════════════════════════════
OUTLIER_THRESHOLDS = {
    "REQUIRES_LABOR": 75,
    "REQUIRES_EQUIPMENT": 3300,
    "USES_MATERIAL": 225,
}


def phase_d(extractions: list[dict]) -> dict:
    """이상치 필터링. quantity > threshold → confidence 하향."""
    flagged = 0
    zero_deleted = 0
    outlier_log = []

    for ext in extractions:
        new_rels = []
        for rel in ext.get("relationships", []):
            qty = rel.get("quantity")
            rt = rel.get("type", "")

            # quantity == 0 → 삭제
            if qty is not None and qty == 0:
                zero_deleted += 1
                continue

            # > threshold → flag
            threshold = OUTLIER_THRESHOLDS.get(rt)
            if threshold and qty is not None and qty > threshold:
                rel["confidence"] = 0.3
                rel.setdefault("properties", {})["outlier_flag"] = True
                flagged += 1
                outlier_log.append({
                    "chunk_id": ext["chunk_id"],
                    "type": rt,
                    "quantity": qty,
                    "threshold": threshold,
                })

            new_rels.append(rel)
        ext["relationships"] = new_rels

    return {
        "outliers_flagged": flagged,
        "zero_quantity_deleted": zero_deleted,
        "_outlier_log": outlier_log,
    }


# ════════════════════════════════════════════════════════════════
#  Phase E: 관계 참조 갱신 + 중복 제거
# ════════════════════════════════════════════════════════════════
def phase_e(
    extractions: list[dict],
    name_map: NameMap,
    valid_entities: set[tuple[str, str]],
) -> dict:
    """관계의 source/target을 대표 이름으로 갱신 + 중복 제거.

    중복 키: (source, source_type, target, target_type, type, quantity, unit, per_unit)
    """
    updated = 0
    ref_deleted = 0
    dedup_removed = 0

    for ext in extractions:
        new_rels = []
        seen_keys = set()

        for rel in ext.get("relationships", []):
            src_type = rel.get("source_type", "")
            tgt_type = rel.get("target_type", "")
            src_name = rel.get("source", "")
            tgt_name = rel.get("target", "")

            # name_map으로 갱신
            new_src = name_map.get((src_type, src_name), src_name)
            new_tgt = name_map.get((tgt_type, tgt_name), tgt_name)

            if new_src != src_name or new_tgt != tgt_name:
                rel["source"] = new_src
                rel["target"] = new_tgt
                updated += 1

            # 참조 무결성 검증
            if (src_type, new_src) not in valid_entities and src_type not in ("", "Section"):
                ref_deleted += 1
                continue
            if (tgt_type, new_tgt) not in valid_entities and tgt_type not in ("", "Section"):
                ref_deleted += 1
                continue

            # 중복 제거: Codex #4 반영 with per_unit
            qty = rel.get("quantity")
            unit = rel.get("unit", "")
            per_unit = rel.get("per_unit", "")

            # Why: properties에 은닉된 spec을 안전 추출하여 PE관 15건 보존
            props = rel.get("properties") or {}
            sspec = normalize_spec(props.get("source_spec", ""))
            tspec = normalize_spec(props.get("target_spec", ""))

            dedup_key = (new_src, src_type, sspec, new_tgt, tgt_type, tspec,
                         rel.get("type", ""), qty, unit, per_unit)
            if dedup_key in seen_keys:
                dedup_removed += 1
                continue
            seen_keys.add(dedup_key)

            new_rels.append(rel)

        ext["relationships"] = new_rels

    return {
        "rel_names_updated": updated,
        "rel_ref_deleted": ref_deleted,
        "rel_dedup_removed": dedup_removed,
    }


# ════════════════════════════════════════════════════════════════
#  Phase F: 엔티티 ID 부여
# ════════════════════════════════════════════════════════════════
def phase_f(entities: list[dict]) -> tuple[dict[tuple, str], dict[tuple[str, str], str]]:
    """글로벌 유니크 ID 부여.

    Returns:
        exact_map:    {(type, name, spec) → entity_id}  정확 매칭용
        fallback_map: {(type, name) → entity_id}        spec 없는 관계 대비 대체용
    """
    counters = Counter()
    exact_map: dict[tuple, str] = {}
    fallback_map: dict[tuple[str, str], str] = {}

    # Why: type + name + spec 순으로 정렬하여 ID 발급 일관성 확보
    entities.sort(key=lambda e: (e["type"], e.get("normalized_name", ""), e.get("spec", "")))

    for ent in entities:
        etype = ent["type"]
        prefix = TYPE_PREFIX.get(etype, "X")
        counters[etype] += 1
        eid = f"{prefix}-{counters[etype]:04d}"
        ent["entity_id"] = eid

        # Why: spec을 포함한 3단 식별자 맵핑 (PE관 15개 규격 각각 고유 ID 보존)
        spec = normalize_spec(ent.get("spec"))
        exact_map[(etype, ent["name"], spec)] = eid

        # Why: spec이 없는 LLM 관계(REQUIRES_LABOR 등)가 고아 노드가 되지 않도록
        #      최초 1회만 등록하여 대표 ID로 fallback
        if (etype, ent["name"]) not in fallback_map:
            fallback_map[(etype, ent["name"])] = eid

        # Why: Section은 code/title/name 등 다양한 키로 참조됨 (HAS_CHILD에서)
        if etype == "Section":
            code = ent.get("code", "")
            title = ent.get("title", "")
            if code:
                exact_map[("Section", code, "")] = eid
                fallback_map[("Section", code)] = eid
            if title and title != ent["name"]:
                exact_map[("Section", title, "")] = eid
                fallback_map[("Section", title)] = eid

    return exact_map, fallback_map


# ════════════════════════════════════════════════════════════════
#  메인 파이프라인
# ════════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  Step 2.4 엔티티 정규화 & 중복 제거 (v1.2)")
    print("=" * 60)

    # 입력 로드
    print("\n  입력 로드...")
    data = json.loads(MERGED_ENTITIES_FILE.read_text(encoding="utf-8"))
    extractions = data["extractions"]
    global_rels = data.get("global_relationships", {})
    print(f"    청크: {len(extractions)}")
    print(f"    엔티티: {data['total_entities']:,}")
    print(f"    관계: {data['total_relationships']:,}")

    # ── Phase A ──
    print("\n  [Phase A] 문자열 정규화...")
    a_stats = phase_a(extractions)
    print(f"    NFKC: {a_stats['unicode_normalized']}")
    print(f"    공백: {a_stats['space_normalized']}")

    # ── Phase B ──
    print("\n  [Phase B] 규칙 기반 중복 제거...")
    deduped_ents, name_map, b_stats = phase_b(extractions)
    print(f"    입력: {b_stats['input_entities']:,}")
    print(f"    출력: {b_stats['output_entities']:,}")
    print(f"    제거: {b_stats['dedup_removed']:,} ({b_stats['dedup_removed']/b_stats['input_entities']*100:.1f}%)")

    # 유효 엔티티 집합 생성 (Codex #3: 타입 안전 키)
    valid_entities: set[tuple[str, str]] = set()
    entity_map: dict[tuple[str, str], dict] = {}
    for ent in deduped_ents:
        key = (ent["type"], ent["name"])
        valid_entities.add(key)
        entity_map[key] = ent

    # ── Phase B+: HAS_CHILD 미등록 Section 보충 ──
    # Why: TOC 기반 hierarchy에서 중간 카테고리 노드("기계손료", "거푸집" 등)가
    #      HAS_CHILD의 source/target으로 사용되지만 Section 엔티티로 등록되지 않음.
    #      이들을 자동으로 Section으로 등록하여 181건 매핑 누락 해소.
    print("\n  [Phase B+] HAS_CHILD 미등록 Section 보충...")
    bp_added = 0
    for rtype, rels in global_rels.items():
        if rtype != "HAS_CHILD":
            continue
        for rel in rels:
            for field in ("source", "target"):
                fname = rel.get(field, "")
                ftype = rel.get(f"{field}_type", "")
                if ftype == "Section" and (ftype, fname) not in valid_entities:
                    # Section 엔티티 자동 생성
                    src_cid = rel.get("source_chunk_id", "")
                    new_ent = {
                        "type": "Section",
                        "name": fname,
                        "normalized_name": fname.replace(" ", ""),
                        "spec": "",
                        "code": "",
                        "confidence": 0.8,
                        "source_method": "auto_hierarchy",
                        "source_chunk_ids": [src_cid] if src_cid else [],
                    }
                    deduped_ents.append(new_ent)
                    valid_entities.add(("Section", fname))
                    entity_map[("Section", fname)] = new_ent
                    bp_added += 1
    print(f"    자동 Section 생성: {bp_added}개")

    # ── Phase C ──
    print("\n  [Phase C] 관계 방향 보정...")
    c_stats = phase_c(extractions, entity_map)
    print(f"    수정: {c_stats['direction_fixed']}")
    print(f"    삭제: {c_stats['direction_deleted']}")

    # ── Phase D ──
    print("\n  [Phase D] 이상치 필터링...")
    d_stats = phase_d(extractions)
    print(f"    Flagged: {d_stats['outliers_flagged']}")
    print(f"    Zero deleted: {d_stats['zero_quantity_deleted']}")

    # ── Phase E ──
    print("\n  [Phase E] 관계 참조 갱신...")
    e_stats = phase_e(extractions, name_map, valid_entities)
    print(f"    이름 갱신: {e_stats['rel_names_updated']}")
    print(f"    참조 삭제: {e_stats['rel_ref_deleted']}")
    print(f"    중복 제거: {e_stats['rel_dedup_removed']}")

    # ── Phase F ──
    print("\n  [Phase F] 엔티티 ID 부여...")
    exact_map, fallback_map = phase_f(deduped_ents)
    print(f"    ID 부여 (Exact: {len(exact_map):,}, Fallback: {len(fallback_map):,})")

    # Why: BELONGS_TO는 properties.source_spec으로 정확한 ID를 찾고,
    #      REQUIRES_LABOR(spec 없는 관계)는 fallback으로 대표 ID에 안전하게 매핑
    def get_eid(etype: str, ename: str, espec: str) -> str:
        norm_spec = normalize_spec(espec)
        # 1. spec이 일치하는 정확한 ID 찾기
        eid = exact_map.get((etype, ename, norm_spec))
        if eid:
            return eid
        # 2. spec이 없거나 불일치하면 name 기준 대표 ID로 Fallback (관계 고아 방지)
        return fallback_map.get((etype, ename), "")

    # 관계에 entity_id 매핑 (청크)
    for ext in extractions:
        for rel in ext.get("relationships", []):
            props = rel.get("properties") or {}
            sspec = props.get("source_spec", "")
            tspec = props.get("target_spec", "")

            rel["source_entity_id"] = get_eid(
                rel.get("source_type", ""), rel.get("source", ""), sspec
            )
            rel["target_entity_id"] = get_eid(
                rel.get("target_type", ""), rel.get("target", ""), tspec
            )

    # global 관계도 ID 매핑
    for rel_type, rels in global_rels.items():
        for rel in rels:
            props = rel.get("properties") or {}
            sspec = props.get("source_spec", "")
            tspec = props.get("target_spec", "")

            rel["source_entity_id"] = get_eid(
                rel.get("source_type", ""), rel.get("source", ""), sspec
            )
            rel["target_entity_id"] = get_eid(
                rel.get("target_type", ""), rel.get("target", ""), tspec
            )
    # ── 글로벌 dedup: entity_id 기반 (Phase F 이후) ──
    # Why: 동일 entity_id 쌍이 서로 다른 청크에서 반복되는 관계 제거
    #      Phase E의 chunk 내 dedup은 name 기반이라 cross-chunk 중복 해소 불가
    print("\n  [글로벌 dedup] entity_id 기반 관계 중복 제거...")
    global_seen: set[tuple] = set()
    global_dup = 0
    for ext in extractions:
        deduped = []
        for rel in ext.get("relationships", []):
            g_key = (
                rel.get("source_entity_id", ""),
                rel.get("target_entity_id", ""),
                rel.get("type", ""),
                rel.get("quantity"),
                rel.get("unit", ""),
                rel.get("per_unit", ""),
            )
            if g_key in global_seen:
                global_dup += 1
                continue
            global_seen.add(g_key)
            deduped.append(rel)
        ext["relationships"] = deduped
    # global_relationships도 dedup
    for rtype, rels in global_rels.items():
        deduped_g = []
        for rel in rels:
            g_key = (
                rel.get("source_entity_id", ""),
                rel.get("target_entity_id", ""),
                rel.get("type", ""),
                rel.get("quantity"),
                rel.get("unit", ""),
                rel.get("per_unit", ""),
            )
            if g_key in global_seen:
                global_dup += 1
                continue
            global_seen.add(g_key)
            deduped_g.append(rel)
        global_rels[rtype] = deduped_g
    print(f"    글로벌 중복 제거: {global_dup:,}")

    # ── Phase G: 후처리 정제 ──
    # Why: 전수 데이터 검증(V1~V7)에서 발견된 잔존 이슈를 최종 정리
    print("\n  [Phase G] 후처리 정제...")

    # G-1. 가비지 엔티티 제거
    # 특수문자만 이름("-", "\"", "→"), 숫자만 WorkType, 콜론시작 등
    g_garbage = 0
    garbage_ids = set()
    cleaned_ents = []
    for e in deduped_ents:
        name = e.get("name", "")
        etype = e["type"]
        is_garbage = False

        # 특수문자만 이름
        if etype in ("WorkType", "Equipment", "Material", "Labor"):
            if name in ("-", "\"", "→", ":", "", " ", "·"):
                is_garbage = True
            # 숫자만 WorkType
            elif etype == "WorkType" and re.match(r"^\d+$", name):
                is_garbage = True
            # 숫자+단위만 Equipment/Material (040040, 060040 등)
            elif etype in ("Equipment", "Material") and re.match(
                r"^[\d.,]+\s*(mm|cm|m|kg|t|ton|kW)?$", name
            ):
                is_garbage = True

        # 콜론 시작 이름
        if name.startswith(":") or name.startswith("："):
            is_garbage = True

        # 1글자 이름 중 LLM 환각 가능성이 높은 패턴
        # "비", "눈", "떼" 등 — 테이블 행이 잘려서 추출된 가비지
        if not is_garbage and len(name) == 1 and etype in ("Material", "Labor"):
            # 유효한 1글자 이름 화이트리스트
            VALID_1CHAR = {"붓", "잭", "핀", "삽", "솔", "줄", "봉", "관", "판",
                           "통", "못", "물", "풀", "돌", "개", "정", "탭", "슈", "인"}
            if name not in VALID_1CHAR:
                is_garbage = True

        if is_garbage:
            garbage_ids.add(e.get("entity_id", ""))
            g_garbage += 1
        else:
            cleaned_ents.append(e)

    deduped_ents = cleaned_ents

    # 가비지 엔티티 참조 관계도 삭제
    g_rel_deleted = 0
    for ext in extractions:
        cleaned = []
        for rel in ext.get("relationships", []):
            if (rel.get("source_entity_id") in garbage_ids or
                    rel.get("target_entity_id") in garbage_ids):
                g_rel_deleted += 1
            else:
                cleaned.append(rel)
        ext["relationships"] = cleaned

    for rtype in list(global_rels.keys()):
        cleaned = []
        for rel in global_rels[rtype]:
            if (rel.get("source_entity_id") in garbage_ids or
                    rel.get("target_entity_id") in garbage_ids):
                g_rel_deleted += 1
            else:
                cleaned.append(rel)
        global_rels[rtype] = cleaned

    print(f"    가비지 엔티티 제거: {g_garbage}")
    print(f"    가비지 관계 제거: {g_rel_deleted}")

    # G-2. NFKC name 정규화 (name 필드 자체)
    # Why: Phase A에서 normalized_name에는 NFKC 적용했지만 name 필드는 원본 유지
    #      Section의 '⽊'→'木', '･'→'・', 'Ⅵ'→'VI' 등 호환문자 잔존
    g_nfkc = 0
    nfkc_map = {}  # old_name → new_name
    for e in deduped_ents:
        name = e.get("name", "")
        nfkc_name = unicodedata.normalize("NFKC", name)
        if nfkc_name != name:
            nfkc_map[name] = nfkc_name
            e["name"] = nfkc_name
            g_nfkc += 1

    # 관계의 source/target 이름도 갱신
    nfkc_rel_updated = 0
    for ext in extractions:
        for rel in ext.get("relationships", []):
            for field in ("source", "target"):
                old = rel.get(field, "")
                if old in nfkc_map:
                    rel[field] = nfkc_map[old]
                    nfkc_rel_updated += 1

    for rtype, rels in global_rels.items():
        for rel in rels:
            for field in ("source", "target"):
                old = rel.get(field, "")
                if old in nfkc_map:
                    rel[field] = nfkc_map[old]
                    nfkc_rel_updated += 1

    print(f"    NFKC name 수정: {g_nfkc}")
    print(f"    NFKC 관계 갱신: {nfkc_rel_updated}")

    # G-3. 자기참조 관계 삭제
    # Why: HAS_CHILD에서 source_entity_id == target_entity_id인 2건 발견
    g_selfref = 0
    for ext in extractions:
        cleaned = []
        for rel in ext.get("relationships", []):
            sid = rel.get("source_entity_id", "")
            tid = rel.get("target_entity_id", "")
            if sid and sid == tid:
                g_selfref += 1
            else:
                cleaned.append(rel)
        ext["relationships"] = cleaned

    for rtype in list(global_rels.keys()):
        cleaned = []
        for rel in global_rels[rtype]:
            sid = rel.get("source_entity_id", "")
            tid = rel.get("target_entity_id", "")
            if sid and sid == tid:
                g_selfref += 1
            else:
                cleaned.append(rel)
        global_rels[rtype] = cleaned

    print(f"    자기참조 삭제: {g_selfref}")

    # entity_id 재할당 (가비지 제거 후 연번 재정렬)
    print("    entity_id 재할당...")
    id_map_new = {}
    type_counters: dict[str, int] = defaultdict(int)
    for e in deduped_ents:
        etype = e["type"]
        type_counters[etype] += 1
        prefix = TYPE_PREFIX.get(etype, "X")
        new_id = f"{prefix}-{type_counters[etype]:04d}"
        old_id = e.get("entity_id", "")
        id_map_new[old_id] = new_id
        e["entity_id"] = new_id

    # 관계의 entity_id도 갱신
    for ext in extractions:
        for rel in ext.get("relationships", []):
            old_s = rel.get("source_entity_id", "")
            old_t = rel.get("target_entity_id", "")
            rel["source_entity_id"] = id_map_new.get(old_s, old_s)
            rel["target_entity_id"] = id_map_new.get(old_t, old_t)

    for rtype, rels in global_rels.items():
        for rel in rels:
            old_s = rel.get("source_entity_id", "")
            old_t = rel.get("target_entity_id", "")
            rel["source_entity_id"] = id_map_new.get(old_s, old_s)
            rel["target_entity_id"] = id_map_new.get(old_t, old_t)

    print(f"    entity_id 재할당 완료: {len(deduped_ents):,}")


    total_rels = sum(len(ext.get("relationships", [])) for ext in extractions)
    for rels in global_rels.values():
        total_rels += len(rels)

    ent_type_counts = Counter(e["type"] for e in deduped_ents)
    rel_type_counts = Counter()
    for ext in extractions:
        for r in ext.get("relationships", []):
            rel_type_counts[r["type"]] += 1
    for rtype, rels in global_rels.items():
        rel_type_counts[rtype] += len(rels)

    # ── 엔티티 source_chunk_ids 정리 ──
    # Why: 빈 문자열 chunk_id가 품질 지표(E1) 카운트를 왜곡하지 않도록 제거.
    for ent in deduped_ents:
        chunk_ids = ent.get("source_chunk_ids", [])
        if chunk_ids:
            cleaned = sorted({cid for cid in chunk_ids if cid})
            ent["source_chunk_ids"] = cleaned

    # ── 최종 통계 보정 (Phase B+/G 반영) ──
    # Why: output_entities/dedup_removed는 최종 산출 기준으로 갱신해야
    #      보고서/검증에서 stale 메타 불일치가 발생하지 않음.
    final_output_entities = len(deduped_ents)
    final_dedup_removed = b_stats["input_entities"] - final_output_entities

    # ── 출력 ──
    print("\n  출력 저장...")
    output = {
        "total_entities": len(deduped_ents),
        "total_relationships": total_rels,
        "entity_type_counts": dict(ent_type_counts.most_common()),
        "relationship_type_counts": dict(rel_type_counts.most_common()),
        "normalization_stats": {
            **a_stats,
            **b_stats,
            **{k: v for k, v in c_stats.items() if not k.startswith("_")},
            **{k: v for k, v in d_stats.items() if not k.startswith("_")},
            **e_stats,
            "global_dedup_removed": global_dup,
            "garbage_entities_removed": g_garbage,
            "garbage_rels_removed": g_rel_deleted,
            "nfkc_name_fixed": g_nfkc,
            "selfref_removed": g_selfref,
            "output_entities": final_output_entities,
            "dedup_removed": final_dedup_removed,
        },
        "entities": deduped_ents,
        "extractions": extractions,  # 관계 보존 (청크별)
        "global_relationships": global_rels,
        "warnings": c_stats.get("_warnings", []) + [
            {"type": "outlier", **o} for o in d_stats.get("_outlier_log", [])
        ],
    }

    NORMALIZED_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print(f"\n    저장: {NORMALIZED_FILE}")

    # 요약
    print("\n" + "=" * 60)
    print("  정규화 결과 요약")
    print("=" * 60)
    print(f"  총 엔티티: {len(deduped_ents):,}")
    print(f"  총 관계: {total_rels:,}")
    print(f"  엔티티 유형별:")
    for t, c in sorted(ent_type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c:,}")
    print(f"  관계 유형별:")
    for t, c in sorted(rel_type_counts.items(), key=lambda x: -x[1]):
        print(f"    {t}: {c:,}")
    print(f"  감소율: {b_stats['dedup_removed']/b_stats['input_entities']*100:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
