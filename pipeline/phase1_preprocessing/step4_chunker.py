"""Step 4: 청크 생성 & 메타데이터 부착

정제된 섹션을 GraphRAG 및 벡터 검색에 최적화된 청크로 변환.
기본 단위: 항(X-Y-Z) 1개 = 1개 청크. 1,500토큰 초과 시 분할.
대형 섹션은 재귀적으로 분할하여 2,000토큰 이하로 유지.
"""
import json
from pathlib import Path
from tqdm import tqdm
from datetime import datetime

from config import (
    CLEANED_SECTIONS_FILE, CHUNKS_FILE, OUTPUT_DIR,
    MAX_CHUNK_TOKENS, MIN_CHUNK_TOKENS, QUALITY_THRESHOLDS,
)
from utils.token_counter import count_tokens, count_chunk_tokens


HARD_LIMIT = QUALITY_THRESHOLDS["max_token_limit"]  # 2000


def estimate_section_tokens(section: dict) -> int:
    """섹션의 총 토큰 수 추정"""
    total = count_tokens(section.get("clean_text", ""))
    for table in section.get("tables", []):
        total += count_tokens(json.dumps(table.get("rows", []), ensure_ascii=False))
    for note in section.get("notes", []):
        total += count_tokens(note)
    return total


def estimate_table_tokens(table: dict) -> int:
    """단일 테이블의 토큰 수 추정"""
    return count_tokens(json.dumps(table.get("rows", []), ensure_ascii=False))


def create_chunk(section: dict, chunk_id: str, text: str = None,
                 tables: list = None, notes: list = None) -> dict:
    """청크 딕셔너리 생성"""
    text = text if text is not None else section.get("clean_text", "")
    tables = tables if tables is not None else section.get("tables", [])
    notes = notes if notes is not None else section.get("notes", [])

    chunk = {
        "chunk_id": chunk_id,
        "section_id": section["section_id"],
        "title": section["title"],
        "department": section["department"],
        "chapter": section["chapter"],
        "section": section.get("toc_section", ""),
        "subsection": f"{section['section_id']} {section['title']}",
        "page": section["page"],
        "revision_year": section.get("revision_year", ""),
        "source_file": section["source_file"],
        "text": text,
        "tables": tables,
        "notes": notes,
        "conditions": section.get("conditions", []),
        "cross_references": section.get("cross_references", []),
        "unit_basis": section.get("unit_basis", ""),
    }
    chunk["token_count"] = count_chunk_tokens(chunk)
    return chunk


def split_large_table(table: dict, target_tokens: int) -> list[dict]:
    """대형 테이블을 행 단위로 분할하여 여러 테이블로 만듦"""
    rows = table.get("rows", [])
    headers = table.get("headers", [])
    table_id = table.get("table_id", "")
    table_type = table.get("type", "D_기타")

    if not rows:
        return [table]

    # 행을 토큰 기준으로 그룹핑
    sub_tables = []
    current_rows = []
    current_tokens = count_tokens(json.dumps(headers, ensure_ascii=False))

    for row in rows:
        row_tokens = count_tokens(json.dumps(row, ensure_ascii=False))
        if current_rows and current_tokens + row_tokens > target_tokens:
            sub_tables.append({
                **table,
                "table_id": f"{table_id}-{len(sub_tables)+1}",
                "rows": current_rows,
                "parsed_row_count": len(current_rows),
            })
            current_rows = [row]
            current_tokens = count_tokens(json.dumps(headers, ensure_ascii=False)) + row_tokens
        else:
            current_rows.append(row)
            current_tokens += row_tokens

    if current_rows:
        sub_tables.append({
            **table,
            "table_id": f"{table_id}-{len(sub_tables)+1}" if sub_tables else table_id,
            "rows": current_rows,
            "parsed_row_count": len(current_rows),
        })

    return sub_tables


