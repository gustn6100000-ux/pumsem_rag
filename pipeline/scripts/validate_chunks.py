"""
전수 데이터 검증 스크립트 (Full Data Quality Audit)
원본 MD 파일의 HTML 테이블과 DB chunk 테이블을 1:1 대조하여
누락/불일치/교차 혼입을 자동 감지한다.

Usage:
    python scripts/validate_chunks.py
"""
import os
import re
import json
import csv
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client

# ─── 환경 설정 ───
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
MD_DIR = Path(__file__).resolve().parent.parent / "download_file"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════
# Phase 1: 원본 MD HTML 테이블 파서
# ═══════════════════════════════════════════════════════

def flatten_header(th_tag) -> str:
    """<th> 태그에서 텍스트 추출 (br → 공백)"""
    for br in th_tag.find_all("br"):
        br.replace_with(" ")
    return th_tag.get_text(strip=True)


def extract_tables_from_md(md_path: Path) -> list[dict]:
    """
    하나의 MD 파일에서 section별 HTML 테이블을 추출.
    <!-- SECTION: {id} | {title} | ... --> 패턴으로 section 경계 인식.
    <!-- CONTEXT: {id} | ... --> 패턴도 동일 section의 연속으로 처리.
    """
    content = md_path.read_text(encoding="utf-8")
    results = []

    # section 경계 파싱: SECTION 또는 CONTEXT 태그
    # 각 section 영역을 (section_id, title, html_block) 으로 분할
    pattern = r'<!--\s*(?:SECTION|CONTEXT):\s*(.+?)\s*-->'
    parts = re.split(pattern, content)

    # parts: [before, meta1, block1, meta2, block2, ...]
    current_section_id = None
    current_title = None

    for i in range(1, len(parts), 2):
        meta = parts[i]
        html_block = parts[i + 1] if i + 1 < len(parts) else ""

        # meta: "13-2-4 | 강판 전기아크용접 | 부문:기계설비부문 | 장:제13장 플랜트설비공사"
        meta_parts = [p.strip() for p in meta.split("|")]
        section_id = meta_parts[0] if meta_parts else "unknown"
        title = meta_parts[1] if len(meta_parts) > 1 else ""

        # CONTEXT는 이전 SECTION의 연속 → section_id 유지
        current_section_id = section_id
        current_title = title

        # HTML 테이블 추출
        soup = BeautifulSoup(html_block, "html.parser")
        tables = soup.find_all("table")

        for tidx, table in enumerate(tables):
            # 헤더 추출 (모든 th)
            headers = [flatten_header(th) for th in table.find_all("th")]

            # 데이터 행 추출 (td만 있는 tr)
            data_rows = []
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if tds:
                    row = [td.get_text(strip=True) for td in tds]
                    # colspan 주석 행 제외 (단일 셀에 [주] 등)
                    if len(tds) == 1 and tds[0].get("colspan"):
                        continue
                    data_rows.append(row)

            col_count = len(headers) if headers else (len(data_rows[0]) if data_rows else 0)

            results.append({
                "source_file": md_path.name,
                "section_id": current_section_id,
                "title": current_title,
                "table_index": tidx,
                "headers": headers,
                "header_count": len(headers),
                "row_count": len(data_rows),
                "col_count": col_count,
                "first_row": data_rows[0] if data_rows else [],
            })

    return results


def parse_all_md_files(md_dir: Path) -> dict[str, list[dict]]:
    """모든 MD 파일 파싱 → section_id별 테이블 목록"""
    section_tables = defaultdict(list)
    md_files = sorted(md_dir.glob("*.md"))
    print(f"[Phase 1] {len(md_files)}개 MD 파일 파싱 시작...")

    total_tables = 0
    for f in md_files:
        tables = extract_tables_from_md(f)
        for t in tables:
            section_tables[t["section_id"]].append(t)
        total_tables += len(tables)
        if tables:
            print(f"  {f.name}: {len(tables)}개 테이블")

    print(f"[Phase 1] 완료: {len(section_tables)}개 section, {total_tables}개 테이블\n")
    return dict(section_tables)


# ═══════════════════════════════════════════════════════
# Phase 2: DB Chunk 테이블 추출
# ═══════════════════════════════════════════════════════

