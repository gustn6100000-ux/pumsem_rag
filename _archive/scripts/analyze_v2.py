import json
from collections import Counter

data = json.load(open(r"G:\My Drive\Antigravity\pjt\pumsem\pipeline\scripts\output\audit_v2_report.json", encoding="utf-8"))

fails = [s for s in data["sections"] if s["status"] == "FAIL"]
warns = [s for s in data["sections"] if s["status"] == "WARN"]

# FAIL 유형별 분류
fail_types = Counter()
total_missing_rows = 0
section_missing = []
row_missing = []

for f in fails:
    for issue in f["issues"]:
        fail_types[issue["type"]] += 1
    if any(i["type"] == "SECTION_MISSING" for i in f["issues"]):
        section_missing.append(f)
    else:
        row_missing.append(f)
    total_missing_rows += f["md_rows"] - f["db_rows"]

print("=== FAIL 131건 분류 ===")
print("유형별:", dict(fail_types))
print("총 누락 행:", total_missing_rows)
print("DB에 아예 없음:", len(section_missing), "건")
print("DB에 있지만 행 부족:", len(row_missing), "건")

print("\n=== A. DB에 아예 없는 section (SECTION_MISSING) ===")
for s in sorted(section_missing, key=lambda x: -x["md_rows"]):
    sid = s["base_section_id"]
    t = s["title"]
    print(f"  [{sid}] {t} - {s['md_tables']}표/{s['md_rows']}행")

print("\n=== B. DB에 있지만 행 부족 (ROW_MISSING, coverage < 50%) ===")
for s in sorted(row_missing, key=lambda x: x["coverage_pct"]):
    sid = s["base_section_id"]
    t = s["title"]
    chunks_str = ", ".join(cd["chunk_id"] for cd in s.get("chunk_details", []))
    print(f"  [{sid}] {t} - MD:{s['md_rows']} DB:{s['db_rows']} ({s['coverage_pct']}%) chunks:[{chunks_str}]")

# WARN 분석
print("\n=== WARN 67건 coverage 범위 ===")
warn_coverages = [w["coverage_pct"] for w in warns]
print(f"  최소: {min(warn_coverages):.1f}%, 최대: {max(warn_coverages):.1f}%, 평균: {sum(warn_coverages)/len(warn_coverages):.1f}%")
print(f"  50-60%: {sum(1 for c in warn_coverages if 50<=c<60)}건")
print(f"  60-70%: {sum(1 for c in warn_coverages if 60<=c<70)}건")
print(f"  70-80%: {sum(1 for c in warn_coverages if 70<=c<80)}건")

warn_missing = sum(w["md_rows"] - w["db_rows"] for w in warns)
print(f"  WARN 누락 행 합계: {warn_missing}")
print(f"  FAIL 누락 행 합계: {total_missing_rows}")
print(f"  총 누락 행: {warn_missing + total_missing_rows} / {data['summary']['total_md_rows']}")

# COL_MISMATCH가 FAIL과 동시에 있는 것 - 원본 MD에 더 많은 컬럼의 표가 있는데 DB에 없는 경우
print("\n=== FAIL + COL_MISMATCH (표 구조 불일치) ===")
col_mismatch_fails = [f for f in fails if any(i["type"] == "COL_MISMATCH" for i in f["issues"])]
print(f"  {len(col_mismatch_fails)}건")
for s in col_mismatch_fails[:10]:
    sid = s["base_section_id"]
    t = s["title"]
    col_detail = [i["detail"] for i in s["issues"] if i["type"] == "COL_MISMATCH"][0]
    print(f"  [{sid}] {t} - {col_detail}")
