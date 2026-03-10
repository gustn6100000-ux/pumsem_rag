import json, csv

data = json.load(open(r"G:\My Drive\Antigravity\pjt\pumsem\pipeline\scripts\output\audit_v2_report.json", encoding="utf-8"))

fails = sorted([s for s in data["sections"] if s["status"] == "FAIL"], key=lambda x: x["coverage_pct"])
warns = sorted([s for s in data["sections"] if s["status"] == "WARN"], key=lambda x: x["coverage_pct"])

# FAIL 전체 목록 출력
print("### FAIL 전체 목록 ({} 건)".format(len(fails)))
print()
print("| # | Section | 제목 | MD표 | MD행 | DB청크 | DB표 | DB행 | Coverage | 문제유형 |")
print("|---|---|---|---|---|---|---|---|---|---|")
for i, f in enumerate(fails, 1):
    sid = f["base_section_id"]
    t = f["title"][:20]
    issues = ",".join(set(issue["type"].replace("_","") for issue in f["issues"]))
    cov = "{:.0f}%".format(f["coverage_pct"])
    print("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
        i, sid, t, f["md_tables"], f["md_rows"], f.get("db_chunks",0), f["db_tables"], f["db_rows"], cov, issues
    ))

print()
print("### WARN 전체 목록 ({} 건)".format(len(warns)))
print()
print("| # | Section | 제목 | MD행 | DB행 | Coverage | 문제유형 |")
print("|---|---|---|---|---|---|---|")
for i, w in enumerate(warns, 1):
    sid = w["base_section_id"]
    t = w["title"][:20]
    issues = ",".join(set(issue["type"].replace("_","") for issue in w["issues"]))
    cov = "{:.0f}%".format(w["coverage_pct"])
    print("| {} | {} | {} | {} | {} | {} | {} |".format(
        i, sid, t, w["md_rows"], w["db_rows"], cov, issues
    ))
