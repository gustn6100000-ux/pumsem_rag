"""
Phase 1 파이프라인 샘플링 검증 스크립트
- MD 원본 파일 분석
- chunks.json과 대조 검증
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import json
import os
import re
import random
from pathlib import Path
from collections import Counter

MD_DIR = Path(r'G:\내 드라이브\Antigravity\python_code\download_file')
CHUNKS_PATH = Path(r'G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json')

# Load chunks
with open(CHUNKS_PATH, 'r', encoding='utf-8') as f:
    data = json.load(f)
chunks = data['chunks']

# Build lookup by section_id
section_chunks = {}
for c in chunks:
    sid = c['section_id']
    if sid not in section_chunks:
        section_chunks[sid] = []
    section_chunks[sid].append(c)

# ========== 1. MD FILE OVERVIEW ==========
print("=" * 70)
print("1. MD 파일 현황")
print("=" * 70)

md_files = sorted(MD_DIR.glob("*.md"))
print(f"총 MD 파일: {len(md_files)}개\n")

total_lines = 0
total_size = 0
file_stats = []
for f in md_files:
    content = f.read_text(encoding='utf-8')
    lines = content.count('\n') + 1
    size_kb = f.stat().st_size / 1024
    total_lines += lines
    total_size += f.stat().st_size
    
    # Count SECTION markers
    section_markers = len(re.findall(r'<!-- SECTION:', content))
    context_markers = len(re.findall(r'<!-- CONTEXT:', content))
    html_tables = len(re.findall(r'<table', content, re.IGNORECASE))
    
    file_stats.append({
        'name': f.name,
        'lines': lines,
        'size_kb': size_kb,
        'sections': section_markers,
        'context_markers': context_markers,
        'tables': html_tables
    })

print(f"{'파일명':<45} {'줄수':>7} {'크기(KB)':>8} {'SECTION':>8} {'CONTEXT':>8} {'테이블':>7}")
print("-" * 90)
for s in file_stats:
    print(f"{s['name']:<45} {s['lines']:>7} {s['size_kb']:>8.0f} {s['sections']:>8} {s['context_markers']:>8} {s['tables']:>7}")

print(f"\n총합: {total_lines:,}줄, {total_size/1024/1024:.1f}MB, "
      f"SECTION마커 {sum(s['sections'] for s in file_stats)}개, "
      f"CONTEXT마커 {sum(s['context_markers'] for s in file_stats)}개, "
      f"HTML테이블 {sum(s['tables'] for s in file_stats)}개")

# ========== 2. SECTION MARKER SAMPLING ==========
print("\n" + "=" * 70)
print("2. SECTION 마커 → 청크 매핑 샘플 검증 (5개 파일)")
print("=" * 70)

# Pick 5 diverse files
sample_files = random.sample(md_files, min(5, len(md_files)))
for f in sample_files:
    content = f.read_text(encoding='utf-8')
    markers = re.findall(r'<!-- SECTION:\s*(\S+)', content)
    
    matched = 0
    missing = []
    for m in markers:
        if m in section_chunks:
            matched += 1
        else:
            missing.append(m)
    
    rate = matched / len(markers) * 100 if markers else 0
    status = "[OK]" if rate == 100 else "[NG]"
    print(f"\n{status} {f.name}")
    print(f"  SECTION 마커: {len(markers)}개, 청크 매핑: {matched}개 ({rate:.0f}%)")
    if missing:
        print(f"  누락: {missing[:5]}")

# ========== 3. CONTENT INTEGRITY SAMPLING ==========
print("\n" + "=" * 70)
print("3. 콘텐츠 무결성 샘플 검증 (10개 섹션)")
print("=" * 70)

# Random sample 10 sections
all_section_ids = list(section_chunks.keys())
sample_sids = random.sample(all_section_ids, min(10, len(all_section_ids)))

for sid in sample_sids:
    chunk_list = section_chunks[sid]
    first_chunk = chunk_list[0]
    
    # Find in MD file
    src_file = first_chunk.get('source_file', '')
    md_path = MD_DIR / src_file
    
    found_in_md = False
    md_context = ""
    if md_path.exists():
        md_content = md_path.read_text(encoding='utf-8')
        # Check if section marker exists in file
        if f'SECTION: {sid}' in md_content or f'SECTION: {sid} ' in md_content:
            found_in_md = True
            # Get some context around the marker
            idx = md_content.find(f'SECTION: {sid}')
            md_context = md_content[idx:idx+100].replace('\n', ' ')[:80]
    
    # Chunk analysis
    total_tokens = sum(c['token_count'] for c in chunk_list)
    total_tables = sum(len(c.get('tables', [])) for c in chunk_list)
    total_notes = sum(len(c.get('notes', [])) for c in chunk_list)
    has_text = any(c.get('text', '').strip() for c in chunk_list)
    
    status = "[OK]" if found_in_md and has_text else ("[--]" if found_in_md else "[NG]")
    print(f"\n{status} 섹션 {sid} | \"{first_chunk['title']}\" | {first_chunk['department']}")
    print(f"  청크: {len(chunk_list)}개, 토큰합: {total_tokens}, 테이블: {total_tables}, 주석: {total_notes}")
    print(f"  소스: {src_file}")
    print(f"  MD내존재: {'예' if found_in_md else '아니오'} | 텍스트있음: {'예' if has_text else '아니오'}")
    if md_context:
        print(f"  MD컨텍스트: {md_context}")

# ========== 4. TABLE COUNT CROSS-CHECK ==========
print("\n" + "=" * 70)
print("4. 테이블 수 크로스체크 (원본 vs 청크)")
print("=" * 70)

# Count HTML tables per source file in chunks
chunk_tables_by_file = Counter()
for c in chunks:
    src = c.get('source_file', '')
    chunk_tables_by_file[src] += len(c.get('tables', []))

# Compare with MD file HTML tables
print(f"\n{'파일명':<45} {'MD테이블':>8} {'청크테이블':>10} {'차이':>6}")
print("-" * 75)
total_md_tables = 0
total_chunk_tables = 0
for s in file_stats:
    chunk_t = chunk_tables_by_file.get(s['name'], 0)
    diff = chunk_t - s['tables']
    marker = "<!>" if abs(diff) > 5 else ""
    print(f"{s['name']:<45} {s['tables']:>8} {chunk_t:>10} {diff:>+6} {marker}")
    total_md_tables += s['tables']
    total_chunk_tables += chunk_t

print(f"\n총합: MD={total_md_tables}, 청크={total_chunk_tables}, 차이={total_chunk_tables-total_md_tables:+d}")

# ========== 5. OKOKI FILE CHECK ==========
print("\n" + "=" * 70)
print("5. OKOKI 파일 확인 (비표준 파일명)")
print("=" * 70)
okoki_files = [f for f in md_files if 'OKOKI' in f.name]
for f in okoki_files:
    content = f.read_text(encoding='utf-8')
    markers = re.findall(r'<!-- SECTION:\s*(\S+)', content)
    # Check if this file's sections are in chunks
    matched = sum(1 for m in markers if m in section_chunks)
    print(f"  {f.name}: SECTION마커 {len(markers)}개, 청크매핑 {matched}개")
    if markers and matched == 0:
        print(f"  <!> 이 파일의 섹션이 청크에 매핑되지 않음!")

# ========== 6. CHUNK TEXT QUALITY SAMPLING ==========
print("\n" + "=" * 70)
print("6. 청크 텍스트 품질 샘플링 (10개)")
print("=" * 70)

# Sample chunks with tables to check table parsing quality
table_chunks = [c for c in chunks if c.get('tables')]
sample_tc = random.sample(table_chunks, min(10, len(table_chunks)))

for c in sample_tc:
    t = c['tables'][0]
    row_count = len(t.get('rows', []))
    header_count = len(t.get('headers', []))
    raw_rows = t.get('raw_row_count', 0)
    parsed_rows = t.get('parsed_row_count', 0)
    
    # Check for empty cells
    empty_cells = 0
    total_cells = 0
    for row in t.get('rows', []):
        for v in row.values():
            total_cells += 1
            if str(v).strip() in ('', '-'):
                empty_cells += 1
    
    empty_pct = empty_cells / total_cells * 100 if total_cells else 0
    
    print(f"\n  {c['chunk_id']} | {c['section_id']} | \"{c['title']}\"")
    print(f"    테이블 {t['table_id']}: type={t['type']}, 헤더={header_count}개, 행={row_count}")
    print(f"    raw_rows={raw_rows}, parsed_rows={parsed_rows}, 빈셀={empty_pct:.0f}%")

print("\n" + "=" * 70)
print("검증 완료")
print("=" * 70)
