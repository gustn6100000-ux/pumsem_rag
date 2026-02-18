"""Tier 2: 심화 품질 검증 (Deep Quality Check)

Phase 1 전처리 결과물(chunks.json)을 원본(raw_sections.json)과 대조하여
텍스트 충실도·테이블 정확도·숫자 보존 등 7개 항목을 전수 검사.

REV.02 주요 변경:
  - D2/D5/D6 원본 소스를 raw_sections.json으로 전환 (마커 범위 과다 포함 해소)
  - D2 parsed_cells에 notes_in_table 포함
  - D2 셀 비교 정규화 (composite header, 후행 0)
  - extract_cells_from_html을 BeautifulSoup 기반으로 전환

실행:
    py quality_deep_check.py           # 전체 검사
    py quality_deep_check.py --sample  # 부문별 20개 샘플만
"""
import json
import re
import sys
from pathlib import Path
from datetime import datetime
from collections import Counter, defaultdict

from bs4 import BeautifulSoup

from config import (
    MD_DIR, CHUNKS_FILE, RAW_SECTIONS_FILE, TOC_FILE, OUTPUT_DIR,
    DEEP_CHECK_THRESHOLDS, DEEP_CHECK_REPORT_FILE, PATTERNS,
)
from utils.html_utils import extract_cell_text, expand_table


# ─── 유틸리티 ─────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def tokenize_for_comparison(text: str) -> set[str]:
    """비교용 토큰화: 공백·마커·특수문자 제거 후 단어 집합 반환"""
    # HTML 태그 제거
    text = re.sub(r"<[^>]+>", " ", text)
    # 마커 제거
    text = re.sub(r"<!--.*?-->", " ", text)
    # 특수문자를 공백으로
    text = re.sub(r"[|─━═\-\+\*#>]", " ", text)
    # 토큰 추출
    tokens = set(re.findall(r"\S+", text.lower()))
    return tokens


def strip_html_for_numbers(text: str) -> str:
    """D6용: HTML 태그를 제거하여 속성값(colspan, rowspan 등) 숫자 오추출 방지"""
    import html as html_module
    # HTML 주석 제거
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    # HTML 태그 제거 (속성값 포함)
    text = re.sub(r"<[^>]+>", " ", text)
    # HTML 엔티티 디코딩 (&#8805; → ≥ 등, 숫자로 오인 방지)
    text = html_module.unescape(text)
    return text


def extract_numbers(text: str) -> list[str]:
    """텍스트에서 수치 토큰 추출 (예: '3인', '0.6m³', '100m', '2,500')

    HTML 태그가 포함된 텍스트는 먼저 태그를 제거하여
    colspan/rowspan 등 속성값이 숫자로 추출되는 것을 방지.
    """
    # HTML 태그가 포함되어 있으면 제거
    if "<" in text:
        text = strip_html_for_numbers(text)
    # 숫자+단위 패턴 또는 독립 숫자
    numbers = re.findall(
        r"\d[\d,]*\.?\d*\s*(?:인|명|대|m[²³]?|km|kg|t|ton|개|본|매|㎡|㎥|%|식|조|ea|set|hr|분|초|원)",
        text,
    )
    # 독립 숫자 (소수점, 콤마 포함)
    numbers += re.findall(r"(?<!\w)\d[\d,]*\.?\d*(?!\w)", text)
    return [n.strip() for n in numbers if n.strip()]


def _match_number_in_text(core: str, combined: str) -> bool:
    """D6용: 숫자 코어가 결합 텍스트에 존재하는지 다단계 매칭.

    1차: 원본 그대로
    2차: 콤마 제거 (3,500 → 3500)
    3차: 후행 0 제거/추가 (0.10 → 0.1, 0.1 → 0.10)
    4차: 정수/소수 등가 (3.0 → 3)
    """
    # 1차: 원본 그대로
    if core in combined:
        return True
    # 2차: 콤마 제거
    no_comma = core.replace(",", "")
    if no_comma != core and no_comma in combined:
        return True
    # 3차: 후행 0 변형
    if "." in no_comma:
        # 후행 0 제거: "0.10" → "0.1"
        trimmed = re.sub(r"(\.\d*?)0+$", r"\1", no_comma)
        trimmed = re.sub(r"\.$", "", trimmed)
        if trimmed != no_comma and trimmed in combined:
            return True
        # 후행 0 추가: "0.1" → "0.10" (try_numeric이 보존한 형태)
        padded = no_comma + "0"
        if padded in combined:
            return True
    else:
        # 정수에 ".0" 추가: "3" → "3.0"
        with_decimal = no_comma + ".0"
        if with_decimal in combined:
            return True
    # 4차: 소수점 이하가 0인 경우: "3.0" → "3", "3.00" → "3"
    if "." in no_comma:
        int_part = no_comma.split(".")[0]
        dec_part = no_comma.split(".")[1]
        if dec_part and all(c == "0" for c in dec_part):
            if int_part in combined:
                return True
    return False


