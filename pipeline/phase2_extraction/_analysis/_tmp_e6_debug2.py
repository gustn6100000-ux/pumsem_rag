# -*- coding: utf-8 -*-
"""E6 실패 잔여 75건 심층 분석 — 실제 화면에서 원본 대조"""
import json, sys, re, random
from collections import defaultdict, Counter
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json", encoding="utf-8").read())
raw = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase1_output\chunks.json", encoding="utf-8").read())
chunks_map = {c["chunk_id"]: c for c in raw["chunks"]}

report = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\extraction_report.json", encoding="utf-8").read())
failed = report["details"]["E6"]["samples"]

# 실패한 엔티티를 원본과 직접 비교
lines = []
for s in failed[:15]:
    eid = s["entity_id"]
    ent = next((e for e in norm["entities"] if e["entity_id"] == eid), None)
    if not ent:
        continue
    
    name = ent["name"]
    norm_name = ent.get("normalized_name", name)
    cid = s["chunk_ids"][0] if s["chunk_ids"] else None
    chunk = chunks_map.get(cid, {}) if cid else {}
    
    text = chunk.get("text", "")
    tables = chunk.get("tables", [])
    
    # 재귀 평탄화
    def flatten(obj):
        parts = []
        if isinstance(obj, str):
            parts.append(obj)
        elif isinstance(obj, (int, float)):
            parts.append(str(obj))
        elif isinstance(obj, dict):
            for v in obj.values():
                parts.extend(flatten(v))
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                parts.extend(flatten(item))
        return parts
    
    table_flat = " ".join(flatten(tables))
    full = text + " " + table_flat
    
    lines.append(f"\n{'='*60}")
    lines.append(f"Entity: [{ent['type']}] {name}")
    lines.append(f"NormName: {norm_name}")
    lines.append(f"Chunk: {cid}")
    lines.append(f"Source method: {ent.get('source_method', '?')}")
    
    # 이름의 각 토큰이 원본에 있는지
    tokens = [t for t in re.split(r"[\s()（）\[\]~×·]+", name) if len(t) >= 2]
    tok_found = [(t, t in full) for t in tokens]
    lines.append(f"Tokens: {tok_found}")
    
    # 원본 텍스트에서 가장 유사한 부분 찾기
    if name[:4] in full:
        idx = full.index(name[:4])
        lines.append(f"Partial match at {idx}: ...{full[max(0,idx-10):idx+50]}...")
    else:
        lines.append(f"No partial match for first 4 chars: {name[:4]}")
    
    lines.append(f"Text snippet: {text[:200]}")
    lines.append(f"Table flat snippet: {table_flat[:200]}")

result = "\n".join(lines)
print(result)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\e6_debug2.txt", "w", encoding="utf-8").write(result)
