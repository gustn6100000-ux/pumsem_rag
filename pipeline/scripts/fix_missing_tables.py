"""
데이터 보완 스크립트: 원본 MD 테이블 → DB chunk tables 배열 삽입
FAIL section 중 coverage가 낮은 것들의 원본 테이블을 DB에 보완.

전략:
  1. audit_v2_report.json에서 FAIL/WARN section 추출
  2. 원본 MD 파일에서 해당 section의 HTML 테이블 파싱
  3. DB에서 매칭되는 chunk를 찾고, 없으면 새 chunk 생성
  4. tables 배열에 누락된 테이블 추가

Usage:
    python scripts/fix_missing_tables.py [--dry-run] [--section SECTION_ID]
"""
import os
import re
import json
import sys
from pathlib import Path
from collections import defaultdict

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client

# ─── 환경 설정 ───
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
MD_DIR = Path(__file__).resolve().parent.parent / "download_file"
REPORT_FILE = Path(__file__).resolve().parent / "output" / "audit_v2_report.json"


def flatten_header(th_tag) -> str:
    for br in th_tag.find_all("br"):
        br.replace_with(" ")
    return th_tag.get_text(strip=True)


def parse_html_table(table_tag) -> dict:
    """HTML <table>을 {headers, rows} 형태로 파싱"""
    headers = [flatten_header(th) for th in table_tag.find_all("th")]
    rows = []
    for tr in table_tag.find_all("tr"):
        tds = tr.find_all("td")
        if tds:
            # colspan 전체가 1행인 것은 비고행 → 스킵
            if len(tds) == 1 and tds[0].get("colspan"):
                continue
            row = [td.get_text(strip=True) for td in tds]
            rows.append(row)
    return {"headers": headers, "rows": rows}