def normalize_cell_for_comparison(text: str) -> str:
    """D2 셀 비교용 정규화.

    composite header 구분자, 후행 0, HTML 엔티티, 공백, 콤마 등을 정규화.
    """
    text = re.sub(r"\s+", "", text)       # 공백 제거
    text = text.replace('\xa0', '')       # 비파괴 공백
    text = text.replace('&nbsp;', '')     # HTML 엔티티
    text = re.sub(r"_", "", text)         # composite header 구분자 제거
    text = text.replace("^", "")          # 윗첨자 표기 제거 (10^-7 → 10-7)
    text = text.replace(",", "")          # 숫자 콤마 제거 (3,500 → 3500)
    # 후행 0 제거: "0.10" → "0.1", "3.50" → "3.5"
    text = re.sub(r"(\d+\.\d*?)0+$", r"\1", text)
    # 소수점 뒤에 아무것도 안 남으면 정리: "3." → "3"
    text = re.sub(r"\.$", "", text)
    return text


# ─── raw_sections.json 기반 원본 조회 ──────────────────────────

_raw_sections_cache = None


def _load_raw_sections() -> dict:
    """raw_sections.json을 로드하여 (section_id, source_file) → raw_text 매핑 생성"""
    global _raw_sections_cache
    if _raw_sections_cache is not None:
        return _raw_sections_cache

    data = load_json(RAW_SECTIONS_FILE)
    mapping = {}
    for sec in data["sections"]:
        key = (sec["section_id"], sec.get("source_file", ""))
        mapping[key] = sec.get("raw_text", "")
    _raw_sections_cache = mapping
    return mapping


def get_section_raw_text(section_id: str, source_file: str) -> str:
    """raw_sections.json에서 섹션의 원시 텍스트를 반환.

    Step 1에서 이미 올바르게 분리한 텍스트를 사용하므로
    마커 범위 과다 포함 문제가 발생하지 않는다.
    """
    mapping = _load_raw_sections()
    return mapping.get((section_id, source_file), "")


def get_html_tables_from_raw_section(section_id: str, source_file: str) -> list[str]:
    """raw_sections.json 기반으로 섹션 내 HTML 테이블 추출"""
    raw_text = get_section_raw_text(section_id, source_file)
    if not raw_text:
        return []
    return re.findall(r"<table.*?</table>", raw_text, re.DOTALL | re.IGNORECASE)


# ─── 하위 호환용 MD 기반 원본 조회 (D1에서 사용) ──────────────

def get_section_text_from_md(md_path: Path, section_id: str) -> str:
    """MD 원본에서 특정 섹션의 원시 텍스트 추출.

    SECTION/CONTEXT 마커 사이의 텍스트를 반환한다.
    같은 section_id의 모든 SECTION/CONTEXT 마커 범위를 합산한다.
    """
    if not md_path.exists():
        return ""
    content = md_path.read_text(encoding="utf-8")

    base_id = section_id.split("#")[0]

    all_marker_pat = re.compile(
        rf"<!-- (?:SECTION|CONTEXT): {re.escape(base_id)} \|.*?-->",
    )
    any_marker_pat = re.compile(r"<!-- (?:SECTION|CONTEXT): \S+ \|")

    collected_text = []
    for match in all_marker_pat.finditer(content):
        start = match.end()
        next_any = any_marker_pat.search(content, start)
        end = next_any.start() if next_any else len(content)
        collected_text.append(content[start:end])

    return "\n".join(collected_text)


