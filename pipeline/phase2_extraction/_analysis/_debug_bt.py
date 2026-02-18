# -*- coding: utf-8 -*-
"""M2 BELONGS_TO 누락 원인 분석"""
import json, sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

merged = json.loads(Path(r"G:\내 드라이브\Antigravity\python_code\phase2_output\merged_entities.json").read_text(encoding="utf-8"))
exts = merged["extractions"]

# BELONGS_TO 없는 WorkType 분석
missing_chunks = []
for ext in exts:
    wts = [e for e in ext.get("entities", []) if e["type"] == "WorkType"]
    rels = ext.get("relationships", [])
    bt_sources = {r["source"] for r in rels if r["type"] == "BELONGS_TO"}
    
    missing = [wt for wt in wts if wt["name"] not in bt_sources]
    if missing:
        missing_chunks.append({
            "chunk_id": ext["chunk_id"],
            "section_id": ext.get("section_id", ""),
            "total_wt": len(wts),
            "missing_wt": len(missing),
            "missing_names": [m["name"] for m in missing[:3]],
            "bt_sources": list(bt_sources)[:3],
        })

print(f"BELONGS_TO 누락 청크: {len(missing_chunks)} / {len(exts)}")
print(f"누락 WorkType 총 수: {sum(c['missing_wt'] for c in missing_chunks)}")

# 원인 분류
reasons = Counter()
for mc in missing_chunks:
    if not mc["bt_sources"]:
        # 이 청크에 BELONGS_TO 관계가 아예 없음
        reasons["chunk에 BT 없음"] += mc["missing_wt"]
    else:
        # BT 관계는 있지만 일부 WorkType이 누락
        reasons["부분 누락"] += mc["missing_wt"]

print(f"\n원인 분류:")
for reason, cnt in reasons.most_common():
    print(f"  {reason}: {cnt:,}")

# 상세 분석: BT가 아예 없는 청크 10건 샘플
no_bt = [mc for mc in missing_chunks if not mc["bt_sources"]]
print(f"\nBT 없는 청크 수: {len(no_bt)}")
for mc in no_bt[:10]:
    print(f"  {mc['chunk_id']} (sid={mc['section_id']}) wt={mc['total_wt']}: {mc['missing_names']}")

# 부분 누락: 이름 불일치 조사
partial = [mc for mc in missing_chunks if mc["bt_sources"]]
print(f"\n부분 누락 청크 수: {len(partial)}")
for mc in partial[:5]:
    print(f"  {mc['chunk_id']} ({mc['total_wt']}개 중 {mc['missing_wt']}개 누락)")
    print(f"    BT sources: {mc['bt_sources']}")
    print(f"    Missing:    {mc['missing_names']}")