def fetch_db_chunks(supabase) -> dict[str, list[dict]]:
    """DB에서 section_id별 chunk 테이블 메타데이터 추출"""
    print("[Phase 2] DB chunk 데이터 추출 시작...")

    # Supabase는 대량 쿼리에 limit이 있으므로 페이징
    all_chunks = []
    offset = 0
    batch_size = 1000
    while True:
        resp = supabase.table("graph_chunks") \
            .select("id, section_id, title, tables") \
            .range(offset, offset + batch_size - 1) \
            .execute()
        batch = resp.data or []
        all_chunks.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size

    print(f"  총 {len(all_chunks)}개 chunk 로드")

    # section_id별 그룹핑
    section_chunks = defaultdict(list)
    for chunk in all_chunks:
        sid = chunk.get("section_id", "unknown")
        tables = chunk.get("tables") or []
        chunk_tables = []
        for tidx, tbl in enumerate(tables):
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            first_row = rows[0] if rows else {}
            chunk_tables.append({
                "chunk_id": chunk["id"],
                "table_index": tidx,
                "headers": headers,
                "header_count": len(headers),
                "row_count": len(rows),
                "first_row": first_row,
            })
        section_chunks[sid].append({
            "chunk_id": chunk["id"],
            "title": chunk.get("title", ""),
            "table_count": len(tables),
            "tables": chunk_tables,
        })

    total_tables = sum(
        sum(c["table_count"] for c in chunks)
        for chunks in section_chunks.values()
    )
    print(f"[Phase 2] 완료: {len(section_chunks)}개 section, {total_tables}개 테이블\n")
    return dict(section_chunks)


# ═══════════════════════════════════════════════════════
# Phase 3: 대조 엔진
# ═══════════════════════════════════════════════════════

# 용접 유형별 헤더 패턴 (교차 혼입 감지용)
WELD_HEADER_PATTERNS = {
    "V형_하횡입": ["하향", "횡향", "입향"],
    "U/H/X형_한양": ["한면", "양면"],
}


def classify_header_type(headers: list[str]) -> str:
    """헤더 목록에서 용접 유형 패턴 분류"""
    header_text = " ".join(headers)
    if all(kw in header_text for kw in ["하향", "횡향", "입향"]):
        return "V형_하횡입"
    if any(kw in header_text for kw in ["한면", "양면"]):
        return "한면양면"
    return "기타"


def audit_section(
    section_id: str,
    md_tables: list[dict],
    db_chunks: list[dict],
) -> dict:
    """단일 section에 대해 원본↔DB 대조"""
    issues = []

    # 원본 행 수 합계
    md_total_rows = sum(t["row_count"] for t in md_tables)
    md_total_tables = len(md_tables)

    # DB 행 수 합계
    db_all_tables = []
    for chunk in db_chunks:
        db_all_tables.extend(chunk["tables"])
    db_total_rows = sum(t["row_count"] for t in db_all_tables)
    db_total_tables = len(db_all_tables)

    # ── 3-A: 행 수 대조 ──
    if md_total_rows == 0:
        coverage = 100.0  # 원본에 표가 없으면 pass
    else:
        coverage = (db_total_rows / md_total_rows) * 100

    if coverage < 50:
        severity = "CRITICAL"
        issues.append({
            "type": "ROW_MISSING",
            "severity": severity,
            "detail": f"원본 {md_total_rows}행 vs DB {db_total_rows}행 (coverage {coverage:.1f}%)",
        })
    elif coverage < 80:
        severity = "WARNING"
        issues.append({
            "type": "ROW_PARTIAL",
            "severity": severity,
            "detail": f"원본 {md_total_rows}행 vs DB {db_total_rows}행 (coverage {coverage:.1f}%)",
        })
    elif coverage < 95:
        severity = "INFO"
        issues.append({
            "type": "ROW_MINOR_DIFF",
            "severity": severity,
            "detail": f"원본 {md_total_rows}행 vs DB {db_total_rows}행 (coverage {coverage:.1f}%)",
        })

    # ── 3-B: 교차 혼입 감지 ──
    # 같은 chunk에 다른 헤더 유형이 존재하면 경고
    for chunk in db_chunks:
        header_types = set()
        for tbl in chunk["tables"]:
            ht = classify_header_type(tbl["headers"])
            header_types.add(ht)
        if len(header_types) > 1 and "기타" not in header_types:
            issues.append({
                "type": "CROSS_TYPE",
                "severity": "WARNING",
                "detail": f"chunk {chunk['chunk_id']}에 다른 헤더 유형 혼재: {header_types}",
            })

    # ── 3-C: 테이블 수 대조 ──
    if md_total_tables > 0 and db_total_tables == 0:
        issues.append({
            "type": "TABLE_MISSING",
            "severity": "CRITICAL",
            "detail": f"원본에 {md_total_tables}개 테이블이 있지만 DB에 0개",
        })
    elif md_total_tables > db_total_tables + 1:
        issues.append({
            "type": "TABLE_COUNT_DIFF",
            "severity": "WARNING",
            "detail": f"원본 {md_total_tables}개 vs DB {db_total_tables}개 테이블",
        })

    # 판정
    if not issues:
        status = "PASS"
    elif any(i["severity"] == "CRITICAL" for i in issues):
        status = "FAIL"
    elif any(i["severity"] == "WARNING" for i in issues):
        status = "WARN"
    else:
        status = "INFO"

    return {
        "section_id": section_id,
        "title": md_tables[0]["title"] if md_tables else "",
        "status": status,
        "md_tables": md_total_tables,
        "md_rows": md_total_rows,
        "db_tables": db_total_tables,
        "db_rows": db_total_rows,
        "coverage_pct": round(coverage, 1),
        "issues": issues,
    }


