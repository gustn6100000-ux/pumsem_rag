import json

data = json.loads(open(r'G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json', 'r', encoding='utf-8').read())
notes = [e for e in data['entities'] if e.get('type') == 'Note']

print('=== Note 엔티티 샘플 5건 ===')
for i, n in enumerate(notes[:5]):
    eid = n.get('entity_id', '?')
    name = n.get('name', '?')
    keys = list(n.keys())
    print(f'\n[{i+1}] id={eid}')
    print(f'    name: {name}')
    print(f'    keys: {keys}')

# Note 관련 관계
rels = data.get('relationships', [])
note_rels = [r for r in rels if 'note' in str(r).lower()]
print(f'\n=== Note 관련 관계: {len(note_rels)}건 ===')
rel_types = {}
for r in note_rels:
    rt = r.get('relation', '?')
    rel_types[rt] = rel_types.get(rt, 0) + 1
print('관계 유형 분포:')
for k, v in sorted(rel_types.items(), key=lambda x: -x[1]):
    print(f'  {k}: {v}')
    
print('\n--- Note 관계 샘플 5건 ---')
for r in note_rels[:5]:
    sid = r.get('source_id', '?')
    tid = r.get('target_id', '?')
    rel = r.get('relation', '?')
    print(f'  {sid} --[{rel}]--> {tid}')

# chunks에서 notes
chunks = json.loads(open(r'G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json', 'r', encoding='utf-8').read())
has_notes = [c for c in chunks if c.get('notes')]
print(f'\n=== 청크 내 notes 필드 ===')
print(f'총 청크: {len(chunks)}건, notes 있는 청크: {len(has_notes)}건')
if has_notes:
    s = has_notes[0]
    nd = s['notes']
    print(f'첫 번째 청크 section: {s.get("section_code","?")}')
    print(f'notes 타입: {type(nd).__name__}')
    if isinstance(nd, list) and nd:
        print(f'notes 개수: {len(nd)}')
        item = nd[0]
        if isinstance(item, str):
            print(f'  [0]: {item[:200]}')
        elif isinstance(item, dict):
            print(f'  [0] keys: {list(item.keys())}')
            print(f'  [0]: {json.dumps(item, ensure_ascii=False)[:300]}')
