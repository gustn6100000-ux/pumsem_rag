import re
import json
from bs4 import BeautifulSoup

def parse_valve_tables(filepath: str):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    soup = BeautifulSoup(content, 'html.parser')
    tables = soup.find_all('table')

    # Find the tables that belong to the VALVE installation
    valve_tables = [t for t in tables if 'VALVE' in t.get_text() or '사용압력' in t.get_text()]
    
    if not valve_tables:
        print("Valve tables not found.")
        return

    sql_statements = []

    def parse_float(val: str) -> float:
        val = val.strip()
        if not val or val == '-': return 0.0
        try: return float(val)
        except: return 0.0

    def extract_spec_mm(val: str) -> int:
        val = val.strip()
        m = re.search(r'\d+', val)
        if m: return int(m.group(0))
        return 0

    def add_entry(spec, pressure, job, qty):
        if qty > 0:
            sql_statements.append({
                'section_code': '13-3-1',  # Using 13-3-1 as requested in plan
                'section_name': '플랜지 및 밸브류 설치',
                'material': '밸브/플랜지',  # General default
                'spec_mm': spec,
                'outer_dia_mm': 0.0,
                'thickness_mm': 0.0,
                'unit_weight': 0.0,
                'pipe_location': '기본',
                'joint_type': pressure,     # Repurpose joint_type for Pressure Rating
                'job_name': job,
                'quantity': round(qty, 3),
                'quantity_unit': '인/개소',    # Amount per unit
                'source_page': 790          # Approximate page within 13 chapter
            })

    # The columns in the valve tables:
    # 0: 구경
    # 1,2: 10.5K (배관공, 특별인부)
    # 3,4: 21~27.5K (배관공, 특별인부)
    # 5,6: 42~62K
    # 7,8: 105K
    # 9,10: 176K
    
    pressures = ["10.5K", "21.0~27.5K", "42~62K", "105K", "176K"]

    for table in valve_tables:
        rows = table.find_all('tr')
        # Skip headers, typically the first 2-3 rows. We'll identify data rows by looking at td count
        for row in rows:
            tds = row.find_all('td')
            if len(tds) >= 11:
                spec_str = tds[0].get_text()
                spec_mm = extract_spec_mm(spec_str)
                if spec_mm == 0: continue
                
                # Loop through the 5 pressure ratings
                for i in range(5):
                    pipe_qty = parse_float(tds[1 + i*2].get_text())
                    spec_qty = parse_float(tds[2 + i*2].get_text())
                    
                    add_entry(spec_mm, pressures[i], '플랜트배관공', pipe_qty)
                    add_entry(spec_mm, pressures[i], '특별인부', spec_qty)

    print(f"Generated {len(sql_statements)} records.")
    with open('records_13_3_1.json', 'w', encoding='utf-8') as out:
        json.dump(sql_statements, out, ensure_ascii=False, indent=2)
    print(f"Saved {len(sql_statements)} records to records_13_3_1.json")

if __name__ == '__main__':
    md_path = r"g:\My Drive\Antigravity\pipeline\download_file\20260208_747-878 OKOK.md"
    parse_valve_tables(md_path)
