"""
중복 테이블 탐지 및 제거 스크립트
같은 base_section_id 내에서 여러 chunk에 동일한 테이블이 존재하는 경우를 정리.

중복 판정 기준:
  - 같은 base_section_id의 chunk들 중
  - headers 상위 3개가 동일하고
  - 행 수가 같거나 비슷한 테이블

전략:
  - fix_missing_tables.py로 추가한 테이블이 이미 #N chunk에 존재할 경우
  - 보완한 chunk(base_id, #2, #3 등에서 tables가 적은 것)에서 중복 테이블 제거

Usage:
    python scripts/dedup_tables.py [--dry-run]
"""
import os
import json
import sys
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")


def table_signature(tbl: dict) -> str:
    """테이블의 고유 시그니처 생성 (헤더 상위 3개 + 행 수)"""
    headers = tbl.get("headers", [])
    h_key = "|".join(str(h).strip()[:20] for h in headers[:4])
    row_count = len(tbl.get("rows", []))
    return f"{h_key}:{row_count}"


def table_header_key(tbl: dict) -> str:
    """헤더만으로 키 생성 (행 수 무시)"""
    headers = tbl.get("headers", [])
    return "|".join(str(h).strip()[:20] for h in headers[:4])


def get_base_sid(section_id: str) -> str:
    return section_id.split("#")[0].split("-A")[0].split("-B")[0].split("-C")[0].split("-D")[0].split("-E")[0].split("-F")[0]


def main():
    dry_run = "--dry-run" in sys.argv
    print("=" * 60)
    print(f"  중복 테이블 제거 스크립트 {'(DRY RUN)' if dry_run else ''}")
    print("=" * 60)

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    # 1. 전체 chunk 로드
    all_chunks = []
    offset = 0
    while True:
        resp = supabase.table("graph_chunks") \
            .select("id, section_id, title, tables") \
            .range(offset, offset + 999) \
            .execute()
        batch = resp.data or []
        if not batch:
            break
        all_chunks.extend(batch)
        offset += len(batch)
        if len(batch) < 1000:
            break

    print(f"  총 {len(all_chunks)}개 chunk 로드\n")

    # 2. base_section_id별 그룹핑
    groups = defaultdict(list)
    for c in all_chunks:
        sid = c.get("section_id", "")
        base = get_base_sid(sid)
        groups[base].append(c)

    # 3. 각 그룹에서 중복 테이블 탐지
    total_dedup = 0
    total_removed_rows = 0
    updated_chunks = []

    for base_sid, chunks in sorted(groups.items()):
        if len(chunks) < 2:
            continue

        # 각 chunk의 각 테이블에 대해 시그니처 생성
        # 같은 base_sid 내에서 동일 시그니처가 여러 chunk에 존재하면 중복
        sig_to_chunks = defaultdict(list)  # sig -> [(chunk_id, tbl_idx, tbl)]
        for c in chunks:
            tables = c.get("tables") or []
            if isinstance(tables, str):
                try:
                    tables = json.loads(tables)
                except:
                    continue
            for tidx, tbl in enumerate(tables):
                sig = table_signature(tbl)
                sig_to_chunks[sig].append((c["id"], tidx, tbl))

        # 중복 발견: 같은 sig가 2개 이상 chunk에 존재
        for sig, entries in sig_to_chunks.items():
            if len(entries) <= 1:
                continue

            chunk_ids = set(e[0] for e in entries)
            if len(chunk_ids) <= 1:
                continue  # 같은 chunk 내 중복은 무시

            # 어떤 chunk에서 제거할지 결정:
            # - #N 접미사가 없는 chunk(원본)는 보존
            # - fix_missing_tables.py로 추가한 chunk (tables가 많은 것)에서 제거
            # - C-NEW chunk는 보존 (유일한 데이터일 수 있음)
            
            # 간단한 전략: 가장 큰 tables 배열을 가진 chunk에서 중복 제거
            entries_by_chunk = defaultdict(list)
            for cid, tidx, tbl in entries:
                entries_by_chunk[cid].append((tidx, tbl))

            # 보존할 chunk: 원래 #N chunk (section_id에 #가 있는 것)
            # 제거 대상: base_id chunk (보완으로 추가된 것)
            for cid, tbl_entries in entries_by_chunk.items():
                chunk_obj = next(c for c in chunks if c["id"] == cid)
                sid = chunk_obj.get("section_id", "")
                
                # #N chunk는 보존
                if "#" in sid:
                    continue
                # C-NEW chunk는 보존
                if cid.startswith("C-NEW"):
                    continue

                # base_id chunk에서 이 중복 테이블 제거 대상으로 마킹
                for tidx, tbl in tbl_entries:
                    total_dedup += 1
                    total_removed_rows += len(tbl.get("rows", []))

        # 실제 제거 수행: 각 base_id chunk에서 #N에 이미 있는 테이블 제거
        for c in chunks:
            sid = c.get("section_id", "")
            cid = c["id"]
            
            # #N chunk나 C-NEW는 건드리지 않음
            if "#" in sid or cid.startswith("C-NEW"):
                continue

            tables = c.get("tables") or []
            if isinstance(tables, str):
                try:
                    tables = json.loads(tables)
                except:
                    continue
            if not tables:
                continue

            # 이 chunk의 각 테이블이 같은 base_sid의 다른 chunk에 존재하는지 확인
            other_sigs = set()
            for other_c in chunks:
                if other_c["id"] == cid:
                    continue
                other_tables = other_c.get("tables") or []
                if isinstance(other_tables, str):
                    try:
                        other_tables = json.loads(other_tables)
                    except:
                        continue
                for tbl in other_tables:
                    other_sigs.add(table_signature(tbl))

            # 중복 테이블 제거
            new_tables = []
            removed = 0
            for tbl in tables:
                sig = table_signature(tbl)
                if sig in other_sigs:
                    removed += 1
                else:
                    new_tables.append(tbl)

            if removed > 0:
                print(f"  [{base_sid}] {cid} ({c['title'][:25]})")
                print(f"    기존 {len(tables)}표 → {len(new_tables)}표 (중복 {removed}표 제거)")
                updated_chunks.append((cid, new_tables, removed))

    print(f"\n{'=' * 60}")
    print(f"  중복 제거 대상: {len(updated_chunks)}개 chunk")
    print(f"  제거 테이블: {sum(r for _, _, r in updated_chunks)}개")
    print("=" * 60)

    if not dry_run and updated_chunks:
        for cid, new_tables, _ in updated_chunks:
            supabase.table("graph_chunks") \
                .update({"tables": new_tables}) \
                .eq("id", cid) \
                .execute()
        print(f"\n  ✅ {len(updated_chunks)}개 chunk 업데이트 완료")
    elif dry_run:
        print(f"\n  🏷️ DRY RUN - 변경 없음")


if __name__ == "__main__":
    main()
