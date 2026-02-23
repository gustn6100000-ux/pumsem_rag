import json
import urllib.request
import os
import sys

from dotenv import load_dotenv

# 스크립트 위치 기준으로 상위 폴더(pipeline)의 .env 로드
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PIPELINE_DIR = os.path.dirname(SCRIPT_DIR)
load_dotenv(os.path.join(PIPELINE_DIR, '.env'))

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not url or not key:
    print("Missing SUPABASE credentials in .env")
    sys.exit(1)

files_to_seed = [
    os.path.join(SCRIPT_DIR, 'records_13_1_1.json'),
    os.path.join(SCRIPT_DIR, 'records_13_3_1.json')
]
for file_path in files_to_seed:
    print(f"Loading {file_path}...")
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Deduplicate data based on the unique constraint to avoid "affect row a second time" error
    unique_data = {}
    for row in data:
        key_tuple = (
            row.get('section_code'),
            row.get('material'),
            row.get('spec_mm'),
            row.get('thickness_mm'),
            row.get('pipe_location'),
            row.get('joint_type'),
            row.get('job_name')
        )
        unique_data[key_tuple] = row
    
    data = list(unique_data.values())

    # Split into chunks of 100 for safety against payload limits
    chunk_size = 100
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        conflict_cols = "section_code,material,spec_mm,thickness_mm,pipe_location,joint_type,job_name"
        req = urllib.request.Request(f"{url}/rest/v1/complex_table_specs?on_conflict={conflict_cols}", data=json.dumps(chunk).encode('utf-8'))
        req.add_header('apikey', key)
        req.add_header('Authorization', f'Bearer {key}')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Prefer', 'resolution=merge-duplicates')

        try:
            response = urllib.request.urlopen(req)
            print(f"Batch {i//chunk_size + 1} Success. HTTP Status: {response.getcode()}")
        except urllib.error.HTTPError as e:
            print(f"Batch {i//chunk_size + 1} Error: HTTP {e.code} - {e.read().decode()}")
            sys.exit(1)

    print(f"Finished seeding {len(data)} records from {file_path}.")
