"""Step 3: 텍스트 정제 & 주석/비고 추출

테이블 외 텍스트에서 주석, 조건/할증, 교차참조, 보완연도 등 구조화된 정보 추출.
"""
import json
import re
from pathlib import Path
from tqdm import tqdm

from config import (
    PARSED_TABLES_FILE, CLEANED_SECTIONS_FILE, OUTPUT_DIR, PATTERNS,
)


def extract_notes(text: str) -> tuple[list[str], str]:
    """[주] 블록과 번호 매긴 주석을 추출.

    Returns:
        (notes 리스트, 주석 제거된 텍스트)
    """
    notes = []
    remaining_text = text

    # [주] 블록 찾기
    note_block_pattern = re.compile(
        r'\[주\]\s*\n(.*?)(?=\n\n(?!\s*[①②③④⑤⑥⑦⑧⑨⑩])|\n(?=\d+-\d+)|\Z)',
        re.DOTALL
    )
    matches = list(note_block_pattern.finditer(text))

    if matches:
        for m in matches:
            block_text = m.group(1).strip()
            # 개별 주석 항목 분리
            items = re.split(r'(?=[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮])', block_text)
            for item in items:
                item = item.strip()
                if item:
                    # 번호 기호 제거
                    item = re.sub(r'^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]\s*', '', item).strip()
                    if item:
                        notes.append(item)

        # [주] 블록 전체 제거
        remaining_text = note_block_pattern.sub('', remaining_text)
        # "[주]" 단독 행도 제거
        remaining_text = re.sub(r'^\[주\]\s*$', '', remaining_text, flags=re.MULTILINE)

    return notes, remaining_text.strip()


def extract_conditions(text: str) -> list[dict]:
    """조건/할증 정보 추출"""
    conditions = []

    # 패턴 1: "~경우 본 시공량을 X%까지 감/가산"
    pattern1 = re.compile(
        r'(.{5,60}?(?:경우|때|시))\s*.*?(\d+)%.*?(가산|감산|감하여|증|할증)',
        re.DOTALL
    )
    for m in pattern1.finditer(text):
        cond_type = m.group(3)
        if "감" in cond_type:
            cond_type = "감산"
        elif "증" in cond_type or "가산" in cond_type:
            cond_type = "가산"
        conditions.append({
            "type": cond_type,
            "condition": m.group(1).strip(),
            "rate": f"{m.group(2)}%",
        })

    # 패턴 2: "~할 때 X% 할증" (별도 패턴)
    pattern2 = re.compile(r'(\d+)%\s*(할증|가산|감산|증감)')
    for m in pattern2.finditer(text):
        # 이미 위에서 잡힌 것과 중복 확인
        rate = f"{m.group(1)}%"
        if not any(c["rate"] == rate for c in conditions):
            conditions.append({
                "type": m.group(2),
                "condition": text[max(0, m.start()-30):m.start()].strip(),
                "rate": rate,
            })

    return conditions


def extract_cross_references(text: str) -> list[dict]:
    """교차참조 추출: "제X장", "X-X-X 참조" 등"""
    refs = []

    pattern = PATTERNS["cross_ref"]
    for m in pattern.finditer(text):
        chapter = m.group(1)
        section_id = m.group(2)
        context = text[max(0, m.start()-20):min(len(text), m.end()+20)].strip()
        refs.append({
            "target_section_id": section_id,
            "target_chapter": f"제{chapter}장" if chapter else "",
            "context": context,
        })

    return refs


def extract_revision_year(text: str) -> str:
    """보완연도 추출: ('24년 보완), ('02, '22년 보완)"""
    m = PATTERNS["revision"].search(text)
    if m:
        year = m.group(1)
        # 2자리 연도를 4자리로 변환
        if len(year) == 2:
            year = f"20{year}" if int(year) < 50 else f"19{year}"
        return year
    return ""


def extract_unit_basis(text: str) -> str:
    """단위 기준 추출: (일당), (m³당), (100m당)"""
    m = PATTERNS["unit_basis"].search(text)
    return m.group(1) if m else ""


