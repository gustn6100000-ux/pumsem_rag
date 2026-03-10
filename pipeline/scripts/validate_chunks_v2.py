"""
전수 데이터 검증 스크립트 v2 (Full Data Quality Audit)
개선: section_id #N 접미사 정규화 + base_id별 그룹핑으로 정확한 대조.

Usage:
    python scripts/validate_chunks_v2.py
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


def get_base_section_id(sid: str) -> str:
    """section_id에서 #N 접미사 제거하여 base ID 추출"""
    return sid.split("#")[0].strip()


# ═══════════════════════════════════════════════════════
# Phase 1: 원본 MD HTML 테이블 파서
# ═══════════════════════════════════════════════════════

def flatten_header(th_tag) -> str:
    for br in th_tag.find_all("br"):
        br.replace_with(" ")
    return th_tag.get_text(strip=True)


def extract_tables_from_md(md_path: Path) -> list[dict]:
    content = md_path.read_text(encoding="utf-8")
    results = []

    pattern = r'<!--\s*(?:SECTION|CONTEXT):\s*(.+?)\s*-->'
    parts = re.split(pattern, content)

    for i in range(1, len(parts), 2):
        meta = parts[i]
        html_block = parts[i + 1] if i + 1 < len(parts) else ""

        meta_parts = [p.strip() for p in meta.split("|")]
        section_id = meta_parts[0] if meta_parts else "unknown"
        title = meta_parts[1] if len(meta_parts) > 1 else ""

        soup = BeautifulSoup(html_block, "html.parser")
        tables = soup.find_all("table")

        for tidx, table in enumerate(tables):
            headers = [flatten_header(th) for th in table.find_all("th")]
            data_rows = []
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if tds:
                    if len(tds) == 1 and tds[0].get("colspan"):
                        continue
                    row = [td.get_text(strip=True) for td in tds]
                    data_rows.append(row)

            col_count = len(headers) if headers else (len(data_rows[0]) if data_rows else 0)

            results.append({
                "source_file": md_path.name,
                "section_id": section_id,
                "title": title,
                "table_index": tidx,
                "headers": headers,
                "header_count": len(headers),
                "row_count": len(data_rows),
                "col_count": col_count,
                "first_row": data_rows[0] if data_rows else [],
            })

    return results


def parse_all_md_files(md_dir: Path) -> dict[str, list[dict]]:
    """모든 MD 파일 파싱 → base_section_id별 테이블 목록"""
    section_tables = defaultdict(list)
    md_files = sorted(md_dir.glob("*.md"))
    print(f"[Phase 1] {len(md_files)}개 MD 파일 파싱 시작...")

    total_tables = 0
    for f in md_files:
        tables = extract_tables_from_md(f)
        for t in tables:
            # base section_id로 정규화
            base_id = get_base_section_id(t["section_id"])
            section_tables[base_id].append(t)
        total_tables += len(tables)

    print(f"[Phase 1] 완료: {len(section_tables)}개 section (base_id), {total_tables}개 테이블\n")
    return dict(section_tables)


# ═══════════════════════════════════════════════════════
# Phase 2: DB Chunk 테이블 추출
# ═══════════════════════════════════════════════════════

def fetch_db_chunks(supabase) -> dict[str, list[dict]]:
    """DB에서 base_section_id별 chunk 테이블 메타데이터 추출"""
    print("[Phase 2] DB chunk 데이터 추출 시작...")

    all_chunks = []
    offset = 0
    batch_size = 1000
    while True:
        resp = supabase.table("graph_chunks") \
            .select("id, section_id, title, tables, text") \
            .range(offset, offset + batch_size - 1) \
            .execute()
        batch = resp.data or []
        all_chunks.extend(batch)
        if len(batch) < batch_size:
            break
        offset += batch_size

    print(f"  총 {len(all_chunks)}개 chunk 로드")

    # base_section_id별 그룹핑
    section_chunks = defaultdict(list)
    for chunk in all_chunks:
        raw_sid = chunk.get("section_id", "unknown")
        base_id = get_base_section_id(raw_sid)
        tables = chunk.get("tables") or []
        text = chunk.get("text") or ""
        chunk_tables = []
        for tidx, tbl in enumerate(tables):
            headers = tbl.get("headers", [])
            rows = tbl.get("rows", [])
            chunk_tables.append({
                "chunk_id": chunk["id"],
                "table_index": tidx,
                "headers": headers,
                "header_count": len(headers),
                "row_count": len(rows),
            })
        section_chunks[base_id].append({
            "chunk_id": chunk["id"],
            "raw_section_id": raw_sid,
            "title": chunk.get("title", ""),
            "table_count": len(tables),
            "tables": chunk_tables,
            "has_text": bool(text.strip()),
        })

    total_tables = sum(
        sum(c["table_count"] for c in chunks)
        for chunks in section_chunks.values()
    )
    print(f"[Phase 2] 완료: {len(section_chunks)}개 section (base_id), {total_tables}개 테이블\n")
    return dict(section_chunks)