def split_tables_into_groups(tables: list, target_tokens: int) -> list[list]:
    """테이블들을 토큰 기준으로 그룹으로 묶기. 대형 테이블은 행 분할."""
    if not tables:
        return []

    # 먼저 대형 테이블을 분할
    expanded_tables = []
    for table in tables:
        t_tokens = estimate_table_tokens(table)
        if t_tokens > target_tokens:
            expanded_tables.extend(split_large_table(table, target_tokens))
        else:
            expanded_tables.append(table)

    groups = []
    current_group = []
    current_tokens = 0

    for table in expanded_tables:
        t_tokens = estimate_table_tokens(table)
        if current_group and current_tokens + t_tokens > target_tokens:
            groups.append(current_group)
            current_group = [table]
            current_tokens = t_tokens
        else:
            current_group.append(table)
            current_tokens += t_tokens

    if current_group:
        groups.append(current_group)

    return groups


def split_text_into_parts(text: str, target_tokens: int) -> list[str]:
    """텍스트를 토큰 기준으로 분할"""
    if not text or not text.strip():
        return [""]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        # 단일 줄들로 분할 시도
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            return [text]
        paragraphs = lines

    parts = []
    current_part = []
    current_tokens = 0

    for para in paragraphs:
        p_tokens = count_tokens(para)
        if current_part and current_tokens + p_tokens > target_tokens:
            parts.append("\n\n".join(current_part))
            current_part = [para]
            current_tokens = p_tokens
        else:
            current_part.append(para)
            current_tokens += p_tokens

    if current_part:
        parts.append("\n\n".join(current_part))

    return parts


