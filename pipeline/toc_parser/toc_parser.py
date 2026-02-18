"""
목차 파서 모듈 (v2)
- 2자리 장번호(10~13) OCR 줄바꿈 깨짐 복원
- 부문간 섹션 ID 충돌 해결 (모든 항목 보존)
- 인라인 장 제목 분리 처리

사용법:
    from toc_parser import parse_toc
    section_map = parse_toc("목차_gemini.md")
"""

import re
from pathlib import Path


# ── 유틸리티 ─────────────────────────────────────────────

def _get_chapter_num(section_str: str) -> int:
    """섹션 문자열에서 장 번호 추출 (제7장 → 7, 제10장 → 10)"""
    m = re.search(r'제(\d+)장', section_str)
    return int(m.group(1)) if m else 0


def _normalize_section_name(name: str) -> str:
    """섹션명 정규화
    - "공 통" → "공통" (1글자씩 띄어쓰기된 것만 합침)
    - "지붕 및 홈통공사" → 유지 (정상 띄어쓰기)
    """
    m = re.match(r'(제\d+장)\s*(.*)', name)
    if m:
        prefix = m.group(1)
        rest = m.group(2).strip()
        # 모든 토큰이 1글자 한글인 경우만 공백 제거 ("공 통" → "공통")
        if rest and re.match(r'^[가-힣](\s+[가-힣])*$', rest):
            rest = re.sub(r'\s+', '', rest)
        return f"{prefix} {rest}"
    return name.strip()


def _split_line_at_chapter(line: str) -> list:
    """서브섹션 + 장 제목이 한 줄에 합쳐진 경우 분리
    예: "6-6-3 줄눈 설치··· 585 제7장 지붕 및 홈통공사 587"
      → ["6-6-3 줄눈 설치··· 585", "제7장 지붕 및 홈통공사 587"]
    """
    if not re.match(r'^\d+-', line):
        return [line]

    # 라인 끝에 "제N장 ..." 패턴이 있으면 분리
    m = re.search(
        r'\s+(제\d+장\s+[가-힣]+(?:\s+[가-힣]+)*\s+\d+)\s*$', line
    )
    if m:
        before = line[:m.start()].strip()
        chapter_part = m.group(1).strip()
        return [before, chapter_part] if before else [chapter_part]

    return [line]


