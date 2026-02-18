"""Step 5: 품질 검증 & 통계 리포트

생성된 청크의 품질을 자동 검증하고 전체 파이프라인 결과를 요약.
"""
import json
from pathlib import Path
from datetime import datetime

from config import (
    CHUNKS_FILE, TOC_FILE, QUALITY_REPORT_FILE, OUTPUT_DIR,
    QUALITY_THRESHOLDS, RAW_SECTIONS_FILE,
)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_section_coverage(chunks: list[dict], toc: dict, raw_sections: list[dict] = None) -> dict:
    """toc_parsed.json 대비 섹션 매핑률 검증.

    raw_sections이 제공되면 '감지된 전체 섹션'(빈 내용 포함) 기준으로 커버리지 계산.
    """
    chunk_section_ids = set(c["section_id"] for c in chunks)
    toc_section_ids = set(toc.keys())

    # raw_sections에서 감지된 모든 섹션 ID (빈 내용 포함)
    if raw_sections:
        all_detected_ids = set(s["section_id"] for s in raw_sections)
        mapped = all_detected_ids & toc_section_ids
        missing = toc_section_ids - all_detected_ids
        extra = all_detected_ids - toc_section_ids
    else:
        all_detected_ids = chunk_section_ids
        mapped = chunk_section_ids & toc_section_ids
        missing = toc_section_ids - chunk_section_ids
        extra = chunk_section_ids - toc_section_ids

    coverage = len(mapped) / len(toc_section_ids) if toc_section_ids else 0

    threshold = QUALITY_THRESHOLDS["section_coverage_min"]
    severity = "PASS" if coverage >= threshold else "CRITICAL"

    return {
        "check": "section_coverage",
        "severity": severity,
        "coverage_rate": round(coverage, 4),
        "toc_sections": len(toc_section_ids),
        "mapped_sections": len(mapped),
        "missing_sections": sorted(list(missing))[:20],  # 상위 20개만
        "missing_count": len(missing),
        "extra_sections": sorted(list(extra))[:10],
        "threshold": threshold,
    }


def check_table_parse_rate(raw_sections_data: dict, chunks: list[dict]) -> dict:
    """테이블 파싱 성공률 검증"""
    metadata = raw_sections_data.get("metadata", {})
    total_html = metadata.get("total_html_tables", 0)
    total_parsed = metadata.get("total_parsed_tables", 0)

    rate = total_parsed / total_html if total_html > 0 else 1.0
    threshold = QUALITY_THRESHOLDS["table_parse_success_min"]
    severity = "PASS" if rate >= threshold else "HIGH"

    return {
        "check": "table_parse_rate",
        "severity": severity,
        "total_html_tables": total_html,
        "parsed_tables": total_parsed,
        "failed_tables": total_html - total_parsed,
        "success_rate": round(rate, 4),
        "threshold": threshold,
    }


def check_empty_chunks(chunks: list[dict]) -> dict:
    """빈 청크(텍스트+테이블 모두 비어있는) 비율 검증"""
    empty_chunks = []
    for c in chunks:
        has_text = bool(c.get("text", "").strip())
        has_tables = bool(c.get("tables", []))
        if not has_text and not has_tables:
            empty_chunks.append(c["chunk_id"])

    ratio = len(empty_chunks) / len(chunks) if chunks else 0
    threshold = QUALITY_THRESHOLDS["empty_chunk_max_ratio"]
    severity = "PASS" if ratio <= threshold else "MEDIUM"

    return {
        "check": "empty_chunks",
        "severity": severity,
        "total_chunks": len(chunks),
        "empty_chunks": len(empty_chunks),
        "empty_ratio": round(ratio, 4),
        "empty_chunk_ids": empty_chunks[:20],
        "threshold": threshold,
    }


def check_token_distribution(chunks: list[dict]) -> dict:
    """토큰 분포 검증"""
    tokens = [c.get("token_count", 0) for c in chunks]
    if not tokens:
        return {"check": "token_distribution", "severity": "WARN", "message": "청크 없음"}

    avg = sum(tokens) / len(tokens)
    sorted_tokens = sorted(tokens)
    p95 = sorted_tokens[int(len(sorted_tokens) * 0.95)] if len(sorted_tokens) > 20 else max(tokens)

    issues = []
    if avg < QUALITY_THRESHOLDS["avg_token_min"]:
        issues.append(f"평균 토큰({avg:.0f})이 최소 기준({QUALITY_THRESHOLDS['avg_token_min']}) 미만")
    if avg > QUALITY_THRESHOLDS["avg_token_max"]:
        issues.append(f"평균 토큰({avg:.0f})이 최대 기준({QUALITY_THRESHOLDS['avg_token_max']}) 초과")

    over_limit = [c["chunk_id"] for c in chunks
                  if c.get("token_count", 0) > QUALITY_THRESHOLDS["max_token_limit"]]

    severity = "PASS" if not issues and not over_limit else "MEDIUM"

    return {
        "check": "token_distribution",
        "severity": severity,
        "avg_tokens": round(avg, 1),
        "min_tokens": min(tokens),
        "max_tokens": max(tokens),
        "p95_tokens": p95,
        "over_limit_count": len(over_limit),
        "over_limit_chunks": over_limit[:10],
        "issues": issues,
    }