def split_section_recursive(section: dict) -> list[dict]:
    """섹션을 재귀적으로 분할하여 각 파트가 HARD_LIMIT 이하가 되도록 함.

    Returns:
        list[dict]: [{"text": str, "tables": list, "notes": list}, ...]
    """
    text = section.get("clean_text", "")
    tables = section.get("tables", [])
    notes = section.get("notes", [])

    text_tokens = count_tokens(text)
    table_tokens = sum(estimate_table_tokens(t) for t in tables)
    notes_tokens = sum(count_tokens(n) for n in notes)
    total = text_tokens + table_tokens + notes_tokens

    if total <= MAX_CHUNK_TOKENS:
        return [{"text": text, "tables": tables, "notes": notes}]

    parts = []

    # 전략 1: 테이블이 여러 개면 테이블별로 분할
    if len(tables) > 1:
        table_groups = split_tables_into_groups(tables, MAX_CHUNK_TOKENS)

        # 첫 그룹에 텍스트와 주석 포함
        if text_tokens <= MAX_CHUNK_TOKENS:
            parts.append({
                "text": text,
                "tables": table_groups[0] if table_groups else [],
                "notes": notes,
            })
            for grp in table_groups[1:]:
                parts.append({"text": "", "tables": grp, "notes": []})
        else:
            # 텍스트도 분할 필요
            text_parts = split_text_into_parts(text, MAX_CHUNK_TOKENS // 2)
            # 첫 텍스트 파트에 첫 테이블 그룹 + 주석
            parts.append({
                "text": text_parts[0] if text_parts else "",
                "tables": table_groups[0] if table_groups else [],
                "notes": notes,
            })
            for tp in text_parts[1:]:
                parts.append({"text": tp, "tables": [], "notes": []})
            for grp in table_groups[1:]:
                parts.append({"text": "", "tables": grp, "notes": []})

    # 전략 2: 테이블 1개 + 텍스트가 길 경우
    elif len(tables) == 1:
        t_tokens = estimate_table_tokens(tables[0])

        if t_tokens > MAX_CHUNK_TOKENS:
            # 대형 테이블: 행 분할 후 각각 별도 청크
            if text.strip():
                text_parts = split_text_into_parts(text, MAX_CHUNK_TOKENS)
                parts.append({"text": text_parts[0], "tables": [], "notes": notes})
                for tp in text_parts[1:]:
                    parts.append({"text": tp, "tables": [], "notes": []})
            sub_tables = split_large_table(tables[0], MAX_CHUNK_TOKENS)
            for st in sub_tables:
                parts.append({"text": "", "tables": [st], "notes": []})
        else:
            # 테이블은 작으니 텍스트를 분할
            remaining = MAX_CHUNK_TOKENS - t_tokens
            text_parts = split_text_into_parts(text, max(remaining, 300))
            parts.append({
                "text": text_parts[0] if text_parts else "",
                "tables": tables,
                "notes": notes,
            })
            for tp in text_parts[1:]:
                parts.append({"text": tp, "tables": [], "notes": []})

    # 전략 3: 테이블 없이 텍스트만 긴 경우
    else:
        text_parts = split_text_into_parts(text, MAX_CHUNK_TOKENS)
        for i, tp in enumerate(text_parts):
            parts.append({
                "text": tp,
                "tables": [],
                "notes": notes if i == 0 else [],
            })

    return parts if parts else [{"text": text, "tables": tables, "notes": notes}]


def section_to_chunks(section: dict, chunk_counter: int) -> tuple[list[dict], int]:
    """섹션을 청크로 변환. 반환: (청크 리스트, 다음 카운터)"""
    # 내용이 없는 섹션은 건너뜀
    if not section.get("has_content", True):
        text = section.get("clean_text", "")
        tables = section.get("tables", [])
        if not text.strip() and not tables:
            return [], chunk_counter

    chunk_counter += 1
    base_id = f"C-{chunk_counter:04d}"

    # 토큰 수 추정
    tokens = estimate_section_tokens(section)

    if tokens <= MAX_CHUNK_TOKENS:
        chunk = create_chunk(section, base_id)
        return [chunk], chunk_counter

    # 분할 필요
    parts = split_section_recursive(section)

    if len(parts) == 1:
        chunk = create_chunk(section, base_id)
        return [chunk], chunk_counter

    chunks = []
    for idx, part in enumerate(parts):
        suffix = chr(65 + idx) if idx < 26 else str(idx)  # A~Z, 이후 숫자
        cid = f"{base_id}-{suffix}"
        chunk = create_chunk(
            section, cid,
            text=part["text"],
            tables=part["tables"],
            notes=part["notes"],
        )
        chunks.append(chunk)

    return chunks, chunk_counter


def split_oversized_chunk(chunk: dict) -> list[dict]:
    """HARD_LIMIT 초과 청크를 추가 분할.

    텍스트와 테이블을 분리하고, 대형 테이블은 행 단위로 재분할.
    """
    if chunk["token_count"] <= HARD_LIMIT:
        return [chunk]

    text = chunk.get("text", "")
    tables = chunk.get("tables", [])
    notes = chunk.get("notes", [])
    base_id = chunk["chunk_id"]

    parts = []

    if text.strip() and tables:
        # 텍스트와 테이블을 별도 파트로 분리
        text_target = min(HARD_LIMIT - 100, MAX_CHUNK_TOKENS)
        text_parts = split_text_into_parts(text, text_target)
        for tp in text_parts:
            parts.append({"text": tp, "tables": [], "notes": notes if not parts else []})
        for table in tables:
            t_tokens = estimate_table_tokens(table)
            if t_tokens > HARD_LIMIT - 100:
                for st in split_large_table(table, HARD_LIMIT - 100):
                    parts.append({"text": "", "tables": [st], "notes": []})
            else:
                parts.append({"text": "", "tables": [table], "notes": []})
    elif tables:
        for table in tables:
            t_tokens = estimate_table_tokens(table)
            if t_tokens > HARD_LIMIT - 100:
                for st in split_large_table(table, HARD_LIMIT - 100):
                    parts.append({"text": "", "tables": [st], "notes": []})
            else:
                parts.append({
                    "text": "", "tables": [table],
                    "notes": notes if not parts else [],
                })
    else:
        text_parts = split_text_into_parts(text, HARD_LIMIT - 100)
        for i, tp in enumerate(text_parts):
            parts.append({"text": tp, "tables": [], "notes": notes if i == 0 else []})

    if len(parts) <= 1:
        return [chunk]  # 더 이상 분할 불가

    result = []
    for idx, part in enumerate(parts):
        suffix = chr(97 + idx) if idx < 26 else str(idx)
        new_chunk = {**chunk}
        new_chunk["chunk_id"] = f"{base_id}-{suffix}"
        new_chunk["text"] = part["text"]
        new_chunk["tables"] = part["tables"]
        new_chunk["notes"] = part["notes"]
        new_chunk["token_count"] = count_chunk_tokens(new_chunk)
        result.append(new_chunk)

    return result


def enforce_hard_limit(chunks: list[dict]) -> list[dict]:
    """모든 청크가 HARD_LIMIT 이하가 되도록 초과 청크를 반복 분할"""
    result = []
    for chunk in chunks:
        if chunk["token_count"] <= HARD_LIMIT:
            result.append(chunk)
        else:
            sub_chunks = split_oversized_chunk(chunk)
            result.extend(sub_chunks)
    return result


def run_step4(input_file: Path = None) -> list[dict]:
    """Step 4 실행: 청크 생성"""
    print("\n" + "=" * 60)
    print("Step 4: 청크 생성 & 메타데이터 부착")
    print("=" * 60)

    input_file = input_file or CLEANED_SECTIONS_FILE
    with open(input_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    sections = data["sections"]
    print(f"  입력 섹션: {len(sections)}개")

    all_chunks = []
    chunk_counter = 0
    split_count = 0
    skipped_count = 0

    for section in tqdm(sections, desc="  청크 생성"):
        chunks, chunk_counter = section_to_chunks(section, chunk_counter)
        if not chunks:
            skipped_count += 1
        elif len(chunks) > 1:
            split_count += 1
        all_chunks.extend(chunks)

    # HARD_LIMIT 초과 청크 강제 분할
    pre_enforce = len(all_chunks)
    over_before = sum(1 for c in all_chunks if c["token_count"] > HARD_LIMIT)
    all_chunks = enforce_hard_limit(all_chunks)
    enforced_split = len(all_chunks) - pre_enforce
    if enforced_split > 0:
        print(f"\n  하드리밋 초과 강제 분할: {over_before}개 → {enforced_split}개 추가 청크 생성")

    # 토큰 통계
    token_counts = [c["token_count"] for c in all_chunks]
    avg_tokens = sum(token_counts) / len(token_counts) if token_counts else 0
    max_tokens = max(token_counts) if token_counts else 0
    min_tokens = min(token_counts) if token_counts else 0
    over_limit = sum(1 for t in token_counts if t > HARD_LIMIT)

    print(f"\n  결과:")
    print(f"    총 청크 수: {len(all_chunks)}개")
    print(f"    분할된 섹션: {split_count}개")
    print(f"    건너뛴 섹션(빈 내용): {skipped_count}개")
    print(f"    토큰 통계:")
    print(f"      평균: {avg_tokens:.0f}")
    print(f"      최소: {min_tokens}")
    print(f"      최대: {max_tokens}")
    print(f"      2000토큰 초과: {over_limit}개 (분할 불가)")

    # 부문별 통계
    dept_counts = {}
    for c in all_chunks:
        dept = c["department"]
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
    print(f"    부문별:")
    for dept, count in sorted(dept_counts.items()):
        print(f"      {dept}: {count}개")

    # 저장
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "metadata": {
            "total_chunks": len(all_chunks),
            "total_sections": len(sections),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_files": data["metadata"].get("source_files", 0),
            "pilot_only": data["metadata"].get("pilot_only", False),
            "token_stats": {
                "avg": round(avg_tokens, 1),
                "min": min_tokens,
                "max": max_tokens,
                "over_2000": over_limit,
            },
        },
        "chunks": all_chunks,
    }
    with open(CHUNKS_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  저장: {CHUNKS_FILE}")
    return all_chunks


if __name__ == "__main__":
    run_step4()