def _fix_split_chapter_id(section_id: str, chapter_num: int) -> str:
    """2자리 장번호 ID 복원
    제10장 컨텍스트에서 "0-1-1" → "10-1-1"
    제11장 컨텍스트에서 "1-1-1" → "11-1-1" 등
    """
    if chapter_num < 10:
        return section_id

    first_num = int(section_id.split('-')[0])
    expected_remainder = chapter_num % 10

    # 첫 번째 숫자가 장번호의 나머지와 같고, 장번호 자체와 다르면 → 쪼개진 것
    if first_num == expected_remainder and first_num != chapter_num:
        prefix = str(chapter_num // 10)
        return prefix + section_id

    return section_id


# ── 메인 파서 ────────────────────────────────────────────

def parse_toc(toc_path: str) -> dict:
    """
    목차 파일을 파싱하여 섹션 매핑 사전 생성

    Returns:
        {
            "1-2-2": {"id": "1-2-2", "chapter": "공통부문", "section": "제1장 적용기준", ...},
            "7-1-1#2": {"id": "7-1-1", "chapter": "건축부문", "section": "제7장 지붕 및 홈통공사", ...},
            ...
        }
    """
    section_map = {}

    with open(toc_path, 'r', encoding='utf-8') as f:
        content = f.read()

    current_chapter = ""   # 공통부문, 토목부문, ...
    current_section = ""   # 제1장 적용기준, 제2장 가설공사, ...
    current_chapter_num = 0

    # ── 정규식 패턴 ──
    # 부문 + 장 (예: "공통부문 제1장 적용기준 3")
    chapter_section_pat = re.compile(
        r'(공통부문|토목부문|건축부문|기계설비부문|유지관리부문)\s+'
        r'(제\d+장\s+[가-힣\s]+)\s+(\d+)'
    )
    # 장만 (부문 없이, 예: "제2장 가설공사 34")
    section_pat = re.compile(r'(제\d+장\s+[가-힣\s]+?)\s+(\d+)\s*$')

    # 세부 섹션 (예: "1-2-2 단위표준···4")
    # (?:\s+\d+.*)? → 꼬리의 "29 목차"나 orphan "1" 무시
    subsection_pat = re.compile(
        r'^(\d+-\d+(?:-\d+)?)\s+(.+?)[\s\u00b7\u2024\u2027·.]+(\d+)(?:\s+\d+.*)?$'
    )

    lines = content.split('\n')

    for line in lines:
        line = line.strip()
        if not line or line.startswith('<!--'):
            continue

        # 전처리: "목 차" 제거, "NN 목차" 꼬리 제거
        line = re.sub(r'^목\s*차\s*', '', line)
        line = re.sub(r'\s+\d+\s+목차\s*$', '', line)

        if not line.strip():
            continue

        # 서브섹션 + 인라인 장 제목 분리
        parts = _split_line_at_chapter(line)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # (1) 부문 + 장 패턴
            m = chapter_section_pat.search(part)
            if m:
                current_chapter = m.group(1)
                current_section = _normalize_section_name(m.group(2))
                current_chapter_num = _get_chapter_num(current_section)
                continue

            # (2) 장 패턴 (서브섹션 시작이 아닌 줄만)
            m = section_pat.search(part)
            if m and not re.match(r'^\d+-', part):
                current_section = _normalize_section_name(m.group(1))
                current_chapter_num = _get_chapter_num(current_section)
                continue

            # (3) 세부 섹션
            m = subsection_pat.search(part)
            if m:
                section_id = m.group(1)
                title_raw = m.group(2).strip()
                page_num = int(m.group(3))

                # [FIX] 2자리 장번호 ID 복원 (0-1-1 → 10-1-1)
                section_id = _fix_split_chapter_id(section_id, current_chapter_num)

                # 제목 정리
                title = re.sub(r'[·\u00b7\u2024\u2027.]+.*$', '', title_raw).strip()
                title = re.sub(r'\s+\d+\s*$', '', title).strip()

                if not section_id or not title:
                    continue

                # [FIX] ID 충돌 시 고유 키 생성 (원본 ID는 value에 보존)
                key = section_id
                counter = 1
                while key in section_map:
                    counter += 1
                    key = f"{section_id}#{counter}"

                section_map[key] = {
                    "id": section_id,
                    "chapter": current_chapter,
                    "section": current_section,
                    "title": title,
                    "page": page_num
                }

    return section_map


# ── 페이지 매핑 ──────────────────────────────────────────

def build_page_to_sections_map(section_map: dict) -> dict:
    """페이지 번호 → 해당 페이지에서 시작하는 섹션들 매핑"""
    page_map = {}

    for key, info in section_map.items():
        page_num = info.get("page", 0)
        if page_num > 0:
            if page_num not in page_map:
                page_map[page_num] = []
            page_map[page_num].append({
                "id": info.get("id", key),
                "chapter": info.get("chapter", ""),
                "section": info.get("section", ""),
                "title": info.get("title", "")
            })

    return page_map


def get_current_context(pdf_page_num: int, page_map: dict, last_context: dict = None) -> dict:
    """현재 PDF 페이지에 해당하는 부문/장/섹션 정보 반환"""
    context = last_context.copy() if last_context else {"chapter": "", "section": "", "sections": []}

    if pdf_page_num in page_map:
        sections = page_map[pdf_page_num]
        context["sections"] = sections
        if sections:
            context["chapter"] = sections[0].get("chapter", context.get("chapter", ""))
            context["section"] = sections[0].get("section", context.get("section", ""))
    else:
        context["sections"] = []

    return context


def get_active_section(pdf_page_num: int, section_map: dict) -> dict | None:
    """주어진 페이지에서 활성화된 섹션 반환 (가장 가까운 이전 섹션)"""
    if not section_map or pdf_page_num <= 0:
        return None

    candidates = []
    for key, info in section_map.items():
        page = info.get("page", 0)
        if 0 < page <= pdf_page_num:
            candidates.append({
                "id": info.get("id", key),
                "chapter": info.get("chapter", ""),
                "section": info.get("section", ""),
                "title": info.get("title", ""),
                "page": page
            })

    if not candidates:
        return None

    candidates.sort(key=lambda x: (x["page"], x["id"]))
    return candidates[-1]


# ── 기타 유틸리티 ────────────────────────────────────────

def get_section_info(section_id: str, section_map: dict) -> str:
    """섹션 ID에 대한 구조 정보 문자열 반환"""
    # 직접 키 조회
    info = section_map.get(section_id)
    # 없으면 원본 ID로 검색 (충돌 키 대응)
    if not info:
        for key, val in section_map.items():
            if val.get("id") == section_id:
                info = val
                break
    if not info:
        return ""

    parts = []
    if info.get("chapter"):
        parts.append(info["chapter"])
    if info.get("section"):
        parts.append(info["section"])
    if section_id and info.get("title"):
        parts.append(f"{section_id} {info['title']}")
    return " > ".join(parts)


def parse_toc_file(toc_path: str) -> dict:
    """step1_extract_gemini.py에서 호출하는 래퍼"""
    return parse_toc(toc_path)


def inject_section_markers(text: str, section_map: dict) -> str:
    """텍스트에서 섹션 ID를 감지하고 구조 정보 주석 삽입"""
    if not section_map:
        return text

    section_pattern = re.compile(r'^(\d+-\d+-\d+)\s+', re.MULTILINE)

    def replace_with_marker(match):
        sid = match.group(1)
        info_str = get_section_info(sid, section_map)
        if info_str:
            return f"\n<!-- SECTION: {info_str} -->\n{match.group(0)}"
        return match.group(0)

    return section_pattern.sub(replace_with_marker, text)


# ── CLI ──────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("사용법: python toc_parser.py <목차파일경로>")
        sys.exit(1)

    toc_path = sys.argv[1]

    print(f"📖 목차 파싱 중: {toc_path}")
    section_map = parse_toc(toc_path)

    print(f"\n✅ {len(section_map)}개 섹션 파싱 완료:\n")

    # 부문별 통계
    chapters = {}
    for k, v in section_map.items():
        ch = v.get('chapter', 'UNKNOWN')
        chapters[ch] = chapters.get(ch, 0) + 1
    print("📊 부문별 섹션 수:")
    for ch, cnt in sorted(chapters.items()):
        print(f"  {ch}: {cnt}개")

    # 샘플 출력
    print(f"\n📋 처음 30개 섹션:")
    for i, (k, v) in enumerate(section_map.items()):
        if i >= 30:
            print(f"... 외 {len(section_map) - 30}개")
            break
        display_id = v.get("id", k)
        print(f"  [{k}] {v['chapter']} > {v['section']} > {display_id} {v['title']} (p.{v['page']})")