def check_metadata_completeness(chunks: list[dict]) -> dict:
    """필수 메타데이터 필드 누락 검증"""
    required_fields = ["section_id", "department", "chapter", "title"]
    missing = []

    for c in chunks:
        for field in required_fields:
            if not c.get(field):
                missing.append({"chunk_id": c["chunk_id"], "field": field})

    severity = "PASS" if not missing else "CRITICAL"

    return {
        "check": "metadata_completeness",
        "severity": severity,
        "required_fields": required_fields,
        "missing_count": len(missing),
        "missing_details": missing[:20],
    }


def check_duplicate_chunks(chunks: list[dict]) -> dict:
    """중복 section_id 검사"""
    section_id_counts = {}
    for c in chunks:
        sid = c["section_id"]
        section_id_counts[sid] = section_id_counts.get(sid, 0) + 1

    # 분할된 청크(C-XXXX-A/B)는 같은 section_id를 가질 수 있으므로
    # chunk_id 기준으로 중복 확인
    chunk_ids = [c["chunk_id"] for c in chunks]
    dup_chunk_ids = [cid for cid in chunk_ids if chunk_ids.count(cid) > 1]

    severity = "PASS" if not dup_chunk_ids else "HIGH"

    return {
        "check": "duplicate_chunks",
        "severity": severity,
        "unique_section_ids": len(section_id_counts),
        "duplicate_chunk_ids": list(set(dup_chunk_ids)),
    }


def build_department_summary(chunks: list[dict]) -> dict:
    """부문별 통계"""
    summary = {}
    for c in chunks:
        dept = c["department"]
        if dept not in summary:
            summary[dept] = {"chunks": 0, "tables": 0, "sections": set()}
        summary[dept]["chunks"] += 1
        summary[dept]["tables"] += len(c.get("tables", []))
        summary[dept]["sections"].add(c["section_id"])

    # set → count
    for dept in summary:
        summary[dept]["sections"] = len(summary[dept]["sections"])

    return summary


def run_step5(chunks_file: Path = None) -> dict:
    """Step 5 실행: 품질 검증"""
    print("\n" + "=" * 60)
    print("Step 5: 품질 검증 & 통계 리포트")
    print("=" * 60)

    chunks_file = chunks_file or CHUNKS_FILE
    chunks_data = load_json(chunks_file)
    chunks = chunks_data["chunks"]
    print(f"  입력 청크: {len(chunks)}개")

    toc_data = load_json(TOC_FILE)
    toc = toc_data.get("section_map", toc_data)

    # raw_sections 로드 (커버리지 검증용 - 빈 내용 포함 전체 섹션)
    raw_sections = []
    if RAW_SECTIONS_FILE.exists():
        raw_sections_data = load_json(RAW_SECTIONS_FILE)
        raw_sections = raw_sections_data.get("sections", [])
        print(f"  raw_sections 로드: {len(raw_sections)}개")

    # parsed_tables 메타데이터 (테이블 파싱 통계용)
    raw_data = {}
    raw_path = RAW_SECTIONS_FILE.parent / "parsed_tables.json"
    if raw_path.exists():
        raw_data = load_json(raw_path)

    # 검증 수행
    checks = [
        check_section_coverage(chunks, toc, raw_sections=raw_sections),
        check_table_parse_rate(raw_data, chunks),
        check_empty_chunks(chunks),
        check_token_distribution(chunks),
        check_metadata_completeness(chunks),
        check_duplicate_chunks(chunks),
    ]

    # 부문별 통계
    dept_summary = build_department_summary(chunks)

    # 전체 통계
    token_counts = [c.get("token_count", 0) for c in chunks]
    total_tables = sum(len(c.get("tables", [])) for c in chunks)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "total_chunks": len(chunks),
            "total_sections_mapped": len(set(c["section_id"] for c in chunks)),
            "total_tables_parsed": total_tables,
            "avg_token_count": round(sum(token_counts) / len(token_counts), 1) if token_counts else 0,
            "max_token_count": max(token_counts) if token_counts else 0,
        },
        "by_department": dept_summary,
        "checks": checks,
        "issues": [],
    }

    # 이슈 목록 생성
    for check in checks:
        if check["severity"] not in ("PASS",):
            report["issues"].append({
                "severity": check["severity"],
                "check": check["check"],
                "message": json.dumps(
                    {k: v for k, v in check.items() if k not in ("check", "severity")},
                    ensure_ascii=False
                )[:200],
            })

    # 결과 출력
    print(f"\n  검증 결과:")
    for check in checks:
        icon = "  PASS" if check["severity"] == "PASS" else f"  {check['severity']}"
        print(f"    [{icon}] {check['check']}")

    print(f"\n  요약:")
    print(f"    총 청크: {report['summary']['total_chunks']}")
    print(f"    매핑된 섹션: {report['summary']['total_sections_mapped']}")
    print(f"    파싱된 테이블: {report['summary']['total_tables_parsed']}")
    print(f"    평균 토큰: {report['summary']['avg_token_count']}")

    if report["issues"]:
        print(f"\n  이슈 {len(report['issues'])}건:")
        for issue in report["issues"]:
            print(f"    [{issue['severity']}] {issue['check']}")

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(QUALITY_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n  저장: {QUALITY_REPORT_FILE}")
    return report


if __name__ == "__main__":
    run_step5()
