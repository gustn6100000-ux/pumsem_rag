# -*- coding: utf-8 -*-
"""Phase 0-1: 미인식 테이블 전수 조사

D_기타로 분류된 테이블 중 실제로는 품셈 데이터를 포함하는 테이블을
패턴별로 분류하여 리포트를 생성한다.

사용법:
    python analyze_unhandled_tables.py

출력:
    phase2_output/unhandled_table_analysis.json
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import CHUNKS_FILE, PHASE2_OUTPUT

sys.stdout.reconfigure(encoding="utf-8")


# ─── 직종 키워드 (매트릭스 메타행 탐지용) ─────────────────────
JOB_KEYWORDS = [
    "인부", "특별인부", "보통인부",
    "용접공", "플랜트용접공", "플랜트 용접공", "특수용접공",
    "배관공", "철근공", "비계공", "형틀목공", "콘크리트공",
    "조적공", "미장공", "방수공", "도장공", "타일공",
    "내장공", "판금공", "석공", "건축목공",
    "기사", "산업기사", "기능사", "기능공", "기술자",
    "취부공", "전공",
]

# ─── 자재 키워드 ─────────────────────────────────────────────
MATERIAL_KEYWORDS = [
    "재료", "자재", "재료비", "시멘트", "골재", "모래", "자갈",
    "철근", "콘크리트", "아스팔트", "합판", "거푸집",
    "방수재", "접착제", "도료", "산소", "LPG", "아세틸렌",
    "용접봉", "가스", "산소", "전극봉",
]


def is_matrix_table(headers: list, rows: list) -> bool:
    """매트릭스 테이블 판별: 헤더의 50%+ 가 숫자"""
    if len(headers) < 4:
        return False
    numeric_count = sum(
        1 for h in headers[1:]
        if re.match(r'^\d+(\.\d+)?$', str(h).strip())
    )
    ratio = numeric_count / max(len(headers) - 1, 1)
    return ratio >= 0.5


def has_job_keywords_in_rows(rows: list, headers: list) -> bool:
    """행 데이터에서 직종 키워드 포함 여부"""
    for row in rows[:5]:  # 상위 5행만 검사
        row_text = " ".join(str(row.get(h, "")) for h in headers)
        if any(kw in row_text for kw in JOB_KEYWORDS):
            return True
    return False


def has_range_values(rows: list, headers: list) -> bool:
    """범위 값 (16.5~25.1) 포함 여부"""
    range_pattern = re.compile(r'\d+\.?\d*\s*[~～\-]\s*\d+\.?\d*')
    count = 0
    for row in rows[:10]:
        for h in headers:
            val = str(row.get(h, ""))
            if range_pattern.search(val):
                count += 1
    return count >= 2


def has_material_keywords(headers: list, rows: list) -> bool:
    """헤더 또는 행에 자재 키워드 포함"""
    header_text = " ".join(headers)
    if any(kw in header_text for kw in MATERIAL_KEYWORDS):
        return True
    for row in rows[:3]:
        row_text = " ".join(str(v) for v in row.values())
        if any(kw in row_text for kw in MATERIAL_KEYWORDS):
            return True
    return False


def has_numeric_data_rows(rows: list, headers: list) -> int:
    """숫자 데이터를 포함하는 행 수"""
    count = 0
    for row in rows:
        for h in headers[1:]:
            val = str(row.get(h, "")).strip()
            if re.match(r'^[0-9]+\.?[0-9]*$', val):
                count += 1
                break
    return count


def classify_d_table(table: dict) -> str:
    """D_기타 테이블의 실제 패턴 분류"""
    headers = table.get("headers", [])
    rows = table.get("rows", [])

    if not headers or not rows:
        return "empty"

    # 1. 매트릭스 테이블: 헤더가 숫자 + 행에 직종 키워드
    if is_matrix_table(headers, rows):
        if has_job_keywords_in_rows(rows, headers):
            return "matrix_with_job"
        return "matrix_numeric"

    # 2. 직종 키워드가 행에 있는 테이블 (multi_job)
    if has_job_keywords_in_rows(rows, headers):
        numeric_rows = has_numeric_data_rows(rows, headers)
        if numeric_rows >= 2:
            return "multi_job"

    # 3. 범위 값 테이블
    if has_range_values(rows, headers):
        return "range_val"

    # 4. 자재 테이블
    if has_material_keywords(headers, rows):
        numeric_rows = has_numeric_data_rows(rows, headers)
        if numeric_rows >= 2:
            return "material"

    # 5. 수치 데이터가 있지만 패턴 미매칭
    numeric_rows = has_numeric_data_rows(rows, headers)
    if numeric_rows >= 3:
        return "numeric_unclassified"

    return "non_numeric"


def analyze_all_tables():
    """chunks.json의 모든 테이블을 분석"""
    print("=" * 60)
    print("Phase 0-1: 미인식 테이블 전수 조사")
    print("=" * 60)

    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    chunks = data.get("chunks", [])
    print(f"\n전체 청크 수: {len(chunks)}")

    # 통계
    type_counts = defaultdict(int)
    d_pattern_counts = defaultdict(int)

    # 상세 데이터
    pattern_details = defaultdict(list)
    all_table_types = defaultdict(int)

    for chunk in chunks:
        section_id = chunk.get("section_id", "")
        title = chunk.get("title", "")
        tables = chunk.get("tables", [])

        for table in tables:
            table_type = table.get("type", "unknown")
            table_id = table.get("table_id", "")
            headers = table.get("headers", [])
            rows = table.get("rows", [])

            all_table_types[table_type] += 1

            if table_type in ("D_기타", "C_구분설명"):
                pattern = classify_d_table(table)
                d_pattern_counts[pattern] += 1

                detail = {
                    "table_id": table_id,
                    "section_id": section_id,
                    "title": title,
                    "original_type": table_type,
                    "headers": headers[:10],  # 최대 10개
                    "header_count": len(headers),
                    "row_count": len(rows),
                    "sample_row": {
                        k: str(v)[:50] for k, v in (rows[0].items() if rows else {}.items())
                    },
                }

                # 매트릭스 테이블은 예상 엔티티 수 포함
                if pattern.startswith("matrix"):
                    data_row_count = has_numeric_data_rows(rows, headers)
                    sch_count = sum(
                        1 for h in headers[1:]
                        if re.match(r'^\d+$', str(h).strip())
                    )
                    detail["estimated_entities"] = data_row_count * sch_count
                    detail["sch_headers"] = [
                        h for h in headers[1:]
                        if re.match(r'^\d+$', str(h).strip())
                    ]

                pattern_details[pattern].append(detail)

    # ─── 결과 출력 ─────────────────────────────────────────────
    print("\n" + "─" * 40)
    print("전체 테이블 타입 분포:")
    for t, c in sorted(all_table_types.items(), key=lambda x: -x[1]):
        print(f"  {t:20s}: {c:5d}개")

    print("\n" + "─" * 40)
    print("D_기타 + C_구분설명 패턴 분류:")
    for p, c in sorted(d_pattern_counts.items(), key=lambda x: -x[1]):
        label = {
            "matrix_with_job": "🔴 매트릭스(직종+숫자) — Case D 대상",
            "matrix_numeric": "🟡 매트릭스(숫자만) — Case D 후보",
            "multi_job": "🟡 직종 포함 수치 — Case E 대상",
            "range_val": "🟡 범위 값 — Case F 대상",
            "material": "🟢 자재 소요량",
            "numeric_unclassified": "⚪ 수치 있으나 미분류",
            "non_numeric": "⚪ 비수치 (교정 불필요)",
            "empty": "⚪ 빈 테이블",
        }.get(p, p)
        print(f"  {label:50s}: {c:5d}개")

    # 매트릭스 테이블 상세
    matrix_tables = pattern_details.get("matrix_with_job", [])
    if matrix_tables:
        print(f"\n" + "─" * 40)
        print(f"🔴 매트릭스(직종 포함) 테이블 상세 ({len(matrix_tables)}건):")
        total_est = 0
        for t in matrix_tables:
            est = t.get("estimated_entities", 0)
            total_est += est
            print(f"  [{t['section_id']:10s}] {t['title'][:30]:30s} "
                  f"| {t['header_count']}열 x {t['row_count']}행 "
                  f"| SCH: {t.get('sch_headers', [])[:5]} "
                  f"| 예상 엔티티: {est}")
        print(f"  → 총 예상 엔티티: {total_est}개")

    # multi_job 상세
    multi_job_tables = pattern_details.get("multi_job", [])
    if multi_job_tables:
        print(f"\n" + "─" * 40)
        print(f"🟡 직종 포함 수치 테이블 ({len(multi_job_tables)}건, 상위 10):")
        for t in multi_job_tables[:10]:
            print(f"  [{t['section_id']:10s}] {t['title'][:30]:30s} "
                  f"| 헤더: {t['headers'][:5]}")

    # ─── JSON 저장 ─────────────────────────────────────────────
    output = {
        "summary": {
            "total_tables": sum(all_table_types.values()),
            "table_types": dict(all_table_types),
            "d_pattern_breakdown": dict(d_pattern_counts),
        },
        "patterns": {
            pattern: tables
            for pattern, tables in pattern_details.items()
        },
    }

    PHASE2_OUTPUT.mkdir(parents=True, exist_ok=True)
    output_file = PHASE2_OUTPUT / "unhandled_table_analysis.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 분석 결과 저장: {output_file}")
    return output


if __name__ == "__main__":
    analyze_all_tables()
