import json

# 1. normalized_entities.json 구조
data = json.loads(open(r'G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json', 'r', encoding='utf-8').read())
print('=== TOP KEYS ===')
for k in data.keys():
    v = data[k]
    if isinstance(v, list):
        print(f'  {k}: list[{len(v)}]')
    elif isinstance(v, dict):
        print(f'  {k}: dict[{len(v)} keys]')
    else:
        print(f'  {k}: {type(v).__name__} = {v}')

e = data['entities'][0]
print(f'\nENTITY KEYS: {list(e.keys())}')

ext = data['extractions'][0]
print(f'\nEXTRACTION KEYS: {list(ext.keys())}')

rel = ext['relationships'][0]
print(f'\nRELATIONSHIP KEYS: {list(rel.keys())}')
print(f'REL SAMPLE: {json.dumps(rel, ensure_ascii=False)}')

gr = data.get('global_relationships', [])
print(f'\nGLOBAL_REL count: {len(gr)}')
if gr:
    print(f'GLOBAL_REL KEYS: {list(gr[0].keys())}')
    print(f'GLOBAL_REL SAMPLE: {json.dumps(gr[0], ensure_ascii=False)}')

# 2. chunks.json 구조
chunks_data = json.loads(open(r'G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json', 'r', encoding='utf-8').read())
chunks = chunks_data.get('chunks', [])
print(f'\n=== CHUNKS ===')
print(f'total: {len(chunks)}')
if chunks:
    c = chunks[0]
    print(f'CHUNK KEYS: {list(c.keys())}')
    print(f'CHUNK SAMPLE id={c.get("chunk_id","?")}, section={c.get("section_code","?")}')
