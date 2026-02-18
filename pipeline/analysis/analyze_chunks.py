import json
from collections import Counter

with open(r'G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

chunks = data['chunks']
total = len(chunks)

tokens = sorted([c['token_count'] for c in chunks])

p50 = tokens[int(total*0.5)]
p75 = tokens[int(total*0.75)]
p90 = tokens[int(total*0.9)]
p95 = tokens[int(total*0.95)]
p99 = tokens[int(total*0.99)]

print('=== TOKEN DISTRIBUTION ===')
print(f'Total chunks: {total}')
print(f'Mean: {sum(tokens)/total:.0f}')
print(f'Median (p50): {p50}')
print(f'p75: {p75}, p90: {p90}, p95: {p95}, p99: {p99}')
print(f'Min: {min(tokens)}, Max: {max(tokens)}')

ranges = [(0,50),(50,200),(200,500),(500,1000),(1000,1500),(1500,2000),(2000,10000)]
print('\n=== TOKEN RANGE DISTRIBUTION ===')
for lo, hi in ranges:
    cnt = sum(1 for t in tokens if lo <= t < hi)
    pct = cnt/total*100
    print(f'{lo:5d}-{hi:5d}: {cnt:5d} ({pct:5.1f}%)')

print('\n=== BY DEPARTMENT ===')
dept_stats = {}
for c in chunks:
    d = c['department']
    if d not in dept_stats:
        dept_stats[d] = {'count':0, 'tokens':[], 'tables':0, 'empty':0, 'has_notes':0}
    dept_stats[d]['count'] += 1
    dept_stats[d]['tokens'].append(c['token_count'])
    dept_stats[d]['tables'] += len(c.get('tables',[]))
    if c['token_count'] <= 10:
        dept_stats[d]['empty'] += 1
    if c.get('notes'):
        dept_stats[d]['has_notes'] += 1

for d, s in dept_stats.items():
    avg = sum(s['tokens'])/len(s['tokens'])
    mx = max(s['tokens'])
    print(f'{d}: chunks={s["count"]}, avg_tok={avg:.0f}, max_tok={mx}, tables={s["tables"]}, tiny(<=10tok)={s["empty"]}, w/notes={s["has_notes"]}')

print('\n=== TINY CHUNKS (<=10 tokens) ===')
tiny = [c for c in chunks if c['token_count'] <= 10]
print(f'Count: {len(tiny)}')
for c in tiny[:20]:
    print(f'  {c["chunk_id"]} | {c["section_id"]} | tok={c["token_count"]} | "{c["text"][:50]}"')

print('\n=== OVER 2000 TOKENS ===')
over = [c for c in chunks if c['token_count'] > 2000]
for c in over:
    nt = len(c.get('notes',[]))
    tb = len(c.get('tables',[]))
    print(f'  {c["chunk_id"]} | sec={c["section_id"]} | tok={c["token_count"]} | tables={tb} | notes={nt} | "{c["title"]}"')

cr_count = sum(1 for c in chunks if c.get('cross_references'))
print(f'\n=== CROSS REFERENCES ===')
print(f'Chunks with cross_refs: {cr_count} ({cr_count/total*100:.1f}%)')

years = Counter(c['revision_year'] for c in chunks if c.get('revision_year'))
print(f'\n=== TOP REVISION YEARS ===')
for y, cnt in years.most_common(10):
    print(f'  {y}: {cnt}')

# Table type distribution
table_types = Counter()
for c in chunks:
    for t in c.get('tables', []):
        table_types[t.get('type', 'unknown')] += 1
print(f'\n=== TABLE TYPE DISTRIBUTION ===')
for tp, cnt in table_types.most_common():
    print(f'  {tp}: {cnt}')

# Chunks with no text AND no tables
empty_both = [c for c in chunks if not c.get('text','').strip() and not c.get('tables')]
print(f'\n=== TRULY EMPTY (no text, no tables) ===')
print(f'Count: {len(empty_both)}')
for c in empty_both[:10]:
    print(f'  {c["chunk_id"]} | {c["section_id"]} | "{c["title"]}"')
