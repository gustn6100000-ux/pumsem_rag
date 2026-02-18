"""Step 2: HTML 테이블 파서

각 섹션 내 <table>...</table> HTML을 구조화된 딕셔너리 배열로 변환.
rowspan/colspan 처리, 테이블 유형 분류, 테이블 내 주석 분리.
"""
import json
import re
from pathlib import Path
from tqdm import tqdm

from config import (
    RAW_SECTIONS_FILE, PARSED_TABLES_FILE, OUTPUT_DIR, TABLE_TYPE_KEYWORDS,
)
from utils.html_utils import (
    expand_table, extract_tables_from_text, remove_tables_from_text,
)
from bs4 import BeautifulSoup


def classify_table(headers: list[str], rows: list[list[str]]) -> str:
    """테이블 유형을 판별.

    Returns:
        "A_품셈" | "B_규모기준" | "C_구분설명" | "D_기타"
    """
    header_text = " ".join(headers).lower()

    # Type A: 품셈 테이블 (수량, 단위 등)
    a_keywords = TABLE_TYPE_KEYWORDS["A_품셈"]
    if sum(1 for kw in a_keywords if kw in header_text) >= 2:
        return "A_품셈"

    # Type A 보강: 전치 테이블 감지 (행=인력, 열=WorkType)
    # Why: 잡철물 제작 등 테이블에서 행 첫 열이 인력명(철공, 용접공, 인부)이고
    #      헤더가 WorkType명(제품 설치_일반철재 등)인 패턴은 헤더 키워드만으로는
    #      A_품셈을 감지할 수 없으므로, 행 데이터의 첫 열 값도 검사한다.
    if rows and len(rows) >= 2:
        _LABOR_ROW_KEYWORDS = [
            "인부", "철공", "용접공", "배관공", "기사", "기능공", "기능사",
            "조공", "내장공", "도장공", "미장공", "목공", "방수공",
            "보통인부", "특별인부", "잡역부",
        ]
        labor_row_count = 0
        for row in rows:
            # list[str] 형이든 list[dict] 형이든 첫 번째 값 추출
            if isinstance(row, dict):
                first_val = str(list(row.values())[0]).replace(" ", "") if row else ""
            elif isinstance(row, (list, tuple)):
                first_val = str(row[0]).replace(" ", "") if row else ""
            else:
                first_val = ""
            if any(kw in first_val for kw in _LABOR_ROW_KEYWORDS):
                labor_row_count += 1
        if labor_row_count >= 2:
            return "A_품셈"

    # Type B: 규모 기준 테이블
    b_keywords = TABLE_TYPE_KEYWORDS["B_규모기준"]
    if any(kw in header_text for kw in b_keywords):
        return "B_규모기준"

    # Type C: 구분/내용 2열 테이블
    c_keywords = TABLE_TYPE_KEYWORDS["C_구분설명"]
    if len(headers) == 2 and any(kw in header_text for kw in c_keywords):
        return "C_구분설명"

    return "D_기타"


def _is_header_like_row(row: list[str]) -> bool:
    """행이 헤더처럼 보이는지 판정 (숫자가 아닌 텍스트 비율이 높으면 헤더)"""
    if not row:
        return False
    non_numeric = sum(1 for v in row if v and not re.match(r'^[\d,.\-\s]*$', v))
    return non_numeric > len(row) * 0.5


def detect_header_rows(grid: list[list[str]]) -> int:
    """헤더 행 수를 추정 (1~3행 지원)"""
    if not grid or len(grid) < 2:
        return 1

    first_row = grid[0]
    has_dup_first = len(set(first_row)) < len(first_row)

    # 3행 헤더 체크: 첫 행에 중복 있고, 2~3행 모두 헤더 같으면 3행
    if has_dup_first and len(grid) >= 4:
        second_row = grid[1]
        third_row = grid[2]
        fourth_row = grid[3]
        if (_is_header_like_row(second_row) and _is_header_like_row(third_row)
                and not _is_header_like_row(fourth_row)):
            return 3

    # 2행 헤더 체크
    if has_dup_first and len(grid) >= 3:
        second_row = grid[1]
        if _is_header_like_row(second_row):
            return 2

    return 1


