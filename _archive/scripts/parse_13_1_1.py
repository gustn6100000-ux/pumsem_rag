import os
import re
import json
from bs4 import BeautifulSoup

def normalize_material(raw: str) -> str:
    cleaned = re.sub(r'\s+', '', raw)
    m = re.match(r'(.+?)(KSD\d+|A\d+|Type\d+)', cleaned)
    if m:
        return f"{m.group(1)}({m.group(2)})"
    return cleaned

# Hardcoded multiplier tables according to Phase 1.5-C spec (Table 15)
# Note: "가산함" means apply as `base * (1 + pct/100)` for "용접식" only.
MULTIPLIERS = {
    'Cr합금강관(A335-P1,P2,P3,P11,P12)': { 50: 25.0, 80: 27.5, 100: 30.0, 125: 31.5, 150: 34.5, 200: 39.0, 250: 42.5, 300: 45.0, 350: 49.0, 400: 52.5, 450: 59.0, 500: 65.0, 550: 69.0, 600: 73.0 },
    '스텐레스강관(Type304,309,310,316)': { 50: 47.5, 80: 52.0, 100: 57.0, 125: 60.0, 150: 63.5, 200: 72.0, 250: 81.0, 300: 86.0, 350: 93.0, 400: 100.0, 450: 112.0, 500: 123.5, 550: 131.0, 600: 139.0 },
    '알루미늄관': { 50: 69.0, 80: 76.0, 100: 82.5, 125: 87.0, 150: 95.0, 200: 107.0, 250: 117.0, 300: 124.0, 350: 135.0, 400: 144.0, 450: 162.0, 500: 179.0, 550: 190.0, 600: 201.0 },
    '동관': { 50: 20.0, 80: 23.0, 100: 25.0, 125: 27.5, 150: 30.0, 200: 50.0, 250: 75.0, 300: 80.0, 350: 100.0, 400: 110.0, 450: 115.0, 500: 125.0, 550: 133.0, 600: 140.0 },
    '황동관': { 50: 20.0, 80: 23.0, 100: 25.0, 125: 27.5, 150: 30.0, 200: 50.0, 250: 75.0, 300: 80.0, 350: 100.0, 400: 110.0, 450: 115.0, 500: 125.0, 550: 133.0, 600: 140.0 },
}

def get_interpolated_multiplier(mat_name, spec_mm):
    if mat_name not in MULTIPLIERS: return 0.0
    mapping = MULTIPLIERS[mat_name]
    sizes = sorted(mapping.keys())
    if spec_mm <= sizes[0]: return mapping[sizes[0]]
    if spec_mm >= sizes[-1]: return mapping[sizes[-1]]
    lower, upper = sizes[0], sizes[-1]
    for i in range(len(sizes)-1):
        if sizes[i] <= spec_mm <= sizes[i+1]:
            lower = sizes[i]
            upper = sizes[i+1]
            break
    if lower == upper: return mapping[lower]
    ratio = (spec_mm - lower) / (upper - lower)
    return mapping[lower] + (mapping[upper] - mapping[lower]) * ratio