def extract_cells_from_html(html: str) -> list[str]:
    """HTML 테이블에서 셀 텍스트 추출 (파서와 동일한 expand_table 사용).

    expand_table()로 rowspan/colspan을 동일하게 전개하여
    파서와 정확히 같은 기준으로 셀을 추출한다.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return []
    grid = expand_table(table)
    cells = []
    for row in grid:
        for cell_text in row:
            if cell_text and cell_text.strip():
                cells.append(cell_text.strip())
    return cells


# ─── D1: 원본 텍스트 충실도 ──────────────────────────────────

def _group_chunks_by_section(chunks: list[dict]) -> dict:
    """같은 section_id + source_file의 청크들을 그룹화"""
    groups = defaultdict(list)
    for c in chunks:
        key = (c["section_id"], c.get("source_file", ""))
        groups[key].append(c)
    return groups


def check_d1_text_fidelity(chunks: list[dict]) -> dict:
    """원본 텍스트와 청크 텍스트의 Containment(포함도) 측정.

    raw_sections.json 기반으로 원본을 조회한다.
    """
    results = []
    failures = []
    groups = _group_chunks_by_section(chunks)

    for (section_id, src_file), group_chunks in groups.items():
        combined_text = " ".join(c.get("text", "") for c in group_chunks)
        for c in group_chunks:
            for t in c.get("tables", []):
                for row in t.get("rows", []):
                    combined_text += " " + " ".join(str(v) for v in row.values())
        if not combined_text.strip():
            continue

        original = get_section_raw_text(section_id, src_file)
        if not original:
            continue

        chunk_tokens = tokenize_for_comparison(combined_text)
        orig_tokens = tokenize_for_comparison(original)
        if not chunk_tokens or not orig_tokens:
            continue

        intersection = chunk_tokens & orig_tokens
        containment = len(intersection) / len(chunk_tokens)

        results.append(containment)
        if containment < DEEP_CHECK_THRESHOLDS["text_fidelity_min"]:
            failures.append({
                "section_id": section_id,
                "source_file": src_file,
                "containment": round(containment, 3),
                "chunk_count": len(group_chunks),
                "chunk_tokens": len(chunk_tokens),
                "matched_tokens": len(intersection),
                "orig_tokens": len(orig_tokens),
            })

    avg = sum(results) / len(results) if results else 0
    pass_count = sum(1 for r in results if r >= DEEP_CHECK_THRESHOLDS["text_fidelity_min"])
    pass_rate = pass_count / len(results) if results else 0

    severity = "PASS" if pass_rate >= 0.95 else ("WARN" if pass_rate >= 0.85 else "FAIL")

    return {
        "check": "D1_text_fidelity",
        "severity": severity,
        "metric": "containment",
        "checked_sections": len(results),
        "avg_containment": round(avg, 4),
        "pass_count": pass_count,
        "pass_rate": round(pass_rate, 4),
        "threshold": DEEP_CHECK_THRESHOLDS["text_fidelity_min"],
        "failures": failures[:20],
        "failure_count": len(failures),
    }


# ─── D2: 테이블 파싱 정확도 ──────────────────────────────────

def check_d2_table_accuracy(chunks: list[dict]) -> dict:
    """파싱된 테이블 셀과 원본 HTML 테이블 셀의 일치율.

    REV.02 개선:
    - 원본을 raw_sections.json에서 조회 (마커 범위 과다 포함 해소)
    - parsed_cells에 notes_in_table 텍스트 포함
    - 셀 비교 시 정규화 적용 (composite header, 후행 0)
    """
    results = []
    failures = []
    groups = _group_chunks_by_section(chunks)

    for (section_id, src_file), group_chunks in groups.items():
        # 그룹 내 모든 파싱된 테이블 수집
        all_tables = []
        for c in group_chunks:
            all_tables.extend(c.get("tables", []))
        if not all_tables:
            continue

        # raw_sections.json 기반 원본 HTML 테이블 추출
        html_tables = get_html_tables_from_raw_section(section_id, src_file)
        if not html_tables:
            continue

        # 원본 HTML 셀 수집 (BeautifulSoup 기반)
        orig_cells_normalized = set()
        for ht in html_tables:
            for cell in extract_cells_from_html(ht):
                normalized = normalize_cell_for_comparison(cell)
                if normalized and normalized not in ("-", ""):
                    orig_cells_normalized.add(normalized)
        if not orig_cells_normalized:
            continue

        # 파싱된 테이블 셀 수집 (rows + headers + notes_in_table)
        parsed_cells_normalized = set()
        for t in all_tables:
            # 데이터 행
            for row in t.get("rows", []):
                for v in row.values():
                    normalized = normalize_cell_for_comparison(str(v))
                    if normalized and normalized not in ("-", ""):
                        parsed_cells_normalized.add(normalized)
            # 헤더 (개별 구성요소도 분해하여 추가)
            for header in t.get("headers", []):
                full_norm = normalize_cell_for_comparison(str(header))
                if full_norm:
                    parsed_cells_normalized.add(full_norm)
                # composite header 분해: "시공량_무근" → "시공량", "무근" 도 추가
                if "_" in str(header):
                    for part in str(header).split("_"):
                        part_norm = normalize_cell_for_comparison(part)
                        if part_norm:
                            parsed_cells_normalized.add(part_norm)
            # 주석 행 텍스트 (notes_in_table)
            for note in t.get("notes_in_table", []):
                for token in re.findall(r"\S+", note):
                    normalized = normalize_cell_for_comparison(token)
                    if normalized and len(normalized) > 1:
                        parsed_cells_normalized.add(normalized)

        # 1차: set membership 매칭
        matched = sum(1 for c in orig_cells_normalized if c in parsed_cells_normalized)

        # 2차: fallback — 매칭 안 된 셀을 전체 결합 텍스트에서 substring 검색
        # (주석 행이 합쳐지거나, 셀 형식이 달라진 경우 커버)
        if matched < len(orig_cells_normalized):
            combined_text = ""
            for c in group_chunks:
                combined_text += " " + c.get("text", "")
                for t in c.get("tables", []):
                    for header in t.get("headers", []):
                        combined_text += " " + str(header)
                    for row in t.get("rows", []):
                        combined_text += " " + " ".join(str(v) for v in row.values())
                    for note in t.get("notes_in_table", []):
                        combined_text += " " + note
            combined_normalized = normalize_cell_for_comparison(combined_text)
            for c in orig_cells_normalized:
                if c not in parsed_cells_normalized and len(c) >= 2:
                    if c in combined_normalized:
                        matched += 1

        accuracy = matched / len(orig_cells_normalized) if orig_cells_normalized else 1.0
        results.append(accuracy)

        if accuracy < DEEP_CHECK_THRESHOLDS["table_cell_accuracy_min"]:
            failures.append({
                "section_id": section_id,
                "source_file": src_file,
                "accuracy": round(accuracy, 3),
                "orig_cells": len(orig_cells_normalized),
                "parsed_cells": len(parsed_cells_normalized),
                "matched": matched,
                "chunk_count": len(group_chunks),
            })

    avg_accuracy = sum(results) / len(results) if results else 0
    pass_count = sum(1 for r in results if r >= DEEP_CHECK_THRESHOLDS["table_cell_accuracy_min"])
    pass_rate = pass_count / len(results) if results else 0

    severity = "PASS" if pass_rate >= 0.95 else ("WARN" if pass_rate >= 0.85 else "FAIL")

    return {
        "check": "D2_table_accuracy",
        "severity": severity,
        "checked_sections": len(results),
        "avg_accuracy": round(avg_accuracy, 4),
        "pass_count": pass_count,
        "pass_rate": round(pass_rate, 4),
        "threshold": DEEP_CHECK_THRESHOLDS["table_cell_accuracy_min"],
        "failures": failures[:20],
        "failure_count": len(failures),
    }


# ─── D3: 섹션 경계 정확성 ───────────────────────────────────

def check_d3_section_boundary(chunks: list[dict]) -> dict:
    """청크 텍스트에 다른 섹션의 ID가 혼입되어 있는지 검사"""
    all_section_ids = set(c["section_id"].split("#")[0] for c in chunks)
    contaminated = []

    for chunk in chunks:
        text = chunk.get("text", "")
        if not text.strip():
            continue

        own_id = chunk["section_id"].split("#")[0]
        own_parts = own_id.split("-")

        found_ids = set(re.findall(r"(?:^|\s)(\d+-\d+(?:-\d+)?)\s", text, re.MULTILINE))

        for fid in found_ids:
            if fid == own_id:
                continue
            fid_parts = fid.split("-")
            if len(fid_parts) < len(own_parts) and own_id.startswith(fid):
                continue
            if fid.startswith(own_id + "-"):
                continue
            if len(fid_parts) == len(own_parts) and fid_parts[:-1] == own_parts[:-1]:
                try:
                    diff = abs(int(fid_parts[-1]) - int(own_parts[-1]))
                    if diff <= 1:
                        continue
                except ValueError:
                    pass
            if fid not in all_section_ids:
                continue

            contaminated.append({
                "chunk_id": chunk["chunk_id"],
                "own_section": own_id,
                "foreign_section": fid,
            })

    severity = "PASS" if len(contaminated) <= DEEP_CHECK_THRESHOLDS["section_contamination_max"] else "WARN"

    return {
        "check": "D3_section_boundary",
        "severity": severity,
        "contaminated_count": len(contaminated),
        "threshold": DEEP_CHECK_THRESHOLDS["section_contamination_max"],
        "contaminated": contaminated[:20],
    }


# ─── D4: 교차참조 무결성 ────────────────────────────────────

def check_d4_crossref_validity(chunks: list[dict]) -> dict:
    """청크 내 교차참조가 실제 존재하는 섹션을 가리키는지 검증"""
    all_section_ids = set(c["section_id"].split("#")[0] for c in chunks)
    total_refs = 0
    valid_refs = 0
    invalid = []

    for chunk in chunks:
        refs = chunk.get("cross_references", [])
        if not refs:
            continue

        for ref in refs:
            ref_id = ref if isinstance(ref, str) else ref.get("target", "")
            if not ref_id:
                continue
            total_refs += 1
            base_ref = ref_id.split("#")[0]
            if base_ref in all_section_ids:
                valid_refs += 1
            else:
                invalid.append({
                    "chunk_id": chunk["chunk_id"],
                    "section_id": chunk["section_id"],
                    "invalid_ref": ref_id,
                })

    validity_rate = valid_refs / total_refs if total_refs > 0 else 1.0
    threshold = DEEP_CHECK_THRESHOLDS["crossref_validity_min"]
    severity = "PASS" if validity_rate >= threshold else ("WARN" if validity_rate >= 0.9 else "FAIL")

    return {
        "check": "D4_crossref_validity",
        "severity": severity,
        "total_refs": total_refs,
        "valid_refs": valid_refs,
        "validity_rate": round(validity_rate, 4),
        "threshold": threshold,
        "invalid_refs": invalid[:20],
    }


# ─── D5: 주석 추출 정확도 ───────────────────────────────────

def check_d5_notes_recall(chunks: list[dict]) -> dict:
    """원본의 [주] 블록이 청크 notes에 포함되었는지 검증.

    REV.02: raw_sections.json 기반으로 원본 조회.
    notes 뿐만 아니라 테이블의 notes_in_table도 매칭 대상에 포함.
    """
    total_note_sections = 0
    matched_sections = 0
    missed = []
    groups = _group_chunks_by_section(chunks)

    for (section_id, src_file), group_chunks in groups.items():
        original = get_section_raw_text(section_id, src_file)
        if not original:
            continue

        note_markers = ["[주]", "〔주〕", "[注]", "【주】"]
        has_note_in_original = any(m in original for m in note_markers)
        has_circled_num = bool(re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]", original))

        if has_note_in_original or has_circled_num:
            total_note_sections += 1
            # notes 필드 또는 테이블의 notes_in_table 중 어느 하나라도 있으면 매칭
            any_notes = any(c.get("notes", []) for c in group_chunks)
            any_table_notes = any(
                note
                for c in group_chunks
                for t in c.get("tables", [])
                for note in t.get("notes_in_table", [])
            )
            # 청크 text에 주석 마커가 직접 포함된 경우도 매칭
            chunk_texts = " ".join(c.get("text", "") for c in group_chunks)
            any_in_text = (
                any(m in chunk_texts for m in note_markers)
                or bool(re.search(r"[①②③④⑤⑥⑦⑧⑨⑩]", chunk_texts))
            )
            if any_notes or any_table_notes or any_in_text:
                matched_sections += 1
            else:
                missed.append({
                    "section_id": section_id,
                    "source_file": src_file,
                    "chunk_count": len(group_chunks),
                })

    recall = matched_sections / total_note_sections if total_note_sections > 0 else 1.0
    threshold = DEEP_CHECK_THRESHOLDS["notes_recall_min"]
    severity = "PASS" if recall >= threshold else ("WARN" if recall >= 0.85 else "FAIL")

    return {
        "check": "D5_notes_recall",
        "severity": severity,
        "total_note_sections": total_note_sections,
        "matched_sections": matched_sections,
        "recall": round(recall, 4),
        "threshold": threshold,
        "missed": missed[:20],
        "missed_count": len(missed),
    }


# ─── D6: 숫자 보존 정확도 ───────────────────────────────────

def check_d6_numeric_preservation(chunks: list[dict]) -> dict:
    """원본의 수치 토큰이 청크에 보존되었는지 검증.

    REV.02: raw_sections.json 기반으로 원본 조회.
    notes_in_table의 텍스트도 비교 대상에 포함.
    """
    results = []
    failures = []
    groups = _group_chunks_by_section(chunks)

    for (section_id, src_file), group_chunks in groups.items():
        original = get_section_raw_text(section_id, src_file)
        if not original:
            continue

        orig_numbers = extract_numbers(original)
        if not orig_numbers:
            continue

        # 섹션 내 모든 청크의 텍스트+테이블(헤더+행+주석) 합산
        combined = ""
        for c in group_chunks:
            chunk_text = c.get("text", "")
            # 청크 텍스트에 남은 HTML 태그 제거 (파싱 안 된 잔여 HTML)
            if "<" in chunk_text:
                chunk_text = strip_html_for_numbers(chunk_text)
            combined += " " + chunk_text
            for t in c.get("tables", []):
                # 헤더도 포함 (헤더에 숫자가 있을 수 있음)
                for header in t.get("headers", []):
                    combined += " " + str(header)
                for row in t.get("rows", []):
                    combined += " " + " ".join(str(v) for v in row.values())
                # notes_in_table도 포함
                for note in t.get("notes_in_table", []):
                    combined += " " + note

        preserved = 0
        for num in orig_numbers:
            core_num = re.search(r"[\d,.]+", num)
            if not core_num:
                continue
            core = core_num.group()
            if _match_number_in_text(core, combined):
                preserved += 1
        rate = preserved / len(orig_numbers) if orig_numbers else 1.0
        results.append(rate)

        if rate < DEEP_CHECK_THRESHOLDS["numeric_preservation_min"]:
            failures.append({
                "section_id": section_id,
                "source_file": src_file,
                "preservation_rate": round(rate, 3),
                "total_numbers": len(orig_numbers),
                "preserved": preserved,
                "chunk_count": len(group_chunks),
            })

    avg_rate = sum(results) / len(results) if results else 0
    pass_count = sum(1 for r in results if r >= DEEP_CHECK_THRESHOLDS["numeric_preservation_min"])
    pass_rate = pass_count / len(results) if results else 0

    severity = "PASS" if pass_rate >= 0.95 else ("WARN" if pass_rate >= 0.85 else "FAIL")

    return {
        "check": "D6_numeric_preservation",
        "severity": severity,
        "checked_sections": len(results),
        "avg_preservation": round(avg_rate, 4),
        "pass_count": pass_count,
        "pass_rate": round(pass_rate, 4),
        "threshold": DEEP_CHECK_THRESHOLDS["numeric_preservation_min"],
        "failures": failures[:20],
        "failure_count": len(failures),
    }


# ─── D7: 부문 간 데이터 격리 ────────────────────────────────

def check_d7_department_isolation(chunks: list[dict]) -> dict:
    """같은 base section_id가 다른 부문에서 사용될 때 #접미사로 분리되었는지 검증"""
    id_dept_map = defaultdict(set)
    for chunk in chunks:
        base_id = chunk["section_id"].split("#")[0]
        dept = chunk["department"]
        id_dept_map[base_id].add(dept)

    multi_dept_ids = {bid: depts for bid, depts in id_dept_map.items() if len(depts) > 1}

    contaminated = []
    for base_id, depts in multi_dept_ids.items():
        full_ids = defaultdict(set)
        for chunk in chunks:
            if chunk["section_id"].split("#")[0] == base_id:
                full_ids[chunk["department"]].add(chunk["section_id"])

        all_full_ids = []
        for dept, ids in full_ids.items():
            for fid in ids:
                all_full_ids.append((fid, dept))

        seen = {}
        for fid, dept in all_full_ids:
            if fid in seen and seen[fid] != dept:
                contaminated.append({
                    "section_id": fid,
                    "departments": [seen[fid], dept],
                })
            seen[fid] = dept

    severity = "PASS" if not contaminated else "FAIL"

    return {
        "check": "D7_department_isolation",
        "severity": severity,
        "multi_dept_base_ids": len(multi_dept_ids),
        "contaminated_count": len(contaminated),
        "contaminated": contaminated[:20],
        "multi_dept_details": {
            bid: sorted(list(depts)) for bid, depts in list(multi_dept_ids.items())[:10]
        },
    }


