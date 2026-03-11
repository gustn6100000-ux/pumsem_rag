import json

with open(r'g:\My Drive\Antigravity\pjt\pumsem\pipeline\scripts\output\audit_report.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

missing = sum(v.get('diff', 0) for v in data.get('missing_tables', {}).values())
print(f'Total missing tables across all sections: {missing}')

if '8-3-8' in data.get('missing_tables', {}):
    print(f"8-3-8 diff: {data['missing_tables']['8-3-8']['diff']}")
else:
    print('8-3-8 not found in missing_tables report (Fixed!)')
