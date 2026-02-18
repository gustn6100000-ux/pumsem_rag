"""HTML 파싱 유틸리티 - rowspan/colspan 전개 등"""
import html as html_module

from bs4 import BeautifulSoup, Tag
import re


def expand_table(table_tag: Tag) -> list[list[str]]:
    """HTML 테이블의 rowspan/colspan을 전개하여 2D 배열로 반환.

    Returns:
        list[list[str]]: 전개된 셀 값의 2D 배열.
        빈 tbody인 경우에도 thead 행은 포함하여 반환.
    """
    rows = table_tag.find_all("tr")
    if not rows:
        return []

    # 최대 열 수 추정
    max_cols = 0
    for row in rows:
        cols = 0
        for cell in row.find_all(["td", "th"]):
            colspan = int(cell.get("colspan", 1))
            cols += colspan
        max_cols = max(max_cols, cols)

    if max_cols == 0:
        return []

    # 2D 그리드 초기화 (None = 비어있음)
    grid = [[None] * max_cols for _ in range(len(rows))]

    for r_idx, row in enumerate(rows):
        col_idx = 0
        for cell in row.find_all(["td", "th"]):
            # 이미 채워진 셀(rowspan에 의해) 건너뛰기
            while col_idx < max_cols and grid[r_idx][col_idx] is not None:
                col_idx += 1
            if col_idx >= max_cols:
                break

            rowspan = int(cell.get("rowspan", 1))
            colspan = int(cell.get("colspan", 1))
            text = extract_cell_text(cell)

            for dr in range(rowspan):
                for dc in range(colspan):
                    nr, nc = r_idx + dr, col_idx + dc
                    if nr < len(grid) and nc < max_cols:
                        grid[nr][nc] = text

            col_idx += colspan

    # None을 빈 문자열로
    for r in range(len(grid)):
        for c in range(max_cols):
            if grid[r][c] is None:
                grid[r][c] = ""

    return grid


def extract_cell_text(cell: Tag) -> str:
    """셀 요소에서 텍스트 추출 (특수 인라인 태그 처리).

    <sup>, <sub> 태그를 ^, _ 표기로 변환한 뒤 텍스트를 추출한다.
    """
    inner = cell.decode_contents()
    # <sup>X</sup> → ^X (예: 10<sup>-7</sup> → 10^-7)
    inner = re.sub(r'<sup[^>]*>(.*?)</sup>', r'^\1', inner, flags=re.DOTALL)
    # <sub>X</sub> → _X
    inner = re.sub(r'<sub[^>]*>(.*?)</sub>', r'_\1', inner, flags=re.DOTALL)
    # <br>, <br/> → 공백
    inner = re.sub(r'<br\s*/?\s*>', ' ', inner, flags=re.IGNORECASE)
    # 나머지 HTML 태그 제거
    inner = re.sub(r'<[^>]+>', ' ', inner)
    # HTML 엔티티 디코딩 (&nbsp; → 공백, &amp; → & 등)
    inner = html_module.unescape(inner)
    return clean_cell_text(inner)


def clean_cell_text(text: str) -> str:
    """셀 텍스트 정제"""
    # 비파괴 공백(non-breaking space, \xa0) → 일반 공백
    text = text.replace('\xa0', ' ')
    # 연속 공백 정리
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_html_table(html: str) -> list[list[str]]:
    """HTML 문자열에서 테이블을 파싱하여 2D 배열로 반환"""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        return []
    return expand_table(table)


def extract_tables_from_text(text: str) -> list[dict]:
    """텍스트에서 모든 <table>...</table>을 추출.

    Returns:
        list[dict]: [{"html": str, "start": int, "end": int}, ...]
    """
    tables = []
    pattern = re.compile(r'<table[^>]*>.*?</table>', re.DOTALL | re.IGNORECASE)
    for m in pattern.finditer(text):
        tables.append({
            "html": m.group(),
            "start": m.start(),
            "end": m.end(),
        })
    return tables


def remove_tables_from_text(text: str) -> str:
    """텍스트에서 모든 <table>...</table> 제거"""
    return re.sub(r'<table[^>]*>.*?</table>', '', text, flags=re.DOTALL | re.IGNORECASE).strip()