def parse_md(filepath: str):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    soup = BeautifulSoup(content, 'html.parser')
    tables = soup.find_all('table')
    
    # 1. Base Carbon Steel (Tables 0, 1, 2)
    t1 = tables[0]
    t2_part1 = tables[1]
    t2_part2 = tables[2]
    t1_rows = t1.find('tbody').find_all('tr')
    t2_rows = t2_part1.find('tbody').find_all('tr') + t2_part2.find('tbody').find_all('tr')
    if '배 관 구 분' in t1_rows[-1].get_text(): t1_rows = t1_rows[:-1]
    t2_rows = [tr for tr in t2_rows if len(tr.find_all('td')) >= 10]
    
    # 2. Pressure Carbon Steel (압력배관용탄소강관 KSD3562 SCH#40)
    # Using Table 12 and 13 as base for reading raw data
    t12 = tables[12]
    t13 = tables[13]
    t12_rows = t12.find('tbody').find_all('tr')
    t13_rows = t13.find('tbody').find_all('tr')
    t13_rows = [tr for tr in t13_rows if len(tr.find_all('td')) >= 10]
    
    p_material_name = "압력배관용탄소강관(KSD3562-SCH40)"
    sql_statements = []
    
    def parse_float(val: str) -> float:
        val = val.strip()
        if not val or val == '-': return 0.0
        try: return float(val)
        except: return 0.0

    def add_entry(mat, idx, out_dia, thick, weight, pipe_loc, joint, job, qty, unit='인/100m'):
        if qty > 0:
            sql_statements.append({
                'section_code': '13-1-1',
                'section_name': '플랜트 배관 설치',
                'material': mat,
                'spec_mm': idx,
                'outer_dia_mm': out_dia,
                'thickness_mm': thick,
                'unit_weight': weight,
                'pipe_location': pipe_loc,
                'joint_type': joint,
                'job_name': job,
                'quantity': round(qty, 3),
                'quantity_unit': unit,
                'source_page': 697
            })

    carbon_base_records = []
    material_name = "배관용탄소강관(KSD3507)"
    
    for r1, r2 in zip(t1_rows, t2_rows):
        tds1 = r1.find_all('td')
        tds2 = r2.find_all('td')
        col_offset = 1 if tds1[0].has_attr('rowspan') else 0
        
        try: spec_mm = int(tds1[col_offset].get_text().strip())
        except ValueError: continue
        
        outer_dia_mm = parse_float(tds1[col_offset+1].get_text())
        thickness_mm = parse_float(tds1[col_offset+2].get_text())
        unit_weight = parse_float(tds1[col_offset+3].get_text())
        
        v1 = [parse_float(td.get_text()) for td in tds1[col_offset+4:]]
        v2 = [parse_float(td.get_text()) for td in tds2]
        
        if len(v1) < 4 or len(v2) < 10: continue
        
        t1_in_weld_weld, t1_in_weld_pipe, t1_in_weld_spec, t1_in_screw_pipe = v1[:4]
        t2_in_screw_weld, t2_in_screw_spec, t2_in_screw_ton = v2[0], v2[1], v2[2]
        t2_out_weld_weld, t2_out_weld_pipe, t2_out_weld_spec = v2[3], v2[4], v2[5]
        t2_out_screw_pipe, t2_out_screw_weld, t2_out_screw_spec, t2_out_screw_ton = v2[6], v2[7], v2[8], v2[9]
        
        carbon_base_records.append({
            'spec_mm': spec_mm, 'out_dia': outer_dia_mm, 'thick': thickness_mm, 'weight': unit_weight,
            'vals': {
                ('옥내', '용접식', '플랜트용접공'): t1_in_weld_weld,
                ('옥내', '용접식', '플랜트배관공'): t1_in_weld_pipe,
                ('옥내', '용접식', '특별인부'): t1_in_weld_spec,
                ('옥내', '나사식', '플랜트배관공'): t1_in_screw_pipe,
                ('옥내', '나사식', '플랜트용접공'): t2_in_screw_weld,
                ('옥내', '나사식', '특별인부'): t2_in_screw_spec,
                ('옥내', '나사식', '톤당'): (t2_in_screw_ton, '인/ton'),
                ('옥외', '용접식', '플랜트용접공'): t2_out_weld_weld,
                ('옥외', '용접식', '플랜트배관공'): t2_out_weld_pipe,
                ('옥외', '용접식', '특별인부'): t2_out_weld_spec,
                ('옥외', '나사식', '플랜트배관공'): t2_out_screw_pipe,
                ('옥외', '나사식', '플랜트용접공'): t2_out_screw_weld,
                ('옥외', '나사식', '특별인부'): t2_out_screw_spec,
                ('옥외', '나사식', '톤당'): (t2_out_screw_ton, '인/ton')
            }
        })
        
        for k, v in carbon_base_records[-1]['vals'].items():
            if type(v) is tuple: add_entry(material_name, spec_mm, outer_dia_mm, thickness_mm, unit_weight, k[0], k[1], k[2], v[0], v[1])
            else: add_entry(material_name, spec_mm, outer_dia_mm, thickness_mm, unit_weight, k[0], k[1], k[2], v)

    # PARSE PRESSURE CARBON STEEL (압력배관용)
    for r1, r2 in zip(t12_rows, t13_rows):
        tds1 = r1.find_all('td')
        tds2 = r2.find_all('td')
        col_offset = 1 if tds1[0].has_attr('rowspan') else 0
        try: spec_mm = int(tds1[col_offset].get_text().strip())
        except ValueError: continue
            
        outer_dia_mm = parse_float(tds1[col_offset+1].get_text())
        thickness_mm = parse_float(tds1[col_offset+2].get_text())
        unit_weight = parse_float(tds1[col_offset+3].get_text())
        
        v1 = [parse_float(td.get_text()) for td in tds1[col_offset+4:]]
        v2 = [parse_float(td.get_text()) for td in tds2]
        
        if len(v1) < 4 or len(v2) < 10: continue
        
        t1_in_weld_weld, t1_in_weld_pipe, t1_in_weld_spec, t1_in_screw_pipe = v1[:4]
        t2_in_screw_weld, t2_in_screw_spec, t2_in_screw_ton = v2[0], v2[1], v2[2]
        t2_out_weld_weld, t2_out_weld_pipe, t2_out_weld_spec = v2[3], v2[4], v2[5]
        t2_out_screw_pipe, t2_out_screw_weld, t2_out_screw_spec, t2_out_screw_ton = v2[6], v2[7], v2[8], v2[9]
        
        vals = {
            ('옥내', '용접식', '플랜트용접공'): t1_in_weld_weld,
            ('옥내', '용접식', '플랜트배관공'): t1_in_weld_pipe,
            ('옥내', '용접식', '특별인부'): t1_in_weld_spec,
            ('옥내', '나사식', '플랜트배관공'): t1_in_screw_pipe,
            ('옥내', '나사식', '플랜트용접공'): t2_in_screw_weld,
            ('옥내', '나사식', '특별인부'): t2_in_screw_spec,
            ('옥내', '나사식', '톤당'): (t2_in_screw_ton, '인/ton'),
            ('옥외', '용접식', '플랜트용접공'): t2_out_weld_weld,
            ('옥외', '용접식', '플랜트배관공'): t2_out_weld_pipe,
            ('옥외', '용접식', '특별인부'): t2_out_weld_spec,
            ('옥외', '나사식', '플랜트배관공'): t2_out_screw_pipe,
            ('옥외', '나사식', '플랜트용접공'): t2_out_screw_weld,
            ('옥외', '나사식', '특별인부'): t2_out_screw_spec,
            ('옥외', '나사식', '톤당'): (t2_out_screw_ton, '인/ton')
        }
        for k, v in vals.items():
            if type(v) is tuple: add_entry(p_material_name, spec_mm, outer_dia_mm, thickness_mm, unit_weight, k[0], k[1], k[2], v[0], v[1])
            else: add_entry(p_material_name, spec_mm, outer_dia_mm, thickness_mm, unit_weight, k[0], k[1], k[2], v)


    # SYNTHESIZE OTHER 5 MATERIALS 
    for mat_name in MULTIPLIERS.keys():
        for base in carbon_base_records:
            spec_mm = base['spec_mm']
            pct = get_interpolated_multiplier(mat_name, spec_mm)
            for k, v in base['vals'].items():
                qty = v[0] if type(v) is tuple else v
                unit = v[1] if type(v) is tuple else '인/100m'
                
                # 가산 비율은 용접식에만 적용 (Construction estimating practice)
                if k[1] == '용접식':
                    qty = qty * (1.0 + (pct / 100.0))
                
                add_entry(mat_name, spec_mm, base['out_dia'], base['thick'], base['weight'], k[0], k[1], k[2], qty, unit)


    print(f"Generated {len(sql_statements)} records.")
    with open('records_13_1_1.json', 'w', encoding='utf-8') as out:
        json.dump(sql_statements, out, ensure_ascii=False, indent=2)
    print(f"Saved {len(sql_statements)} records to records_13_1_1.json")

if __name__ == '__main__':
    md_path = r"g:\My Drive\Antigravity\pipeline\download_file\20260208_747-878 OKOK.md"
    parse_md(md_path)