def build_composite_headers(grid: list[list[str]], n_header_rows: int) -> list[str]:
    """다중 헤더 행을 합쳐서 단일 헤더 리스트로 만듦"""
    if n_header_rows == 1:
        return [h.strip() for h in grid[0]]

    headers = []
    n_cols = len(grid[0])
    for c in range(n_cols):
        parts = []
        for r in range(n_header_rows):
            val = grid[r][c].strip() if r < len(grid) and c < len(grid[r]) else ""
            if val and val not in parts:
                parts.append(val)
        headers.append("_".join(parts) if len(parts) > 1 else (parts[0] if parts else ""))
    return headers


def is_note_row(row: list[str], total_cols: int = 0) -> bool:
    """테이블 내 주석/비고/구분자 행인지 판별"""
    text = " ".join(row).strip()
    if not text:
        return False

    non_empty = [c for c in row if c.strip()]

    # [주], 〔주〕, 【주】 패턴
    if re.search(r'\[주\]|〔주〕|【주】', text):
        return True
    # ①②③ 번호 패턴으로 시작
    if re.search(r'^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮]', text):
        return True
    # ㉮㉯㉰ 원문자 후속 주석
    if re.search(r'^[㉮㉯㉰㉱㉲㉳㉴㉵㉶㉷]', text):
        return True
    # "- " 또는 "– " 시작 설명 행
    if text.startswith("- ") or text.startswith("– "):
        return True
    # "비 고" / "비고" 시작 행
    if re.match(r'^비\s*고', text):
        return True

    # 단일 셀 행 (colspan 전체 병합 행) 판별
    if len(non_empty) == 1:
        cell_text = non_empty[0]
        # 긴 텍스트 단일셀 → 주석행
        if len(cell_text) > 50:
            return True
        # 볼드 구분자 행: "(0602) 덤프트럭" 같은 기종 제목
        if re.match(r'^\(\d{4}\)\s*.+', cell_text):
            return True
        # 섹션 제목 패턴: "6-3-10 신축이음 설치" 형태
        if re.match(r'^\d+-\d+(?:-\d+)?\s+.{4,}', cell_text):
            return True

    return False


def parse_single_table(html: str, section_id: str, table_idx: int) -> dict:
    """단일 HTML 테이블을 구조화된 딕셔너리로 변환.

    빈 tbody 테이블도 헤더만으로 결과를 반환 (None 대신).
    """
    soup = BeautifulSoup(html, "lxml")
    table_tag = soup.find("table")
    if not table_tag:
        return None

    grid = expand_table(table_tag)
    if not grid:
        return None

    table_id = f"T-{section_id}-{table_idx:02d}"

    # 빈 tbody: 헤더만 있는 테이블 (grid가 1행이거나 데이터 행이 없는 경우)
    if len(grid) < 2:
        headers = [h.strip() for h in grid[0]]
        return {
            "table_id": table_id,
            "type": classify_table(headers, []),
            "headers": headers,
            "rows": [],
            "notes_in_table": [],
            "raw_row_count": 0,
            "parsed_row_count": 0,
        }

    # 헤더 행 수 결정
    n_header_rows = detect_header_rows(grid)
    headers = build_composite_headers(grid, n_header_rows)

    n_cols = len(headers)

    # 데이터 행 추출 (주석 행 분리)
    data_rows = []
    note_rows = []
    for row in grid[n_header_rows:]:
        if is_note_row(row, n_cols):
            note_rows.append(" ".join(c for c in row if c.strip()))
        else:
            data_rows.append(row)

    # 테이블 유형 분류
    table_type = classify_table(headers, data_rows)

    # 행을 딕셔너리로 변환
    rows_as_dicts = []
    for row in data_rows:
        row_dict = {}
        for j, header in enumerate(headers):
            val = row[j] if j < len(row) else ""
            # 숫자 값 변환 시도
            val = try_numeric(val)
            key = header if header else f"col_{j}"
            row_dict[key] = val
        # 모든 값이 비어있는 행 제거
        if any(v for v in row_dict.values() if v != "" and v is not None):
            rows_as_dicts.append(row_dict)

    return {
        "table_id": table_id,
        "type": table_type,
        "headers": headers,
        "rows": rows_as_dicts,
        "notes_in_table": note_rows,
        "raw_row_count": len(grid) - n_header_rows,
        "parsed_row_count": len(rows_as_dicts),
    }