def clean_text(text: str) -> str:
    """텍스트 정제: HTML 주석 제거, 빈 줄 정리, 마커 제거"""
    # HTML 주석 태그 제거 (<!-- ... -->)
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    # 장 제목 텍스트 제거 (메타데이터에 이미 포함)
    text = PATTERNS["chapter_title"].sub('', text)

    # 빈 줄 정리 (3줄 이상 → 2줄)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 앞뒤 공백 정리
    text = text.strip()

    return text


def remove_duplicate_notes(notes: list[str], table_notes: list[str]) -> list[str]:
    """테이블 안팎에 중복되는 주석 제거"""
    if not table_notes:
        return notes

    # 테이블 내 주석과 유사한 주석 제거 (70% 이상 겹치면 중복으로 판단)
    unique_notes = []
    for note in notes:
        note_clean = re.sub(r'\s+', '', note)
        is_dup = False
        for tn in table_notes:
            tn_clean = re.sub(r'\s+', '', tn)
            # 짧은 쪽 기준으로 포함 여부 확인
            shorter = note_clean if len(note_clean) < len(tn_clean) else tn_clean
            longer = tn_clean if len(note_clean) < len(tn_clean) else note_clean
            if shorter and shorter in longer:
                is_dup = True
                break
        if not is_dup:
            unique_notes.append(note)

    return unique_notes


def process_section(section: dict) -> dict:
    """개별 섹션의 텍스트 정제 및 정보 추출"""
    text = section.get("text_without_tables", section.get("raw_text", ""))

    # 보완연도 (원본 텍스트에서 추출)
    revision_year = extract_revision_year(text)

    # 단위 기준
    unit_basis = extract_unit_basis(text)

    # 주석 추출
    notes, text_after_notes = extract_notes(text)

    # 테이블 내 주석과 중복 제거
    table_notes = []
    for t in section.get("tables", []):
        table_notes.extend(t.get("notes_in_table", []))
    notes = remove_duplicate_notes(notes, table_notes)

    # 조건/할증 추출 (주석 제거 전 텍스트에서)
    conditions = extract_conditions(text)

    # 교차참조 추출
    cross_references = extract_cross_references(text)

    # 텍스트 정제
    clean = clean_text(text_after_notes)

    return {
        "section_id": section["section_id"],
        "title": section["title"],
        "department": section["department"],
        "chapter": section["chapter"],
        "page": section["page"],
        "source_file": section["source_file"],
        "toc_title": section.get("toc_title", ""),
        "toc_section": section.get("toc_section", ""),
        "has_content": section.get("has_content", True),

        "clean_text": clean,
        "tables": section.get("tables", []),
        "notes": notes,
        "conditions": conditions,
        "cross_references": cross_references,
        "revision_year": revision_year,
        "unit_basis": unit_basis,
    }


def run_step3(input_file: Path = None) -> list[dict]:
    """Step 3 실행: 텍스트 정제 & 정보 추출"""
    print("\n" + "=" * 60)
    print("Step 3: 텍스트 정제 & 주석/비고 추출")
    print("=" * 60)

    input_file = input_file or PARSED_TABLES_FILE
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    sections = data["sections"]
    print(f"  입력 섹션: {len(sections)}개")

    results = []
    total_notes = 0
    total_conditions = 0
    total_refs = 0
    sections_with_revision = 0

    for section in tqdm(sections, desc="  텍스트 정제"):
        processed = process_section(section)
        results.append(processed)
        total_notes += len(processed["notes"])
        total_conditions += len(processed["conditions"])
        total_refs += len(processed["cross_references"])
        if processed["revision_year"]:
            sections_with_revision += 1

    print(f"\n  결과:")
    print(f"    추출된 주석: {total_notes}개")
    print(f"    추출된 조건/할증: {total_conditions}개")
    print(f"    추출된 교차참조: {total_refs}개")
    print(f"    보완연도 있는 섹션: {sections_with_revision}개")

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CLEANED_SECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                **data["metadata"],
                "total_notes": total_notes,
                "total_conditions": total_conditions,
                "total_cross_references": total_refs,
            },
            "sections": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n  저장: {CLEANED_SECTIONS_FILE}")
    return results


if __name__ == "__main__":
    run_step3()
