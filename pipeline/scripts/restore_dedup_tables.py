import os, sys, json
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(r'g:\My Drive\Antigravity\pjt\pumsem\pipeline\.env')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_SERVICE_ROLE_KEY'))

with open(r'g:\My Drive\Antigravity\pjt\pumsem\pipeline\phase1_output\chunks.json', 'r', encoding='utf-8') as f:
    local_data = json.load(f)

local_chunks = {c['chunk_id']: c.get('tables', []) for c in local_data.get('chunks', [])}

print("Fetching chunks from DB...")
# DB chunks
db_chunks = {}
offset = 0
while True:
    resp = supabase.table("graph_chunks").select("id, tables").range(offset, offset + 999).execute()
    batch = resp.data or []
    if not batch:
        break
    for c in batch:
        db_chunks[c['id']] = c.get('tables', [])
    offset += len(batch)
    if len(batch) < 1000:
        break

print(f"Loaded {len(local_chunks)} local chunks, {len(db_chunks)} DB chunks.")

updates = []
for cid, local_tables in local_chunks.items():
    if cid not in db_chunks:
        continue
    db_tables = db_chunks[cid]
    local_len = len(local_tables)
    db_len = len(db_tables)
    
    if local_len > db_len:
        print(f"[{cid}] local: {local_len} -> db: {db_len}. Restoring original tables...")
        # Since dedup blindly deleted, and we want original local tables, we can just union them or safely restore local.
        # Check if DB has tables that are NOT in local (added by fix script)
        def signature(tbl):
            return "|".join([str(h)[:20] for h in tbl.get('headers', [])[:4]]) + str(len(tbl.get('rows', [])))
        
        local_sigs = {signature(t) for t in local_tables}
        extra_db_tables = [t for t in db_tables if signature(t) not in local_sigs]
        
        # Merge local tables + any extra tables added explicitly by fix_missing_tables.py
        new_tables = local_tables + extra_db_tables
        updates.append((cid, new_tables))

print(f"\nFound {len(updates)} chunks to restore tables.")

with open('restore_plan.json', 'w', encoding='utf-8') as f:
    json.dump([{'chunk_id': c, 'new_len': len(t)} for c, t in updates], f, indent=2)

if '--execute' in sys.argv:
    print("Executing updates...")
    success = 0
    for cid, tables in updates:
        supabase.table('graph_chunks').update({'tables': tables}).eq('id', cid).execute()
        success += 1
    print(f"Restored {success} chunks successfully.")
else:
    print("Run with --execute to apply changes.")
