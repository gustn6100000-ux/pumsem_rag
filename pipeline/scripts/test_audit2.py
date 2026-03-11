import json

with open(r'g:\My Drive\Antigravity\pjt\pumsem\pipeline\scripts\output\audit_report.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

missing = 0
for sec in data.get('sections', []):
    md = sec.get('md_tables', 0)
    db = sec.get('db_tables', 0)
    if sec.get('status') == 'FAIL' and md > db:
        missing += (md - db)

print(f'Total missing tables across all sections: {missing}')

for sec in data.get('sections', []):
    if sec.get('section_id') == '8-3-8':
        print(f"8-3-8 status: {sec.get('status')}")
        print(f"md_tables: {sec.get('md_tables')}, db_tables: {sec.get('db_tables')}")