def try_numeric(val: str):
    """문자열을 숫자로 변환 시도.

    후행 0이 있는 소수 (예: "0.10")는 정밀도 보존을 위해 문자열로 유지.
    """
    if not isinstance(val, str):
        return val
    val_stripped = val.strip().replace(",", "")
    if not val_stripped:
        return val
    try:
        if "." in val_stripped:
            # 후행 0 체크: "0.10", "3.50" 등은 문자열 유지
            decimal_part = val_stripped.split(".")[-1]
            if decimal_part.endswith("0"):
                # 유효한 숫자인지만 확인
                float(val_stripped)
                return val_stripped
            return float(val_stripped)
        return int(val_stripped)
    except ValueError:
        return val


def process_section_tables(section: dict) -> dict:
    """섹션의 raw_text에서 테이블을 파싱하고, 테이블 제거된 텍스트를 반환"""
    raw_text = section.get("raw_text", "")
    section_id = section.get("section_id", "unknown")

    # HTML 테이블 추출
    table_htmls = extract_tables_from_text(raw_text)

    parsed_tables = []
    for idx, table_info in enumerate(table_htmls, 1):
        result = parse_single_table(table_info["html"], section_id, idx)
        if result:
            parsed_tables.append(result)

    # 테이블 제거된 텍스트
    text_without_tables = remove_tables_from_text(raw_text)

    return {
        **section,
        "tables": parsed_tables,
        "text_without_tables": text_without_tables,
        "table_count": len(parsed_tables),
        "table_html_count": len(table_htmls),
    }


def run_step2(input_file: Path = None) -> list[dict]:
    """Step 2 실행: 테이블 파싱"""
    print("\n" + "=" * 60)
    print("Step 2: HTML 테이블 파서")
    print("=" * 60)

    input_file = input_file or RAW_SECTIONS_FILE
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    sections = data["sections"]
    print(f"  입력 섹션: {len(sections)}개")

    results = []
    total_tables = 0
    total_html_tables = 0
    failed_tables = 0

    for section in tqdm(sections, desc="  테이블 파싱"):
        processed = process_section_tables(section)
        results.append(processed)
        total_tables += processed["table_count"]
        total_html_tables += processed["table_html_count"]
        failed_tables += processed["table_html_count"] - processed["table_count"]

    print(f"\n  결과:")
    print(f"    발견된 HTML 테이블: {total_html_tables}개")
    print(f"    파싱 성공: {total_tables}개")
    print(f"    파싱 실패: {failed_tables}개")
    if total_html_tables > 0:
        print(f"    성공률: {total_tables/total_html_tables*100:.1f}%")

    # 테이블 유형별 통계
    type_counts = {}
    for sec in results:
        for t in sec["tables"]:
            tt = t["type"]
            type_counts[tt] = type_counts.get(tt, 0) + 1
    print(f"    유형별:")
    for tt, count in sorted(type_counts.items()):
        print(f"      {tt}: {count}개")

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(PARSED_TABLES_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                **data["metadata"],
                "total_html_tables": total_html_tables,
                "total_parsed_tables": total_tables,
                "failed_tables": failed_tables,
            },
            "sections": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n  저장: {PARSED_TABLES_FILE}")
    return results


if __name__ == "__main__":
    run_step2()
