import os
import re
from glob import glob
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.environ.get('SUPABASE_URL'), os.environ.get('SUPABASE_SERVICE_ROLE_KEY'))

md_dir = r"g:\My Drive\Antigravity\pjt\pumsem\pipeline\download_file"
md_files = glob(os.path.join(md_dir, "*.md"))

# md_stats: section_id -> { "tables": X, "rows": Y }
md_stats = defaultdict(lambda: {"tables": 0, "rows": 0})

section_pattern = re.compile(r'<!--\s*SECTION:\s*([\d\-\#a-zA-Z가-힣]+)')
table_start_pattern = re.compile(r'<table')
tr_pattern = re.compile(r'<tr(\s|>)')

for filepath in md_files:
    with open(filepath, 'r', encoding='utf-8') as f:
        current_section = None
        in_table = False
        in_tbody = False
        
        for line in f:
            # SECTION 태그 캐치
            sec_match = section_pattern.search(line)
            if sec_match:
                current_section = sec_match.group(1).strip()
                in_table = False
                in_tbody = False
                continue
                
            if current_section:
                if table_start_pattern.search(line):
                    md_stats[current_section]["tables"] += 1
                    in_table = True
                    in_tbody = False
                
                if in_table:
                    if '<tbody' in line:
                        in_tbody = True
                    if '</tbody' in line:
                        in_tbody = False
                        
                    if in_tbody and tr_pattern.search(line):
                        md_stats[current_section]["rows"] += 1
                        
                    if '</table>' in line:
                        in_table = False

print(f"Scanned {len(md_files)} markdown files.")
print(f"Found {len([s for s in md_stats if md_stats[s]['tables'] > 0])} sections with tables in MD.")

# DB Stats
db_stats = defaultdict(lambda: {"tables": 0, "rows": 0})
print("Querying Supabase graph_chunks...")

# Pagination to fetch all chunks
all_chunks = []
start = 0
step = 1000
while True:
    res = supabase.table('graph_chunks').select('section_id, tables').range(start, start + step - 1).execute()
    data = res.data
    all_chunks.extend(data)
    if len(data) < step:
        break
    start += step

print(f"Retrieved {len(all_chunks)} chunks from DB.")

for chunk in all_chunks:
    sec = chunk['section_id']
    if not sec:
        continue
    
    # DB 상에는 13-2-4#V 처럼 해시가 붙어있을 수 있으므로 기본 섹션을 추출해서 비교 (MD도 해시가 있는지 확인)
    # MD의 주석은 보통 <!-- SECTION: 13-2-4 --> 로 끝남.
    sec_base = sec.split('#')[0] if '#' in sec else sec
    
    tables = chunk.get('tables')
    if tables and isinstance(tables, list):
        db_stats[sec_base]["tables"] += len(tables)
        for t in tables:
            r_count = t.get('parsed_row_count', len(t.get('rows', [])))
            db_stats[sec_base]["rows"] += r_count

discrepancies = []
all_keys = set(list(md_stats.keys()) + list(db_stats.keys()))

for sec_id in all_keys:
    m_t = md_stats.get(sec_id, {"tables": 0})["tables"]
    m_r = md_stats.get(sec_id, {"rows": 0})["rows"]
    d_t = db_stats.get(sec_id, {"tables": 0})["tables"]
    d_r = db_stats.get(sec_id, {"rows": 0})["rows"]
    
    if m_t != d_t or m_r != d_r:
        if m_t > 0 or d_t > 0:
            discrepancies.append({
                "section": sec_id,
                "md_tables": m_t,
                "md_rows": m_r,
                "db_tables": d_t,
                "db_rows": d_r,
                "diff_tables": m_t - d_t,
                "diff_rows": m_r - d_r
            })

# diff_rows 의 절댓값이 큰 순서로 정렬
discrepancies.sort(key=lambda x: abs(x["diff_rows"]), reverse=True)

report = f"--- DISCREPANCY REPORT ---\n"
report += f"Total sections with table/row differences: {len(discrepancies)}\n\n"
for d in discrepancies:
    report += f"[{d['section']}] MD Tables: {d['md_tables']} (Rows: {d['md_rows']}) | DB Tables: {d['db_tables']} (Rows: {d['db_rows']}) | Diff (Rows): {d['diff_rows']}\n"

with open("audit_report.txt", "w", encoding="utf-8") as f:
    f.write(report)
print(f"Report written to audit_report.txt. Found {len(discrepancies)} discrepancies.")