# ═══════════════════════════════════════════════════════
# Phase 3: 대조 엔진 (개선)
# ═══════════════════════════════════════════════════════

WELD_HEADER_PATTERNS = {
    "V형_하횡입": ["하향", "횡향", "입향"],
    "한면양면": ["한면", "양면"],
}


def classify_header_type(headers: list[str]) -> str:
    header_text = " ".join(str(h) for h in headers)
    if all(kw in header_text for kw in ["하향", "횡향", "입향"]):
        return "V형_하횡입"
    if any(kw in header_text for kw in ["한면", "양면"]):
        return "한면양면"
    return "기타"


def audit_section(
    base_id: str,
    md_tables: list[dict],
    db_chunks: list[dict],
) -> dict:
    issues = []

    # 원본 행/테이블 수
    md_total_rows = sum(t["row_count"] for t in md_tables)
    md_total_tables = len(md_tables)

    # DB 행/테이블 수
    db_all_tables = []
    for chunk in db_chunks:
        db_all_tables.extend(chunk["tables"])
    db_total_rows = sum(t["row_count"] for t in db_all_tables)
    db_total_tables = len(db_all_tables)

    # Coverage 계산
    if md_total_rows == 0:
        coverage = 100.0
    else:
        coverage = (db_total_rows / md_total_rows) * 100

    # ── 3-A: 행 수 대조 ──
    if md_total_rows > 0:
        if coverage < 50:
            issues.append({
                "type": "ROW_MISSING", "severity": "CRITICAL",
                "detail": f"원본 {md_total_rows}행 vs DB {db_total_rows}행 (coverage {coverage:.1f}%)",
            })
        elif coverage < 80:
            issues.append({
                "type": "ROW_PARTIAL", "severity": "WARNING",
                "detail": f"원본 {md_total_rows}행 vs DB {db_total_rows}행 (coverage {coverage:.1f}%)",
            })
        elif coverage < 95:
            issues.append({
                "type": "ROW_MINOR_DIFF", "severity": "INFO",
                "detail": f"원본 {md_total_rows}행 vs DB {db_total_rows}행 (coverage {coverage:.1f}%)",
            })

    # ── 3-B: 교차 혼입 감지 ──
    for chunk in db_chunks:
        header_types = set()
        for tbl in chunk["tables"]:
            ht = classify_header_type(tbl["headers"])
            header_types.add(ht)
        if len(header_types) > 1 and "기타" not in header_types:
            issues.append({
                "type": "CROSS_TYPE", "severity": "WARNING",
                "detail": f"chunk {chunk['chunk_id']}에 다른 헤더 유형 혼재: {header_types}",
            })

    # ── 3-C: 테이블 수 대조 ──
    if md_total_tables > 0 and db_total_tables == 0:
        issues.append({
            "type": "TABLE_MISSING", "severity": "CRITICAL",
            "detail": f"원본에 {md_total_tables}개 테이블이 있지만 DB에 0개",
        })

    # ── 3-D: 컬럼 수 대조 (MD vs DB 테이블별) ──
    if md_tables and db_all_tables:
        md_col_counts = sorted(set(t["col_count"] for t in md_tables if t["col_count"] > 0))
        db_col_counts = sorted(set(t["header_count"] for t in db_all_tables if t["header_count"] > 0))
        md_only = set(md_col_counts) - set(db_col_counts)
        if md_only and len(md_only) > 0:
            issues.append({
                "type": "COL_MISMATCH", "severity": "INFO",
                "detail": f"원본에만 있는 컬럼 수: {md_only} (MD: {md_col_counts}, DB: {db_col_counts})",
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

    # 상세 chunk 정보
    chunk_details = []
    for chunk in db_chunks:
        chunk_details.append({
            "chunk_id": chunk["chunk_id"],
            "raw_section_id": chunk["raw_section_id"],
            "title": chunk["title"],
            "table_count": chunk["table_count"],
            "total_rows": sum(t["row_count"] for t in chunk["tables"]),
            "has_text": chunk["has_text"],
        })

    return {
        "base_section_id": base_id,
        "title": md_tables[0]["title"] if md_tables else (db_chunks[0]["title"] if db_chunks else ""),
        "status": status,
        "md_tables": md_total_tables,
        "md_rows": md_total_rows,
        "db_chunks": len(db_chunks),
        "db_tables": db_total_tables,
        "db_rows": db_total_rows,
        "coverage_pct": round(coverage, 1),
        "issues": issues,
        "chunk_details": chunk_details,
    }


def run_audit(md_by_section: dict, db_by_section: dict) -> list[dict]:
    print("[Phase 3] 대조 엔진 실행 (base_id 기준)...")
    
    # MD에 있는 section만 대조 (DB-only는 MD 파싱 관련 없음)
    md_sections = set(md_by_section.keys())
    db_sections = set(db_by_section.keys())
    
    results = []
    matched = 0
    md_only = 0
    
    for sid in sorted(md_sections):
        md_tables = md_by_section[sid]
        db_chunks = db_by_section.get(sid, [])
        
        if not db_chunks:
            # 원본에 있지만 DB에 없음
            md_rows = sum(t["row_count"] for t in md_tables)
            if md_rows > 0:
                results.append({
                    "base_section_id": sid,
                    "title": md_tables[0]["title"],
                    "status": "FAIL",
                    "md_tables": len(md_tables), "md_rows": md_rows,
                    "db_chunks": 0, "db_tables": 0, "db_rows": 0,
                    "coverage_pct": 0.0,
                    "issues": [{"type": "SECTION_MISSING", "severity": "CRITICAL",
                                "detail": f"원본 {len(md_tables)}개 테이블/{md_rows}행이 DB에 없음"}],
                    "chunk_details": [],
                })
                md_only += 1
            continue
        
        result = audit_section(sid, md_tables, db_chunks)
        results.append(result)
        matched += 1

    # DB에만 있는 section 통계
    db_only_count = len(db_sections - md_sections)
    
    print(f"[Phase 3] 완료: 대조 {matched}개, MD전용 {md_only}개, DB전용 {db_only_count}개\n")
    return results


# ═══════════════════════════════════════════════════════
# 보고서 출력
# ═══════════════════════════════════════════════════════

def print_summary(results: list[dict], md_count: int, db_count: int):
    total = len(results)
    pass_count = sum(1 for r in results if r["status"] == "PASS")
    info_count = sum(1 for r in results if r["status"] == "INFO")
    warn_count = sum(1 for r in results if r["status"] == "WARN")
    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    cross_type = sum(1 for r in results
                     if any(i["type"] == "CROSS_TYPE" for i in r["issues"]))

    total_md_rows = sum(r["md_rows"] for r in results)
    total_db_rows = sum(r["db_rows"] for r in results)
    overall_coverage = (total_db_rows / total_md_rows * 100) if total_md_rows > 0 else 0

    print("=" * 60)
    print(f"  전수 데이터 검증 결과 v2 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print("=" * 60)
    print(f"  원본 MD section:  {md_count}개 (base_id 기준)")
    print(f"  DB section:       {db_count}개 (base_id 기준)")
    print(f"  대조 대상:        {total}개 section")
    print(f"  {'─' * 50}")
    print(f"  ✅ 정상 (≥95%):   {pass_count}개 ({pass_count/total*100:.1f}%)")
    print(f"  ℹ️  경미 (80~95%): {info_count}개 ({info_count/total*100:.1f}%)")
    print(f"  ⚠️  부분누락:      {warn_count}개 ({warn_count/total*100:.1f}%)")
    print(f"  ❌ 심각누락:      {fail_count}개 ({fail_count/total*100:.1f}%)")
    print(f"  🔀 교차혼입:      {cross_type}개")
    print(f"  {'─' * 50}")
    print(f"  원본 총 행:       {total_md_rows:,}행")
    print(f"  DB 총 행:         {total_db_rows:,}행")
    print(f"  전체 Coverage:    {overall_coverage:.1f}%")
    print("=" * 60)

    # 헤더별 집계: FAIL/WARN 상세
    problem_results = [r for r in results if r["status"] in ("FAIL", "WARN")]
    if problem_results:
        print(f"\n{'─' * 60}")
        print(f"  ❌⚠️ 문제 section 상세 ({len(problem_results)}건)")
        print(f"{'─' * 60}")
        for r in sorted(problem_results, key=lambda x: x.get("coverage_pct") or 0):
            icon = "❌" if r["status"] == "FAIL" else "⚠️"
            cov = f"{r['coverage_pct']:.0f}%" if r['coverage_pct'] is not None else "N/A"
            print(f"\n  {icon} [{r['base_section_id']}] {r['title']}")
            print(f"     원본: {r['md_tables']}개 표 / {r['md_rows']}행")
            print(f"     DB:   {r['db_chunks']}개 chunk / {r['db_tables']}개 표 / {r['db_rows']}행 (coverage {cov})")
            for issue in r["issues"]:
                print(f"     └ {issue['type']}: {issue['detail']}")
            if r.get("chunk_details"):
                for cd in r["chunk_details"]:
                    print(f"       📦 {cd['chunk_id']} ({cd['raw_section_id']}) "
                          f"- {cd['table_count']}표 / {cd['total_rows']}행 "
                          f"{'📝' if cd['has_text'] else '⬜'}")

    # PASS 상세 (상위 10개만)
    pass_results = [r for r in results if r["status"] == "PASS"]
    if pass_results:
        print(f"\n{'─' * 60}")
        print(f"  ✅ 정상 section 샘플 (상위 10건 / 전체 {len(pass_results)}건)")
        print(f"{'─' * 60}")
        for r in pass_results[:10]:
            print(f"  ✅ [{r['base_section_id']}] {r['title']} "
                  f"({r['md_rows']}행 → {r['db_rows']}행, {r['db_chunks']}chunks)")


def save_csv(results: list[dict], output_path: Path):
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "base_section_id", "title", "status",
            "md_tables", "md_rows",
            "db_chunks", "db_tables", "db_rows",
            "coverage_pct", "issue_types", "details", "chunk_ids"
        ])
        for r in sorted(results, key=lambda x: x.get("coverage_pct") or 999):
            issue_types = ";".join(i["type"] for i in r["issues"])
            details = " | ".join(i["detail"] for i in r["issues"])
            chunk_ids = ", ".join(cd["chunk_id"] for cd in r.get("chunk_details", []))
            writer.writerow([
                r["base_section_id"], r["title"], r["status"],
                r["md_tables"], r["md_rows"],
                r.get("db_chunks", 0), r["db_tables"], r["db_rows"],
                r["coverage_pct"], issue_types, details, chunk_ids
            ])
    print(f"[출력] CSV 저장: {output_path}")


def save_json(results: list[dict], output_path: Path):
    total = len(results)
    report = {
        "generated_at": datetime.now().isoformat(),
        "version": "v2",
        "summary": {
            "total_sections": total,
            "pass": sum(1 for r in results if r["status"] == "PASS"),
            "info": sum(1 for r in results if r["status"] == "INFO"),
            "warn": sum(1 for r in results if r["status"] == "WARN"),
            "fail": sum(1 for r in results if r["status"] == "FAIL"),
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
    print("=" * 60)
    print("  품셈 전수 데이터 검증 v2 (base_id 정규화)")
    print("=" * 60)
    print()

    # Phase 1
    md_tables = parse_all_md_files(MD_DIR)

    # Phase 2
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    db_chunks = fetch_db_chunks(supabase)

    # Phase 3
    results = run_audit(md_tables, db_chunks)

    # 보고서 출력
    print_summary(results, len(md_tables), len(db_chunks))
    save_csv(results, OUTPUT_DIR / "audit_v2_details.csv")
    save_json(results, OUTPUT_DIR / "audit_v2_report.json")

    fail_count = sum(1 for r in results if r["status"] == "FAIL")
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
