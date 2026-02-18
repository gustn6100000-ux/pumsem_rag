import json

d = json.loads(open(r'G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json', 'r', encoding='utf-8').read())
chunks = d['chunks']
print(f'total chunks: {len(chunks)}')
c = chunks[0]
print(f'chunk keys: {list(c.keys())}')
print(f'chunk_id: {c.get("chunk_id")}')
print(f'section_code: {c.get("section_code")}')
has_content = sum(1 for ch in chunks if ch.get('content'))
has_tables = sum(1 for ch in chunks if ch.get('tables'))
has_notes = sum(1 for ch in chunks if ch.get('notes'))
has_entities = sum(1 for ch in chunks if ch.get('entity_ids') or ch.get('entities'))
print(f'has content: {has_content}')
print(f'has tables: {has_tables}')
print(f'has notes: {has_notes}')
print(f'has entity_ids: {has_entities}')
