# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

d = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\extraction_report.json", encoding="utf-8").read())
samples = d["details"]["E6"]["samples"]

print(f"=== E6 할루시네이션 FAIL 샘플 ({len(samples)}건) ===\n")
for s in samples:
    etype = s["type"]
    name = s["name"]
    cids = s["chunk_ids"]
    print(f"  {etype:12s} | {name[:50]:50s} | {cids}")

# 타입별 분포
from collections import Counter
tc = Counter(s["type"] for s in samples)
print(f"\n타입별: {dict(tc)}")

# 전체 91건 중 타입별
full = d["details"]["E6"]
print(f"\n총 샘플: {full['total_samples']}, 할루시네이션: {full['hallucinated']}")
