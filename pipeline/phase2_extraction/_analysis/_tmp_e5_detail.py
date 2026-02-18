# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

d = json.loads(open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\extraction_report.json", encoding="utf-8").read())
e5 = d["details"]["E5"]

print("=== E5 상세 ===")
print(f"평가 샘플: {e5['samples_evaluated']}")
print(f"오류: {e5['errors']}")
print(f"가중평균: {e5['weighted_avg']}")
print(f"완전성: {e5['avg_completeness']}")
print(f"정확성: {e5['avg_accuracy']}")
print(f"비환각: {e5['avg_no_hallucination']}")
print(f"관계품질: {e5['avg_relationship_quality']}")
print()

low = e5.get("low_samples", [])
print(f"하위 5개 청크:")
for item in low:
    if isinstance(item, list) and len(item) == 2:
        print(f"  {item[0]}: {item[1]:.3f}")
    else:
        print(f"  {item}")