def run_audit(md_tables_by_section: dict, db_chunks_by_section: dict) -> list[dict]:
    """전체 section에 대해 대조 실행"""
    print("[Phase 3] 대조 엔진 실행...")
    all_sections = set(md_tables_by_section.keys()) | set(db_chunks_by_section.keys())
    results = []

    for sid in sorted(all_sections):
        md_tables = md_tables_by_section.get(sid, [])
        db_chunks = db_chunks_by_section.get(sid, [])

        if not md_tables and db_chunks:
            # DB에만 있고 원본에 없음 (보통 정상)
            results.append({
                "section_id": sid,
                "title": db_chunks[0].get("title", ""),
                "status": "DB_ONLY",
                "md_tables": 0, "md_rows": 0,
                "db_tables": sum(c["table_count"] for c in db_chunks),
                "db_rows": sum(t["row_count"] for c in db_chunks for t in c["tables"]),
                "coverage_pct": None,
                "issues": [],
            })
            continue

        if md_tables and not db_chunks:
            # 원본에 있지만 DB에 없음
            md_rows = sum(t["row_count"] for t in md_tables)
            if md_rows > 0:
                results.append({
                    "section_id": sid,
                    "title": md_tables[0]["title"],
                    "status": "FAIL",
                    "md_tables": len(md_tables), "md_rows": md_rows,
                    "db_tables": 0, "db_rows": 0,
                    "coverage_pct": 0.0,
                    "issues": [{"type": "SECTION_MISSING", "severity": "CRITICAL",
                                "detail": f"원본 {len(md_tables)}개 테이블/{md_rows}행이 DB에 없음"}],
                })
            continue

        result = audit_section(sid, md_tables, db_chunks)
        results.append(result)

    print(f"[Phase 3] 완료: {len(results)}개 section 대조\n")
    return results


# ═══════════════════════════════════════════════════════
# 보고서 출력
# ═══════════════════════════════════════════════════════

