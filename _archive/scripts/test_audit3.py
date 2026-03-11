import json

with open(r'g:\My Drive\Antigravity\pjt\pumsem\pipeline\scripts\output\audit_report.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

missing = 0
sections = []
for sec in data.get('sections', []):
    md = sec.get('md_tables', 0)
    db = sec.get('db_tables', 0)
    if sec.get('status') == 'FAIL' and md > db:
        diff = md - db
        missing += diff
        sections.append((sec.get('section_id'), diff, md, db))

print(f'Total missing tables across all sections: {missing}')
sections.sort(key=lambda x: x[1], reverse=True)
print("Top missing sections:")
for s in sections[:15]:
    print(f"  {s[0]}: missing {s[1]} (md: {s[2]}, db: {s[3]})")
