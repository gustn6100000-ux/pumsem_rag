"""
새 chunk 생성 스크립트: NEED_NEW_CHUNK 39건
원본 MD에서 테이블을 추출하여 DB에 새 chunk로 삽입.

Usage:
    python scripts/create_missing_chunks.py [--dry-run]
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

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
MD_DIR = Path(__file__).resolve().parent.parent / "download_file"
FIX_RESULTS = Path(__file__).resolve().parent / "output" / "fix_results.json"


def flatten_header(th_tag) -> str:
    for br in th_tag.find_all("br"):
        br.replace_with(" ")
    return th_tag.get_text(strip=True)


def extract_section_data(md_dir: Path, target_sid: str) -> dict:
    """원본 MD에서 section의 text + tables 추출"""
    pattern = r'<!--\s*(?:SECTION|CONTEXT):\s*(.+?)\s*-->'
    text_parts = []
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

            # text 추출 (테이블 외 텍스트)
            for tbl in soup.find_all("table"):
                tbl.decompose()
            text = soup.get_text(strip=True)
            if text:
                text_parts.append(text)

            # 테이블 재파싱
            soup2 = BeautifulSoup(html_block, "html.parser")
            for tidx, tbl in enumerate(soup2.find_all("table")):
                headers = [flatten_header(th) for th in tbl.find_all("th")]
                rows = []
                for tr in tbl.find_all("tr"):
                    tds = tr.find_all("td")
                    if tds:
                        if len(tds) == 1 and tds[0].get("colspan"):
                            continue
                        row = [td.get_text(strip=True) for td in tds]
                        rows.append(row)
                if rows:
                    tables.append({"headers": headers, "rows": rows})

            return {
                "title": title,
                "text": "\n".join(text_parts) if text_parts else "",
                "tables": tables,
            }

    return None


def main():
    dry_run = "--dry-run" in sys.argv

    print("=" * 60)
    print(f"  새 chunk 생성 스크립트 {'(DRY RUN)' if dry_run else ''}")
    print("=" * 60)

    # fix_results.json에서 NEED_NEW_CHUNK 목록
    fix_data = json.load(open(FIX_RESULTS, encoding="utf-8"))
    need_new = [r for r in fix_data["results"] if r["action"] == "NEED_NEW_CHUNK"]
    print(f"  대상: {len(need_new)}건\n")

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    created = 0
    skipped = 0

    for idx, item in enumerate(need_new):
        sid = item["section_id"]
        title = item["title"]

        # 원본 MD에서 데이터 추출
        data = extract_section_data(MD_DIR, sid)
        if not data or not data["tables"]:
            print(f"  ⬜ [{sid}] {title} - 원본 테이블 없음")
            skipped += 1
            continue

        chunk_id = f"C-NEW-{idx+1:04d}"
        total_rows = sum(len(t["rows"]) for t in data["tables"])

        print(f"  📦 [{sid}] {data['title']}")
        print(f"     ID: {chunk_id}, {len(data['tables'])}표 / {total_rows}행")

        if not dry_run:
            new_chunk = {
                "id": chunk_id,
                "section_id": sid,
                "title": data["title"],
                "text": data["text"][:500] if data["text"] else "",
                "tables": json.dumps(data["tables"], ensure_ascii=False),
                "department": "",
                "chapter": "",
                "section": "",
                "notes": json.dumps([]),
                "conditions": json.dumps([]),
                "cross_references": json.dumps([]),
                "revision_year": "",
            }
            supabase.table("graph_chunks").insert(new_chunk).execute()
            print(f"     ✅ 생성 완료")

        created += 1

    print(f"\n{'=' * 60}")
    print(f"  생성: {created}건, 스킵: {skipped}건")
    print("=" * 60)


if __name__ == "__main__":
    main()