def extract_tables_for_section(md_dir: Path, target_sid: str) -> list[dict]:
    """원본 MD 파일들에서 특정 section_id의 테이블만 추출"""
    pattern = r'<!--\s*(?:SECTION|CONTEXT):\s*(.+?)\s*-->'
    tables = []

    for md_file in sorted(md_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        parts = re.split(pattern, content)

        for i in range(1, len(parts), 2):
            meta = parts[i]
            html_block = parts[i + 1] if i + 1 < len(parts) else ""
            meta_parts = [p.strip() for p in meta.split("|")]
            section_id = meta_parts[0] if meta_parts else "unknown"
            title = meta_parts[1] if len(meta_parts) > 1 else ""

            if section_id != target_sid:
                continue

            soup = BeautifulSoup(html_block, "html.parser")
            for tidx, tbl in enumerate(soup.find_all("table")):
                parsed = parse_html_table(tbl)
                if parsed["rows"]:  # 빈 테이블 제외
                    tables.append({
                        "source_file": md_file.name,
                        "section_id": section_id,
                        "title": title,
                        "table_index": tidx,
                        **parsed,
                    })
    return tables


def find_target_chunk(supabase, section_id: str) -> dict | None:
    """DB에서 해당 section_id의 chunk 조회 (tables가 적은 것 우선)"""
    # base_id로 먼저 검색
    base_id = section_id.split("#")[0]
    resp = supabase.table("graph_chunks") \
        .select("id, section_id, title, tables") \
        .like("section_id", f"{base_id}%") \
        .execute()
    
    chunks = resp.data or []
    if not chunks:
        return None

    # tables가 가장 적은 chunk (보완 대상)
    chunks.sort(key=lambda c: len(c.get("tables") or []))
    return chunks[0]


def main():
    dry_run = "--dry-run" in sys.argv
    target_section = None
    for i, arg in enumerate(sys.argv):
        if arg == "--section" and i + 1 < len(sys.argv):
            target_section = sys.argv[i + 1]

    print("=" * 60)
    print(f"  데이터 보완 스크립트 {'(DRY RUN)' if dry_run else ''}")
    print("=" * 60)

    # 1. FAIL section 목록 로드
    report = json.load(open(REPORT_FILE, encoding="utf-8"))
    fail_sections = [
        s for s in report["sections"]
        if s["status"] in ("FAIL", "WARN")
    ]

    if target_section:
        fail_sections = [s for s in fail_sections if s["base_section_id"] == target_section]

    print(f"  대상 section: {len(fail_sections)}개\n")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    fixed = 0
    skipped = 0
    no_md = 0
    results = []

    for section in fail_sections:
        sid = section["base_section_id"]
        title = section["title"]
        md_rows = section["md_rows"]
        db_rows = section["db_rows"]

        # 2. 원본 MD에서 해당 section 테이블 추출
        md_tables = extract_tables_for_section(MD_DIR, sid)
        if not md_tables:
            no_md += 1
            continue

        # 3. DB에서 매칭 chunk 조회
        chunk = find_target_chunk(supabase, sid)
        if not chunk:
            # DB에 chunk 없음 → 새 chunk 생성 필요
            results.append({
                "section_id": sid, "title": title,
                "action": "NEED_NEW_CHUNK",
                "md_tables": len(md_tables),
                "md_rows": sum(len(t["rows"]) for t in md_tables),
            })
            skipped += 1
            continue

        # 4. 기존 chunk의 tables에 누락된 테이블 추가
        existing_tables = chunk.get("tables") or []
        existing_row_count = sum(len(t.get("rows", [])) for t in existing_tables)

        new_tables = []
        for mt in md_tables:
            # 이미 존재하는 테이블인지 확인 (헤더로 비교)
            mt_headers_str = "|".join(mt["headers"][:3])
            already_exists = any(
                "|".join((et.get("headers") or [])[:3]) == mt_headers_str
                for et in existing_tables
            )
            if not already_exists and mt["rows"]:
                new_tables.append({
                    "headers": mt["headers"],
                    "rows": mt["rows"],
                })

        if not new_tables:
            results.append({
                "section_id": sid, "title": title,
                "action": "ALREADY_EXISTS",
                "chunk_id": chunk["id"],
            })
            skipped += 1
            continue

        new_row_count = sum(len(t["rows"]) for t in new_tables)
        print(f"  [{sid}] {title}")
        print(f"    chunk: {chunk['id']}, 기존 {len(existing_tables)}표/{existing_row_count}행")
        print(f"    추가: {len(new_tables)}표/{new_row_count}행")

        if not dry_run:
            updated_tables = existing_tables + new_tables
            supabase.table("graph_chunks") \
                .update({"tables": updated_tables}) \
                .eq("id", chunk["id"]) \
                .execute()
            print(f"    ✅ 업데이트 완료")
        else:
            print(f"    🏷️ DRY RUN - 변경 없음")

        results.append({
            "section_id": sid, "title": title,
            "action": "FIXED",
            "chunk_id": chunk["id"],
            "added_tables": len(new_tables),
            "added_rows": new_row_count,
        })
        fixed += 1

    # 결과 요약
    print(f"\n{'=' * 60}")
    print(f"  결과 요약")
    print(f"{'=' * 60}")
    print(f"  교정: {fixed}건")
    print(f"  스킵: {skipped}건")
    print(f"  원본 MD 없음: {no_md}건")

    need_new = [r for r in results if r["action"] == "NEED_NEW_CHUNK"]
    if need_new:
        print(f"\n  새 chunk 생성 필요 ({len(need_new)}건):")
        for r in need_new:
            print(f"    [{r['section_id']}] {r['title']} - {r['md_tables']}표/{r['md_rows']}행")

    # 결과 저장
    output_path = Path(__file__).resolve().parent / "output" / "fix_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"fixed": fixed, "skipped": skipped, "no_md": no_md, "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"\n  결과 저장: {output_path}")


if __name__ == "__main__":
    main()