# ─── 메인 실행 ─────────────────────────────────────────────

def run_deep_check(sample_mode: bool = False) -> dict:
    """Tier 2 심화 품질 검증 실행"""
    print("\n" + "=" * 60)
    print("Tier 2: 심화 품질 검증 (Deep Quality Check) REV.02")
    print("=" * 60)

    chunks_data = load_json(CHUNKS_FILE)
    chunks = chunks_data["chunks"]
    print(f"  입력 청크: {len(chunks)}개")

    if sample_mode:
        import random
        by_dept = defaultdict(list)
        for c in chunks:
            by_dept[c["department"]].append(c)
        sampled = []
        for dept, dept_chunks in by_dept.items():
            sampled.extend(random.sample(dept_chunks, min(20, len(dept_chunks))))
        chunks = sampled
        print(f"  샘플 모드: {len(chunks)}개 선택")

    # raw_sections.json 사전 로드
    print("  raw_sections.json 로드 중...")
    _load_raw_sections()

    # 7개 검증 실행
    print("\n  검증 실행 중...")

    print("    D1: 원본 텍스트 충실도...")
    d1 = check_d1_text_fidelity(chunks)
    print(f"      → [{d1['severity']}] avg_containment={d1['avg_containment']:.3f}, pass={d1['pass_rate']:.1%}")

    print("    D2: 테이블 파싱 정확도...")
    d2 = check_d2_table_accuracy(chunks)
    print(f"      → [{d2['severity']}] avg={d2['avg_accuracy']:.3f}, pass={d2['pass_rate']:.1%}")

    print("    D3: 섹션 경계 정확성...")
    d3 = check_d3_section_boundary(chunks)
    print(f"      → [{d3['severity']}] 혼입={d3['contaminated_count']}건")

    print("    D4: 교차참조 무결성...")
    d4 = check_d4_crossref_validity(chunks)
    print(f"      → [{d4['severity']}] {d4['valid_refs']}/{d4['total_refs']} 유효")

    print("    D5: 주석 추출 정확도...")
    d5 = check_d5_notes_recall(chunks)
    print(f"      → [{d5['severity']}] recall={d5['recall']:.1%} ({d5['matched_sections']}/{d5['total_note_sections']})")

    print("    D6: 숫자 보존 정확도...")
    d6 = check_d6_numeric_preservation(chunks)
    print(f"      → [{d6['severity']}] avg={d6['avg_preservation']:.3f}, pass={d6['pass_rate']:.1%}")

    print("    D7: 부문 간 데이터 격리...")
    d7 = check_d7_department_isolation(chunks)
    print(f"      → [{d7['severity']}] 교차오염={d7['contaminated_count']}건")

    checks = [d1, d2, d3, d4, d5, d6, d7]

    severities = [c["severity"] for c in checks]
    if "FAIL" in severities:
        overall = "FAIL"
    elif "WARN" in severities:
        overall = "WARN"
    else:
        overall = "PASS"

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overall_severity": overall,
        "sample_mode": sample_mode,
        "total_chunks_checked": len(chunks),
        "checks": checks,
        "summary": {
            c["check"]: c["severity"] for c in checks
        },
    }

    print(f"\n  {'='*50}")
    print(f"  전체 판정: [{overall}]")
    print(f"  {'='*50}")
    for c in checks:
        icon = "[OK]" if c["severity"] == "PASS" else ("[!!]" if c["severity"] == "WARN" else "[XX]")
        print(f"    {icon} {c['check']}: {c['severity']}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEEP_CHECK_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  저장: {DEEP_CHECK_REPORT_FILE}")

    return report


if __name__ == "__main__":
    sample = "--sample" in sys.argv
    run_deep_check(sample_mode=sample)
