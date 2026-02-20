# -*- coding: utf-8 -*-
"""Step 2.3: 관계 생성 & 병합 (Relation Builder)

A. Step 2.1(테이블) + Step 2.2(LLM) 엔티티/관계 병합
B. BELONGS_TO 관계 자동 생성 (WorkType → Section)
C. HAS_CHILD 관계 자동 생성 (Section 계층)
D. REFERENCES 관계 자동 생성 (교차참조)

입력: table_entities.json, llm_entities.json, chunks.json, toc_parsed.json
출력: merged_entities.json
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# ─── 경로 설정 ─────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
PHASE1_OUTPUT = BASE_DIR / "phase1_output"
PHASE2_OUTPUT = BASE_DIR / "phase2_output"

CHUNKS_FILE = PHASE1_OUTPUT / "chunks.json"
TOC_FILE = BASE_DIR / "toc_parser" / "toc_parsed.json"

TABLE_ENTITIES_FILE = PHASE2_OUTPUT / "table_entities.json"
LLM_ENTITIES_FILE = PHASE2_OUTPUT / "llm_entities.json"
MERGED_FILE = PHASE2_OUTPUT / "merged_entities.json"


# ═══════════════════════════════════════════════════════════════
# A. 엔티티 & 관계 병합
# ═══════════════════════════════════════════════════════════════

def _entity_key(ent: dict) -> str:
    """엔티티 동일성 판별 키. type + normalized_name (+ spec) 기반."""
    norm = ent.get("normalized_name", ent["name"].replace(" ", ""))
    spec = ent.get("spec", "")

    # Why: PE관처럼 name이 동일하고 spec(관경)만 다른 엔티티의 과잉 병합 방지
    if ent["type"] in ("WorkType", "Equipment", "Material") and spec:
        safe_spec = str(spec).replace(" ", "").lower()
        return f"{ent['type']}::{norm.lower()}::{safe_spec}"

    return f"{ent['type']}::{norm.lower()}"


def _rel_key(rel: dict) -> str:
    """관계 동일성 판별 키. (properties 내 spec 참조로 스키마 안전)"""
    src = rel['source'].replace(' ', '').lower()
    tgt = rel['target'].replace(' ', '').lower()

    # Why: properties에 은닉된 spec을 안전하게 추출 (방어 코드 — 향후 Step 2 개선 시 활성화)
    props = rel.get("properties") or {}
    src_spec = str(props.get("source_spec", "")).replace(' ', '').lower()
    tgt_spec = str(props.get("target_spec", "")).replace(' ', '').lower()

    if src_spec:
        src = f"{src}::{src_spec}"
    if tgt_spec:
        tgt = f"{tgt}::{tgt_spec}"

    per_unit = str(rel.get("per_unit", "")).replace(' ', '').lower()
    
    parts = [rel['type'], src, tgt]
    if per_unit:
        parts.append(per_unit)

    return "::".join(parts)


def merge_chunk_extractions(table_ext: dict | None, llm_ext: dict | None) -> dict:
    """같은 chunk_id의 Step 2.1/2.2 결과를 하나로 병합.

    병합 우선순위:
      - name/spec: LLM 우선 (더 자연스러운 한국어)
      - quantity/unit: 테이블 우선 (정확한 수치)
      - confidence: 최대값
    """
    if table_ext is None and llm_ext is None:
        return {}
    if table_ext is None:
        return llm_ext
    if llm_ext is None:
        return table_ext

    # LLM 결과를 기본으로, 테이블 결과를 보충
    merged = {**llm_ext}
    merged["source_method"] = "merged"

    # ── 엔티티 병합 ──
    # Why: merged_ent_map으로 통합 관리하여 테이블 간 중복도 처리
    merged_ent_map: dict[str, dict] = {}
    merged_entities: list[dict] = []

    for ent in llm_ext.get("entities", []):
        key = _entity_key(ent)
        if key not in merged_ent_map:
            merged_ent_map[key] = ent
            merged_entities.append(ent)

    for tent in table_ext.get("entities", []):
        key = _entity_key(tent)
        if key in merged_ent_map:
            # 이미 존재 → 테이블의 수량/단위로 덮어쓰기 (테이블이 더 정확)
            existing = merged_ent_map[key]
            if tent.get("quantity") is not None:
                existing["quantity"] = tent["quantity"]
            if tent.get("unit"):
                existing["unit"] = tent["unit"]
            existing["confidence"] = max(
                existing.get("confidence", 0),
                tent.get("confidence", 0),
            )
            existing["source_method"] = "merged"
        else:
            # 테이블에만 존재 → 추가
            tent_copy = {**tent, "source_method": "table_rule"}
            merged_entities.append(tent_copy)
            merged_ent_map[key] = tent_copy

    merged["entities"] = merged_entities

    # ── 관계 병합 ── (테이블 수치 우선)
    merged_rel_map: dict[str, dict] = {}
    merged_rels: list[dict] = []

    # LLM 관계 먼저 등록
    for rel in llm_ext.get("relationships", []):
        key = _rel_key(rel)
        if key not in merged_rel_map:
            merged_rel_map[key] = rel
            merged_rels.append(rel)

    # 테이블 관계: 새 키면 추가, 기존 키면 수치 덮어쓰기
    for trel in table_ext.get("relationships", []):
        key = _rel_key(trel)
        if key in merged_rel_map:
            existing = merged_rel_map[key]
            if trel.get("quantity") is not None:
                existing["quantity"] = trel["quantity"]
            if trel.get("unit"):
                existing["unit"] = trel["unit"]
            if trel.get("per_unit"):
                existing["per_unit"] = trel["per_unit"]
            existing["source_method"] = "merged"
        else:
            merged_rel_map[key] = trel
            merged_rels.append(trel)

    merged["relationships"] = merged_rels

    # confidence: 양쪽 최대
    merged["confidence"] = max(
        llm_ext.get("confidence", 0),
        table_ext.get("confidence", 0),
    )

    return merged


def merge_all(table_data: dict, llm_data: dict) -> dict:
    """전체 Step 2.1 + 2.2 결과를 병합."""
    print("  [A] 엔티티/관계 병합 시작...")

    # chunk_id 기준 인덱싱
    table_map = {e["chunk_id"]: e for e in table_data.get("extractions", [])}
    llm_map = {e["chunk_id"]: e for e in llm_data.get("extractions", [])}

    all_chunk_ids = sorted(set(table_map.keys()) | set(llm_map.keys()))
    print(f"    전체 청크: {len(all_chunk_ids)}")
    print(f"    테이블만: {len(set(table_map.keys()) - set(llm_map.keys()))}")
    print(f"    LLM만: {len(set(llm_map.keys()) - set(table_map.keys()))}")
    print(f"    교차: {len(set(table_map.keys()) & set(llm_map.keys()))}")

    merged_extractions = []
    total_before_ents = 0
    total_after_ents = 0
    total_before_rels = 0
    total_after_rels = 0

    for cid in all_chunk_ids:
        t = table_map.get(cid)
        l = llm_map.get(cid)

        before_e = len((t or {}).get("entities", [])) + len((l or {}).get("entities", []))
        before_r = len((t or {}).get("relationships", [])) + len((l or {}).get("relationships", []))

        merged = merge_chunk_extractions(t, l)
        if merged:
            merged_extractions.append(merged)
            after_e = len(merged.get("entities", []))
            after_r = len(merged.get("relationships", []))
            total_before_ents += before_e
            total_after_ents += after_e
            total_before_rels += before_r
            total_after_rels += after_r

    dedup_ents = total_before_ents - total_after_ents
    dedup_rels = total_before_rels - total_after_rels
    print(f"    병합 전 엔티티: {total_before_ents:,} → 후: {total_after_ents:,} (중복 제거: {dedup_ents:,})")
    print(f"    병합 전 관계: {total_before_rels:,} → 후: {total_after_rels:,} (중복 제거: {dedup_rels:,})")

    return {
        "extractions": merged_extractions,
        "merge_stats": {
            "total_chunks": len(all_chunk_ids),
            "entities_before": total_before_ents,
            "entities_after": total_after_ents,
            "entities_dedup": dedup_ents,
            "relationships_before": total_before_rels,
            "relationships_after": total_after_rels,
            "relationships_dedup": dedup_rels,
        },
    }


# ═══════════════════════════════════════════════════════════════
# B. BELONGS_TO 관계 생성
# ═══════════════════════════════════════════════════════════════

_SID_PATTERN = re.compile(r"^\d{1,2}-\d{1,2}(-\d{1,3})?$")


def validate_section_id(sid: str) -> tuple[bool, str]:
    """섹션 ID 검증 및 정규화.
    Returns: (is_valid, normalized_id)
    """
    if not sid or sid.strip() == "":
        return False, "unknown"

    base = sid.split("#")[0].strip()

    if _SID_PATTERN.match(base):
        return True, base

    # 숫자-숫자 형태 (2단계)도 허용
    if re.match(r"^\d{1,2}-\d{1,2}$", base):
        return True, base

    return False, "unknown"


def generate_belongs_to(
    merged_extractions: list[dict],
    chunks: list[dict],
) -> tuple[list[dict], list[dict]]:
    """WorkType → Section BELONGS_TO 관계 + Section 엔티티 생성.

    Returns: (new_section_entities, new_belongs_to_rels)
    """
    print("  [B] BELONGS_TO 관계 생성...")

    chunk_meta = {c["chunk_id"]: c for c in chunks}

    # 기존 Section 엔티티 수집
    existing_sections = set()
    for ext in merged_extractions:
        for ent in ext.get("entities", []):
            if ent["type"] == "Section":
                existing_sections.add(ent.get("code") or ent.get("normalized_name", ""))

    section_entities: dict[str, dict] = {}  # section_id → entity
    belongs_to_rels: list[dict] = []
    skip_count = 0
    invalid_count = 0

    for ext in merged_extractions:
        chunk_id = ext["chunk_id"]
        raw_sid = ext.get("section_id", "")
        meta = chunk_meta.get(chunk_id, {})

        is_valid, norm_sid = validate_section_id(raw_sid)
        if not is_valid:
            invalid_count += 1
            # department 기준 fallback
            dept = meta.get("department", "")
            if dept:
                norm_sid = f"dept_{dept}"
            else:
                continue

        # Section 엔티티 생성 (없으면)
        if norm_sid not in section_entities:
            section_entities[norm_sid] = {
                "type": "Section",
                "name": meta.get("title", norm_sid),
                "normalized_name": norm_sid.replace(" ", ""),
                "code": norm_sid,
                "spec": None,
                "unit": None,
                "quantity": None,
                "properties": {
                    "department": meta.get("department", ""),
                    "chapter": meta.get("chapter", ""),
                },
                "confidence": 1.0,
                "source_chunk_id": chunk_id,
                "source_section_id": norm_sid,
                "source_method": "auto",
            }

        # 이 청크의 모든 WorkType → Section
        work_types = [e for e in ext.get("entities", []) if e["type"] == "WorkType"]
        for wt in work_types:
            spec = wt.get("spec") or ""
            rel = {
                "source": wt["name"],
                "source_type": "WorkType",
                "target": section_entities[norm_sid]["name"],
                "target_type": "Section",
                "type": "BELONGS_TO",
                "quantity": None,
                "unit": None,
                "per_unit": None,
                # Why: 스키마 위반 없이 properties 내부에 규격 은닉 (PE관 15건 분리용)
                "properties": {
                    "section_id": norm_sid,
                    "source_spec": spec,
                },
                "source_chunk_id": chunk_id,
            }
            belongs_to_rels.append(rel)

    # Why: name만으로 dedup하면 PE관 15건이 1건으로 축소됨 → spec 포함 키 사용
    seen = set()
    unique_rels = []
    for r in belongs_to_rels:
        r_spec = (r.get("properties") or {}).get("source_spec", "")
        key = f"{r['source']}::{r_spec}::{r['target']}"
        if key not in seen:
            unique_rels.append(r)
            seen.add(key)

    new_sections = list(section_entities.values())
    print(f"    Section 엔티티 생성: {len(new_sections)}")
    print(f"    BELONGS_TO 관계 생성: {len(unique_rels)}")
    print(f"    비정상 section_id: {invalid_count}")

    return new_sections, unique_rels


# ═══════════════════════════════════════════════════════════════
# C. HAS_CHILD 관계 생성 (섹션 계층)
# ═══════════════════════════════════════════════════════════════

def generate_has_child(
    toc_data: dict,
    section_entities: dict[str, dict],
) -> list[dict]:
    """toc_parsed.json에서 섹션 계층 관계 자동 생성.

    계층:
      "6" → "6-1", "6-2", "6-3"       (장 → 절)
      "6-3" → "6-3-1", "6-3-2"        (절 → 항)
    """
    print("  [C] HAS_CHILD 관계 생성...")

    section_map = toc_data.get("section_map", {})
    all_sids = set(section_map.keys()) | set(section_entities.keys())

    has_child_rels = []

    for sid in sorted(all_sids):
        parts = sid.split("-")
        if len(parts) < 2:
            continue

        # 부모 ID 결정
        parent_id = "-".join(parts[:-1])

        if parent_id in all_sids:
            # 부모/자식 이름 결정
            parent_info = section_map.get(parent_id, {})
            child_info = section_map.get(sid, {})
            parent_name = parent_info.get("title", parent_id)
            child_name = child_info.get("title", sid)

            rel = {
                "source": parent_name,
                "source_type": "Section",
                "target": child_name,
                "target_type": "Section",
                "type": "HAS_CHILD",
                "quantity": None,
                "unit": None,
                "per_unit": None,
                "properties": {
                    "parent_id": parent_id,
                    "child_id": sid,
                    "level": len(parts),
                },
                "source_chunk_id": "",
            }
            has_child_rels.append(rel)

    print(f"    HAS_CHILD 관계 생성: {len(has_child_rels)}")
    return has_child_rels


# ═══════════════════════════════════════════════════════════════
# D. REFERENCES 관계 생성 (교차참조)
# ═══════════════════════════════════════════════════════════════

def generate_references(
    chunks: list[dict],
    section_entities: dict[str, dict],
) -> list[dict]:
    """chunks.json의 cross_references에서 REFERENCES 관계 생성."""
    print("  [D] REFERENCES 관계 생성...")

    valid_sids = set(section_entities.keys())
    ref_rels = []
    skip_count = 0

    for chunk in chunks:
        xrefs = chunk.get("cross_references", [])
        if not xrefs:
            continue

        src_sid = chunk.get("section_id", "")
        _, src_norm = validate_section_id(src_sid)

        for xr in xrefs:
            # cross_reference에서 참조 섹션 ID 추출
            context = xr.get("context", "")
            ref_sid = xr.get("ref_section", "")

            # ref_section이 없는 경우 context에서 추출 시도
            if not ref_sid and context:
                match = re.search(r"(\d{1,2}-\d{1,2}(?:-\d{1,3})?)", context)
                if match:
                    ref_sid = match.group(1)

            if not ref_sid:
                skip_count += 1
                continue

            _, ref_norm = validate_section_id(ref_sid)
            if ref_norm == "unknown" or ref_norm == src_norm:
                skip_count += 1
                continue

            src_name = section_entities.get(src_norm, {}).get("name", src_norm)
            tgt_name = section_entities.get(ref_norm, {}).get("name", ref_norm)

            rel = {
                "source": src_name,
                "source_type": "Section",
                "target": tgt_name,
                "target_type": "Section",
                "type": "REFERENCES",
                "quantity": None,
                "unit": None,
                "per_unit": None,
                "properties": {"context": context[:200]},
                "source_chunk_id": chunk["chunk_id"],
            }
            ref_rels.append(rel)

    # 중복 제거
    seen = set()
    unique = []
    for r in ref_rels:
        key = f"{r['source']}::{r['target']}"
        if key not in seen:
            unique.append(r)
            seen.add(key)

    print(f"    REFERENCES 관계 생성: {len(unique)} (스킵: {skip_count})")
    return unique


# ═══════════════════════════════════════════════════════════════
# 메인 실행
# ═══════════════════════════════════════════════════════════════

def run_step3():
    """Step 2.3 전체 실행."""
    print("=" * 70)
    print("  Step 2.3: 관계 생성 & 병합")
    print("=" * 70)

    # ── 데이터 로드 ──
    print("\n  데이터 로드...")
    table_data = json.loads(TABLE_ENTITIES_FILE.read_text(encoding="utf-8"))
    llm_data = json.loads(LLM_ENTITIES_FILE.read_text(encoding="utf-8"))
    chunks_data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = chunks_data["chunks"]
    toc_data = json.loads(TOC_FILE.read_text(encoding="utf-8"))

    print(f"    Step 2.1: {table_data['total_entities']:,} 엔티티, {table_data['total_relationships']:,} 관계")
    print(f"    Step 2.2: {llm_data['total_entities']:,} 엔티티, {llm_data['total_relationships']:,} 관계")
    print(f"    청크: {len(chunks):,}")
    print(f"    TOC 섹션: {len(toc_data.get('section_map', {})):,}")

    # ── A. 병합 ──
    print()
    merged = merge_all(table_data, llm_data)
    merged_exts = merged["extractions"]

    # ── B. BELONGS_TO ──
    print()
    new_sections, belongs_to_rels = generate_belongs_to(merged_exts, chunks)

    # Section 엔티티를 dict로 변환 (C, D에서 사용)
    section_ent_map = {}
    for se in new_sections:
        sid = se.get("code") or se.get("source_section_id", "")
        section_ent_map[sid] = se

    # ── C. HAS_CHILD ──
    print()
    has_child_rels = generate_has_child(toc_data, section_ent_map)

    # ── D. REFERENCES ──  
    print()
    ref_rels = generate_references(chunks, section_ent_map)

    # ── 통합 결과 조립 ──
    print("\n  최종 결과 조립...")

    # Why: generate_belongs_to에서 이름 기반 매칭으로 생성한 BELONGS_TO가 누락될 수 있음.
    # 조립 단계에서 각 청크의 모든 WorkType에 대해 직접 BELONGS_TO를 보장.
    total_bt_added = 0

    for ext in merged_exts:
        cid = ext["chunk_id"]
        raw_sid = ext.get("section_id", "")
        _, norm_sid = validate_section_id(raw_sid)

        # Section 엔티티 추가 (중복 방지)
        has_section = any(e["type"] == "Section" for e in ext.get("entities", []))
        if not has_section and norm_sid in section_ent_map:
            ext["entities"].append(section_ent_map[norm_sid])

        # BELONGS_TO 보장: 청크 내 모든 WorkType에 대해
        # Why: (name, spec) 튜플로 검색해야 PE관 15건이 각각 독립된 BELONGS_TO를 갖음
        existing_bt = {
            (r["source"], (r.get("properties") or {}).get("source_spec", ""))
            for r in ext.get("relationships", [])
            if r["type"] == "BELONGS_TO"
        }
        section_name = section_ent_map.get(norm_sid, {}).get("name", norm_sid)

        for ent in ext.get("entities", []):
            if ent["type"] == "WorkType":
                ent_spec = ent.get("spec") or ""
                if (ent["name"], ent_spec) not in existing_bt:
                    bt_rel = {
                        "source": ent["name"],
                        "source_type": "WorkType",
                        "target": section_name,
                        "target_type": "Section",
                        "type": "BELONGS_TO",
                        "quantity": None,
                        "unit": None,
                        "per_unit": None,
                        # Why: 스키마 보호 — properties에 규격 캡슐화
                        "properties": {
                            "section_id": norm_sid,
                            "source_spec": ent_spec,
                        },
                        "source_chunk_id": cid,
                    }
                    ext["relationships"].append(bt_rel)
                    total_bt_added += 1

    print(f"    BELONGS_TO 추가 보강: {total_bt_added}개")

    # 전체 통계 집계
    total_ent = sum(len(e.get("entities", [])) for e in merged_exts)
    total_rel = sum(len(e.get("relationships", [])) for e in merged_exts)

    # HAS_CHILD와 REFERENCES는 글로벌 관계 (특정 청크에 속하지 않음)
    # → 별도 최상위 필드로 저장
    total_rel += len(has_child_rels) + len(ref_rels)

    # 타입별 집계
    ent_type_counts = Counter()
    rel_type_counts = Counter()
    for ext in merged_exts:
        for e in ext.get("entities", []):
            ent_type_counts[e["type"]] += 1
        for r in ext.get("relationships", []):
            rel_type_counts[r["type"]] += 1
    for r in has_child_rels:
        rel_type_counts[r["type"]] += 1
    for r in ref_rels:
        rel_type_counts[r["type"]] += 1

    result = {
        "total_chunks": len(merged_exts),
        "processed_chunks": len(merged_exts),
        "total_entities": total_ent,
        "total_relationships": total_rel,
        "entity_type_counts": dict(ent_type_counts.most_common()),
        "relationship_type_counts": dict(rel_type_counts.most_common()),
        "merge_stats": merged["merge_stats"],
        "extractions": merged_exts,
        "global_relationships": {
            "HAS_CHILD": has_child_rels,
            "REFERENCES": ref_rels,
        },
    }

    # ── 저장 ──
    PHASE2_OUTPUT.mkdir(parents=True, exist_ok=True)
    MERGED_FILE.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n  {'='*60}")
    print(f"  결과 저장: {MERGED_FILE}")
    print(f"  {'='*60}")
    print(f"  총 엔티티: {total_ent:,}")
    print(f"  총 관계: {total_rel:,}")
    print(f"  엔티티 유형별:")
    for t, c in ent_type_counts.most_common():
        print(f"    {t}: {c:,}")
    print(f"  관계 유형별:")
    for t, c in rel_type_counts.most_common():
        print(f"    {t}: {c:,}")
    print(f"  {'='*60}")

    return result


if __name__ == "__main__":
    run_step3()
