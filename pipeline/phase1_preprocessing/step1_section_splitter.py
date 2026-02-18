"""Step 1: MD 파일 로더 & 섹션 분할

41개 MD 파일을 읽어서 <!-- SECTION: --> 마커 기준으로 개별 섹션으로 분할.
toc_parsed.json과 매핑하여 메타데이터 보강.

핵심 로직: 연속 SECTION 마커 그룹 뒤의 텍스트를 섹션 제목 패턴으로 재분배.
"""
import json
import re
from pathlib import Path
from tqdm import tqdm

from config import (
    MD_DIR, TOC_FILE, RAW_SECTIONS_FILE, OUTPUT_DIR, PATTERNS, PILOT_FILES,
)


def load_toc(toc_path: Path) -> dict:
    """toc_parsed.json 로드"""
    with open(toc_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("section_map", data)


def build_reverse_map(toc: dict) -> dict:
    """(base_section_id, department) → toc_key 역방향 매핑 생성.

    toc_parsed.json의 접미사(#2, #3 등)는 부문별 고정이 아니라
    동일 섹션ID를 공유하는 부문의 등장 순서에 따라 동적으로 부여되므로,
    TOC 엔트리에서 직접 역방향 매핑을 구축한다.
    """
    reverse = {}
    for toc_key, entry in toc.items():
        base_id = entry.get("id", toc_key.split("#")[0])
        department = entry.get("chapter", "")
        reverse[(base_id, department)] = toc_key
    return reverse


def get_md_files(pilot_only: bool = False) -> list[Path]:
    """MD 파일 목록을 페이지 순서로 정렬하여 반환"""
    if pilot_only:
        files = [MD_DIR / name for name in PILOT_FILES if (MD_DIR / name).exists()]
    else:
        # OKOK, OKOKI 등 모든 변형 포함
        files = sorted(MD_DIR.glob("*OKOK*.md"))

    # 파일명에서 시작 페이지 번호로 정렬
    def sort_key(p: Path):
        m = re.search(r'(\d+)-\d+\s+OKOK', p.name)
        return int(m.group(1)) if m else 0

    return sorted(files, key=sort_key)


def parse_section_markers(text: str) -> list[dict]:
    """텍스트에서 SECTION 마커를 파싱하여 위치 정보와 함께 반환"""
    markers = []
    for m in PATTERNS["section_marker"].finditer(text):
        markers.append({
            "section_id": m.group(1),
            "title": m.group(2).strip(),
            "department": m.group(3).strip(),
            "chapter": m.group(4).strip(),
            "pos": m.start(),
            "end": m.end(),
        })
    return markers


def parse_page_markers(text: str) -> list[dict]:
    """텍스트에서 PAGE 마커를 파싱"""
    pages = []
    for m in PATTERNS["page_marker"].finditer(text):
        pages.append({
            "page": int(m.group(1)),
            "context": m.group(2).strip(),
            "pos": m.start(),
        })
    return pages


def get_page_for_position(page_markers: list[dict], pos: int, file_start_page: int) -> int:
    """특정 위치의 페이지 번호를 결정"""
    current_page = file_start_page
    for pm in page_markers:
        if pm["pos"] <= pos:
            current_page = pm["page"]
        else:
            break
    return current_page


def redistribute_text_to_sections(markers: list[dict], combined_text: str) -> dict:
    """연속 마커 그룹 뒤의 텍스트를 섹션 제목 패턴으로 각 섹션에 분배.

    Args:
        markers: 연속된 SECTION 마커 리스트
        combined_text: 마커 그룹 뒤의 전체 텍스트

    Returns:
        dict: {section_id: text_for_section}
    """
    if not markers or not combined_text.strip():
        return {m["section_id"]: "" for m in markers}

    if len(markers) == 1:
        return {markers[0]["section_id"]: combined_text}

    # 섹션 제목 패턴으로 텍스트 내 분할 지점 찾기
    # 예: "6-1 콘크리트", "6-1-1 레디믹스트콘크리트 타설"
    split_points = []  # [(position, section_id)]

    for marker in markers:
        sid = marker["section_id"]
        title = marker["title"]

        # 섹션 ID로 직접 매칭 (예: "6-1-1 레디믹스트콘크리트")
        # 공백 허용 패턴
        escaped_sid = re.escape(sid)
        pattern = re.compile(
            rf'^{escaped_sid}\s+',
            re.MULTILINE
        )
        m = pattern.search(combined_text)
        if m:
            split_points.append((m.start(), sid))
            continue

        # 제목으로 매칭 (제목 첫 부분)
        if title and len(title) >= 2:
            title_prefix = re.escape(title[:min(len(title), 8)])
            m = re.search(title_prefix, combined_text)
            if m:
                # 행 시작점으로 조정
                line_start = combined_text.rfind('\n', 0, m.start())
                line_start = line_start + 1 if line_start >= 0 else 0
                split_points.append((line_start, sid))

    # 분할 지점 정렬
    split_points.sort(key=lambda x: x[0])

    # 분할 지점이 없으면 전체 텍스트를 마지막 마커에 할당
    if not split_points:
        result = {m["section_id"]: "" for m in markers}
        result[markers[-1]["section_id"]] = combined_text
        return result

    # 분할 지점으로 텍스트 나누기
    result = {m["section_id"]: "" for m in markers}

    # 첫 분할 지점 이전 텍스트 처리
    if split_points[0][0] > 0:
        pre_text = combined_text[:split_points[0][0]].strip()
        if pre_text:
            # 장 제목이나 절 제목일 수 있음, 첫 섹션에 포함
            result[split_points[0][1]] = pre_text + "\n" if pre_text else ""

    for i, (pos, sid) in enumerate(split_points):
        if i + 1 < len(split_points):
            text = combined_text[pos:split_points[i + 1][0]].strip()
        else:
            text = combined_text[pos:].strip()
        # 기존 텍스트에 추가
        if result[sid]:
            result[sid] = result[sid] + "\n" + text
        else:
            result[sid] = text

    return result


def split_sections(text: str, source_file: str, toc: dict, reverse_map: dict) -> list[dict]:
    """하나의 MD 파일을 섹션 단위로 분할"""
    section_markers = parse_section_markers(text)
    page_markers = parse_page_markers(text)

    if not section_markers:
        return []

    file_start_page = page_markers[0]["page"] if page_markers else 0

    # 마커 그룹 식별: 연속된 마커 사이에 실질적 텍스트가 없으면 같은 그룹
    groups = []  # [(marker_list, text_after_group)]
    current_group = [section_markers[0]]

    for i in range(1, len(section_markers)):
        prev_marker = section_markers[i - 1]
        curr_marker = section_markers[i]

        # 이전 마커와 현재 마커 사이 텍스트
        between_text = text[prev_marker["end"]:curr_marker["pos"]]
        # 마커/공백 제거 후 실질적 텍스트 확인
        clean_between = PATTERNS["section_marker"].sub("", between_text)
        clean_between = PATTERNS["page_marker"].sub("", clean_between)
        clean_between = re.sub(r'<!-- CONTEXT:.*?-->', '', clean_between)
        clean_between = clean_between.strip()

        if len(clean_between) <= 10:
            # 텍스트 없음 → 같은 그룹
            current_group.append(curr_marker)
        else:
            # 텍스트 있음 → 이전 그룹 종료, 새 그룹 시작
            # 이전 그룹의 텍스트 = 마지막 마커 끝 ~ 현재 마커 시작
            group_text = text[current_group[-1]["end"]:curr_marker["pos"]].strip()
            groups.append((current_group, group_text))
            current_group = [curr_marker]

    # 마지막 그룹
    last_text = text[current_group[-1]["end"]:].strip()
    groups.append((current_group, last_text))

    # 각 그룹에서 텍스트 재분배
    sections = []
    for marker_group, group_text in groups:
        redistributed = redistribute_text_to_sections(marker_group, group_text)

        for marker in marker_group:
            # TOC 역방향 매핑으로 정확한 toc_key 결정
            dept = marker["department"]
            toc_key = reverse_map.get(
                (marker["section_id"], dept),
                marker["section_id"],
            )
            toc_entry = toc.get(toc_key, {})
            if not toc_entry:
                toc_entry = toc.get(marker["section_id"], {})

            page = get_page_for_position(page_markers, marker["pos"], file_start_page)

            section_text = redistributed.get(marker["section_id"], "")

            # 텍스트에서 마커 제거
            section_text = PATTERNS["section_marker"].sub("", section_text)
            section_text = PATTERNS["page_marker"].sub("", section_text)
            section_text = re.sub(r'<!-- CONTEXT:.*?-->', '', section_text)
            section_text = section_text.strip()

            sections.append({
                "section_id": toc_key,
                "title": marker["title"],
                "department": dept,
                "chapter": marker["chapter"],
                "page": page,
                "raw_text": section_text,
                "source_file": source_file,
                "toc_title": toc_entry.get("title", ""),
                "toc_section": toc_entry.get("section", ""),
                "has_content": len(section_text) > 10,
            })

    return sections


def context_marker_fallback(
    all_sections: list[dict],
    toc: dict,
    md_files: list[Path],
    reverse_map: dict,
) -> list[dict]:
    """미매핑 TOC 항목을 CONTEXT 마커에서 보충.

    CONTEXT 마커는 대부분 이미 SECTION으로 매핑된 섹션의 연속 페이지를
    표시하므로, **SECTION 마커로 잡히지 않은 섹션 ID**만 CONTEXT에서 추가한다.
    """
    detected_ids = set(s["section_id"] for s in all_sections)
    toc_ids = set(toc.keys())
    missing = toc_ids - detected_ids

    if not missing:
        return all_sections

    # 미매핑 섹션의 base_id 집합
    missing_base_ids = {}
    for toc_key in missing:
        entry = toc.get(toc_key, {})
        base_id = entry.get("id", toc_key.split("#")[0])
        missing_base_ids[base_id] = toc_key

    added = []
    pattern = PATTERNS["context_section_marker"]

    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8")
        page_markers = parse_page_markers(text)
        file_start_page = page_markers[0]["page"] if page_markers else 0

        for m in pattern.finditer(text):
            sid = m.group(1)
            if sid not in missing_base_ids:
                continue  # 이미 매핑된 섹션 → 스킵

            toc_key = missing_base_ids[sid]
            toc_entry = toc.get(toc_key, {})

            # CONTEXT 마커 위치 이후의 텍스트 추출
            ctx_end = m.end()
            # 다음 SECTION/CONTEXT 마커 또는 파일 끝까지
            next_marker = PATTERNS["section_marker"].search(text, ctx_end)
            next_ctx = pattern.search(text, ctx_end)

            end_pos = len(text)
            if next_marker:
                end_pos = min(end_pos, next_marker.start())
            if next_ctx:
                end_pos = min(end_pos, next_ctx.start())

            raw_text = text[ctx_end:end_pos]
            raw_text = PATTERNS["section_marker"].sub("", raw_text)
            raw_text = PATTERNS["page_marker"].sub("", raw_text)
            raw_text = re.sub(r'<!-- CONTEXT:.*?-->', '', raw_text)
            raw_text = raw_text.strip()

            if len(raw_text) <= 10:
                continue

            page = get_page_for_position(page_markers, m.start(), file_start_page)

            added.append({
                "section_id": toc_key,
                "title": m.group(2).strip(),
                "department": m.group(3).strip(),
                "chapter": m.group(4).strip(),
                "page": page,
                "raw_text": raw_text,
                "source_file": md_file.name,
                "toc_title": toc_entry.get("title", ""),
                "toc_section": toc_entry.get("section", ""),
                "has_content": True,
                "context_marker_matched": True,
            })

            # 해당 ID는 처리 완료 → 더 이상 검색하지 않음
            del missing_base_ids[sid]
            if not missing_base_ids:
                break
        if not missing_base_ids:
            break

    if added:
        print(f"    CONTEXT 마커 폴백 추가: {len(added)}개 섹션")
        for s in added:
            print(f"      {s['section_id']}: {s['title']} ({s['source_file']})")

    return all_sections + added


def fallback_title_matching(sections: list[dict], toc: dict) -> list[dict]:
    """SECTION 마커가 없는 TOC 항목을 기존 섹션 텍스트 내 제목 패턴으로 찾아 추가 분할.

    기존 섹션의 raw_text에서 "X-Y-Z 제목" 패턴을 찾아 미매칭 TOC 항목을 추출.
    """
    detected_ids = set(s["section_id"] for s in sections)
    toc_ids = set(toc.keys())
    unmatched = toc_ids - detected_ids

    if not unmatched:
        return sections

    # 미매칭 항목을 부문별로 정리: {department: [(toc_key, base_id, title), ...]}
    unmatched_by_dept = {}
    for toc_key in unmatched:
        toc_entry = toc.get(toc_key, {})
        base_id = toc_entry.get("id", toc_key.split("#")[0])
        department = toc_entry.get("chapter", "공통부문")
        title = toc_entry.get("title", "")

        if department not in unmatched_by_dept:
            unmatched_by_dept[department] = []
        unmatched_by_dept[department].append((toc_key, base_id, title))

    # 부문별로 정렬 (섹션 ID 순서대로 처리)
    for dept in unmatched_by_dept:
        unmatched_by_dept[dept].sort(key=lambda x: x[1])

    # 기존 섹션에서 폴백 매칭 수행
    new_sections = []
    found_keys = set()

    for dept, items in unmatched_by_dept.items():
        # 해당 부문의 기존 섹션 중 내용 있는 것만
        dept_sections = [s for s in sections if s["department"] == dept and s["has_content"]]

        for toc_key, base_id, title in items:
            # 섹션 ID 패턴으로 텍스트 내 검색
            # 예: "1-2-10 제초" → 줄 시작에서 "1-2-10 " 패턴
            escaped_id = re.escape(base_id)
            pattern = re.compile(rf'^{escaped_id}\s+', re.MULTILINE)

            for section in dept_sections:
                raw = section["raw_text"]
                m = pattern.search(raw)
                if not m:
                    continue

                # 매칭 지점부터 텍스트 추출
                start_pos = m.start()
                remaining = raw[start_pos:]

                # 끝 지점: 다음 섹션 ID 패턴 또는 텍스트 끝
                # 다음 섹션 패턴: 줄 시작에서 "숫자-숫자" 또는 "숫자-숫자-숫자"
                next_match = re.search(
                    r'\n(\d+-\d+(?:-\d+)?)\s+\S',
                    remaining[len(m.group()):]
                )
                if next_match:
                    end_pos = len(m.group()) + next_match.start() + 1  # +1 for \n
                    section_text = remaining[:end_pos].strip()
                else:
                    section_text = remaining.strip()

                if len(section_text) <= 10:
                    continue

                toc_entry = toc.get(toc_key, {})
                new_sections.append({
                    "section_id": toc_key,
                    "title": title or toc_entry.get("title", ""),
                    "department": dept,
                    "chapter": section["chapter"],
                    "page": section["page"],
                    "raw_text": section_text,
                    "source_file": section["source_file"],
                    "toc_title": toc_entry.get("title", ""),
                    "toc_section": toc_entry.get("section", ""),
                    "has_content": True,
                    "fallback_matched": True,
                })
                found_keys.add(toc_key)
                break  # 첫 매칭으로 충분

    if new_sections:
        print(f"    폴백 매칭 추가: {len(new_sections)}개 섹션")

    return sections + new_sections


def run_step1(pilot_only: bool = False) -> list[dict]:
    """Step 1 실행: MD 파일 로드 -> 섹션 분할"""
    print("=" * 60)
    print("Step 1: MD 파일 로더 & 섹션 분할")
    print("=" * 60)

    toc = load_toc(TOC_FILE)
    print(f"  TOC 로드 완료: {len(toc)}개 섹션")

    reverse_map = build_reverse_map(toc)
    print(f"  역방향 매핑 생성: {len(reverse_map)}개 엔트리")

    md_files = get_md_files(pilot_only=pilot_only)
    print(f"  대상 파일: {len(md_files)}개")

    all_sections = []
    for md_file in tqdm(md_files, desc="  파일 처리"):
        text = md_file.read_text(encoding="utf-8")
        sections = split_sections(text, md_file.name, toc, reverse_map)
        all_sections.extend(sections)

    # 폴백 매칭 1: SECTION 마커 없는 TOC 항목을 텍스트 내 제목 패턴으로 추가 탐지
    pre_fallback = len(all_sections)
    all_sections = fallback_title_matching(all_sections, toc)
    if len(all_sections) > pre_fallback:
        print(f"\n  폴백 매칭(제목): {len(all_sections) - pre_fallback}개 섹션 추가 감지")

    # 폴백 매칭 2: 여전히 미매핑인 TOC 항목을 CONTEXT 마커에서 보충
    pre_ctx = len(all_sections)
    all_sections = context_marker_fallback(all_sections, toc, md_files, reverse_map)
    if len(all_sections) > pre_ctx:
        print(f"\n  폴백 매칭(CONTEXT): {len(all_sections) - pre_ctx}개 섹션 추가 감지")

    # 통계
    content_sections = [s for s in all_sections if s["has_content"]]
    toc_ids = set(toc.keys())
    detected_ids = set(s["section_id"] for s in all_sections)
    matched = detected_ids & toc_ids

    print(f"\n  결과:")
    print(f"    전체 섹션: {len(all_sections)}개")
    print(f"    내용 있는 섹션: {len(content_sections)}개")
    print(f"    내용 없는 섹션: {len(all_sections) - len(content_sections)}개")
    print(f"    TOC 매칭: {len(matched)}/{len(toc_ids)} ({len(matched)/len(toc_ids)*100:.1f}%)")

    # 부문별 통계
    dept_counts = {}
    for s in all_sections:
        dept = s["department"]
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
    print(f"    부문별:")
    for dept, count in sorted(dept_counts.items()):
        print(f"      {dept}: {count}개")

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(RAW_SECTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "total_sections": len(all_sections),
                "content_sections": len(content_sections),
                "source_files": len(md_files),
                "toc_sections": len(toc),
                "toc_matched": len(matched),
                "pilot_only": pilot_only,
            },
            "sections": all_sections,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n  저장: {RAW_SECTIONS_FILE}")
    return all_sections


if __name__ == "__main__":
    import sys
    pilot = "--pilot" in sys.argv
    run_step1(pilot_only=pilot)