def print_summary(results: list[dict]):
    """터미널 요약 대시보드"""
    total = len(results)
    pass_count = sum(1 for r in results if r["status"] == "PASS")
    info_count = sum(1 for r in results if r["status"] == "INFO")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    db_only = sum(1 for r in results if r["status"] == "DB_ONLY")
    cross_type = sum(1 for r in results
                     if any(i["type"] == "CROSS_TYPE" for i in r["issues"]))

    total_md_rows = sum(r["md_rows"] for r in results if r["md_rows"])
    total_db_rows = sum(r["db_rows"] for r in results if r["db_rows"])
    overall_coverage = (total_db_rows / total_md_rows * 100) if total_md_rows > 0 else 0

    print("=" * 55)
    print(f"  전수 데이터 검증 결과 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print("=" * 55)
    print(f"  총 Section:   {total}개  (원본 MD 기준)")
    print(f"  ✅ 정상:       {pass_count}개 ({pass_count/total*100:.1f}%)")
    print(f"  ℹ️  경미차이:   {info_count}개 ({info_count/total*100:.1f}%)")
    print(f"  ⚠️  부분누락:   {warn_count}개 ({warn_count/total*100:.1f}%)")
    print(f"  ❌ 심각누락:   {fail_count}개 ({fail_count/total*100:.1f}%)")
    print(f"  📦 DB전용:     {db_only}개")
    print(f"  🔀 교차혼입:   {cross_type}개")
    print("=" * 55)
    print(f"  원본 총 행:    {total_md_rows:,}행")
    print(f"  DB 총 행:      {total_db_rows:,}행")
    print(f"  전체 Coverage: {overall_coverage:.1f}%")
    print("=" * 55)

    # 오류 상세 (FAIL + WARN만)
    problem_results = [r for r in results if r["status"] in ("FAIL", "WARN")]
    if problem_results:
        print(f"\n{'─' * 55}")
        print(f"  상세 오류 목록 ({len(problem_results)}건)")
        print(f"{'─' * 55}")
        for r in sorted(problem_results, key=lambda x: x["coverage_pct"] or 0):
            icon = "❌" if r["status"] == "FAIL" else "⚠️"
            cov = f"{r['coverage_pct']:.0f}%" if r['coverage_pct'] is not None else "N/A"
            print(f"  {icon} [{r['section_id']}] {r['title']}")
            print(f"     원본 {r['md_rows']}행 → DB {r['db_rows']}행 (coverage {cov})")
            for issue in r["issues"]:
                print(f"     └ {issue['type']}: {issue['detail']}")
            print()


def save_csv(results: list[dict], output_path: Path):
    """상세 CSV 저장"""
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "section_id", "title", "status", "md_tables", "md_rows",
            "db_tables", "db_rows", "coverage_pct", "issue_types", "details"
        ])
        for r in sorted(results, key=lambda x: x.get("coverage_pct") or 999):
            issue_types = ";".join(i["type"] for i in r["issues"])
            details = " | ".join(i["detail"] for i in r["issues"])
            writer.writerow([
                r["section_id"], r["title"], r["status"],
                r["md_tables"], r["md_rows"],
                r["db_tables"], r["db_rows"],
                r["coverage_pct"], issue_types, details
            ])
    print(f"[출력] CSV 저장: {output_path}")


def save_json(results: list[dict], output_path: Path):
    """JSON 보고서 저장"""
    total = len(results)
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_sections": total,
            "pass": sum(1 for r in results if r["status"] == "PASS"),
            "info": sum(1 for r in results if r["status"] == "INFO"),
            "warn": sum(1 for r in results if r["status"] == "WARN"),
            "fail": sum(1 for r in results if r["status"] == "FAIL"),
            "db_only": sum(1 for r in results if r["status"] == "DB_ONLY"),
            "cross_type_count": sum(1 for r in results
                                    if any(i["type"] == "CROSS_TYPE" for i in r["issues"])),
            "total_md_rows": sum(r["md_rows"] for r in results),
            "total_db_rows": sum(r["db_rows"] for r in results),
        },
        "sections": results,
    }
    total_md = report["summary"]["total_md_rows"]
    total_db = report["summary"]["total_db_rows"]
    report["summary"]["overall_coverage_pct"] = round(
        total_db / total_md * 100, 1) if total_md > 0 else 0

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[출력] JSON 저장: {output_path}")


# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════

def main():
    print("=" * 55)
    print("  품셈 전수 데이터 검증 (Full Data Quality Audit)")
    print("=" * 55)
    print()

    # Phase 1: 원본 MD 파싱
    md_tables = parse_all_md_files(MD_DIR)

    # Phase 2: DB chunk 추출
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    db_chunks = fetch_db_chunks(supabase)

    # Phase 3: 대조
    results = run_audit(md_tables, db_chunks)

    # 보고서 출력
    print_summary(results)
    save_csv(results, OUTPUT_DIR / "audit_details.csv")
    save_json(results, OUTPUT_DIR / "audit_report.json")

    # 종료 코드: 오류 있으면 1
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
