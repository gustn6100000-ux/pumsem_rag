# -*- coding: utf-8 -*-
"""파이프라인 품질 전략 Gap 분석 스크립트"""
import json, re, sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

data = json.load(open("phase1_output/chunks.json", "r", encoding="utf-8"))
chunks = data["chunks"]

print("=" * 60)
print("1. 기본 통계")
print("=" * 60)
empty = [c for c in chunks if not c.get("text", "").strip()]
print(f"  총 청크: {len(chunks)}")
print(f"  빈 텍스트: {len(empty)} ({len(empty)/len(chunks)*100:.1f}%)")

# 서브청크 분석
base_ids = {}
for c in chunks:
    m = re.match(r"(C-\d+)", c["chunk_id"])
    if m:
        bid = m.group(1)
        base_ids.setdefault(bid, []).append(c)

multi = {k: v for k, v in base_ids.items() if len(v) > 1}
print(f"  다중 청크 섹션: {len(multi)} (총 {sum(len(v) for v in multi.values())} 청크)")

# 형제 복원 가능성 재확인
restorable = 0
isolated = 0
isolated_ids = []
for c in empty:
    m = re.match(r"(C-\d+)", c["chunk_id"])
    if m:
        bid = m.group(1)
        siblings = [s for s in base_ids.get(bid, []) if s.get("text", "").strip() and s["chunk_id"] != c["chunk_id"]]
        if siblings:
            restorable += 1
        else:
            isolated += 1
            isolated_ids.append(c["chunk_id"])

print(f"  형제 복원 가능: {restorable}")
print(f"  고립 (복원 불가): {isolated}")
print(f"  고립 청크 IDs: {isolated_ids[:15]}")

print("\n" + "=" * 60)
print("2. D_기타 대형 매트릭스 분석")
print("=" * 60)
big_matrix = []
for c in chunks:
    for t in c.get("tables", []):
        if t.get("type") == "D_기타" and len(t.get("headers", [])) >= 10:
            big_matrix.append({
                "cid": c["chunk_id"],
                "ncols": len(t.get("headers", [])),
                "nrows": len(t.get("rows", [])),
                "tlen": len(c.get("text", "")),
                "headers": t.get("headers", [])
            })

print(f"  D_기타 10+cols 테이블: {len(big_matrix)}개")
col_dist = Counter()
for bm in big_matrix:
    bucket = f"{(bm['ncols']//5)*5}-{(bm['ncols']//5)*5+4}"
    col_dist[bucket] += 1
for k, v in sorted(col_dist.items()):
    print(f"    {k}cols: {v}개")

# 대형 매트릭스에서 빈 텍스트 비율
big_empty = [b for b in big_matrix if b["tlen"] == 0]
print(f"  그 중 빈 텍스트: {len(big_empty)}/{len(big_matrix)} ({len(big_empty)/len(big_matrix)*100:.1f}%)")

print("\n" + "=" * 60)
print("3. Context Bleeding 위험 분석")
print("=" * 60)
# 형제 청크간 테이블 규격 겹침 분석
bleeding_risk = 0
for bid, group in multi.items():
    specs_by_chunk = {}
    for c in group:
        specs = set()
        for t in c.get("tables", []):
            for h in t.get("headers", []):
                nums = re.findall(r"\d+(?:\.\d+)?(?:mm|cm|m|인치|A|φ)", str(h))
                specs.update(nums)
            for row in t.get("rows", []):
                for k, v in row.items():
                    nums = re.findall(r"\d+(?:\.\d+)?(?:mm|cm|m)", str(v))
                    specs.update(nums)
        if specs:
            specs_by_chunk[c["chunk_id"]] = specs
    
    if len(specs_by_chunk) >= 2:
        all_specs = list(specs_by_chunk.values())
        for i in range(len(all_specs)):
            for j in range(i+1, len(all_specs)):
                overlap = all_specs[i] & all_specs[j]
                if overlap:
                    bleeding_risk += 1

print(f"  규격 겹침이 있는 형제 그룹 수: {bleeding_risk}")

print("\n" + "=" * 60)
print("4. 프롬프트-데이터 정합성 분석")
print("=" * 60)
# 프롬프트에서 build_user_prompt가 생성하는 텍스트 크기 분석
prompt_sizes = []
for c in chunks:
    size = 0
    size += len(c.get("text", ""))
    for t in c.get("tables", []):
        headers = t.get("headers", [])
        rows = t.get("rows", [])
        size += len(" | ".join(headers)) + 10
        for row in rows:
            size += len(" | ".join(str(row.get(h, "")) for h in headers)) + 10
    prompt_sizes.append(size)

print(f"  프롬프트 입력 크기:")
print(f"    평균: {sum(prompt_sizes)/len(prompt_sizes):.0f} chars")
print(f"    최대: {max(prompt_sizes)} chars")
print(f"    8K+ chars: {sum(1 for s in prompt_sizes if s > 8000)}개")
print(f"    16K+ chars: {sum(1 for s in prompt_sizes if s > 16000)}개")
print(f"    32K+ chars: {sum(1 for s in prompt_sizes if s > 32000)}개")

# 관계 전개 규모 추정 (cols × rows)
expansion_sizes = []
for c in chunks:
    for t in c.get("tables", []):
        if t.get("type") == "D_기타":
            ncols = len(t.get("headers", []))
            nrows = len(t.get("rows", []))
            if ncols >= 4:
                expansion_sizes.append(ncols * nrows)

print(f"\n  관계 전개 규모 (D_기타, 4+cols):")
print(f"    총 대상 테이블: {len(expansion_sizes)}")
over100 = sum(1 for s in expansion_sizes if s > 100)
over200 = sum(1 for s in expansion_sizes if s > 200)
print(f"    100+ 관계 예상: {over100}개")
print(f"    200+ 관계 예상: {over200}개")
if expansion_sizes:
    print(f"    최대 전개 수: {max(expansion_sizes)}")

print("\n" + "=" * 60)
print("5. 수량 정밀도 분석 (검증기 영향)")
print("=" * 60)
# rows 내 수량 패턴 분석
qty_patterns = Counter()
sample_rows = 0
for c in chunks[:500]:
    for t in c.get("tables", []):
        for row in t.get("rows", []):
            for k, v in row.items():
                sv = str(v)
                if re.match(r"^-?\d+(\.\d+)?$", sv.strip()):
                    sample_rows += 1
                    # 소수점 자릿수
                    if "." in sv:
                        decimal = len(sv.split(".")[1])
                        qty_patterns[f"소수{decimal}자리"] += 1
                    else:
                        qty_patterns["정수"] += 1
                elif re.search(r"\d+\.\d+", sv):
                    qty_patterns["혼합문자열내숫자"] += 1

print(f"  수치 셀 분석 (500 청크 샘플):")
for k, v in qty_patterns.most_common(10):
    print(f"    {k}: {v}개")

print("\n" + "=" * 60)
print("6. 고립 청크 상세 분석")
print("=" * 60)
for cid in isolated_ids[:10]:
    c = next((ch for ch in chunks if ch["chunk_id"] == cid), None)
    if c:
        tables = c.get("tables", [])
        ttypes = [t.get("type", "") for t in tables]
        print(f"  {cid}: text={len(c.get('text',''))}ch, tables={len(tables)}, types={ttypes}")
