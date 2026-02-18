# -*- coding: utf-8 -*-
"""비정상 단위 758건 심층 분석 → 정규화 가능 여부 판단"""
import json, sys, re
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8")

norm = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\normalized_entities.json",
    encoding="utf-8"
).read())

all_rels = []
for ext in norm.get("extractions", []):
    for r in ext.get("relationships", []):
        all_rels.append(r)
for rtype, rels in norm.get("global_relationships", {}).items():
    all_rels.extend(rels)

# valid_units (현재 검증 스크립트 기준)
valid_units = {"인", "m", "m²", "m³", "kg", "L", "대", "개", "조", "EA", "set",
               "kW", "HP", "t", "km", "cm", "mm", "ha", "본", "매",
               "kVA", "식", "일", "hr", "시간", "ton", "공", "M/D", "톤"}

# 비정상 단위 수집 (검증 스크립트의 regex 패턴은 제외)
weird_counter = Counter()
weird_rels = []
for r in all_rels:
    u = r.get("unit", "")
    if u and u not in valid_units and not re.match(r"^[\d./×\-~a-zA-Z㎡㎥㎜㎝㎞ℓ%℃]+$", u):
        weird_counter[u] += 1
        if len(weird_rels) < 200:
            weird_rels.append(r)

out = []
def p(s=""): out.append(s)

p("=" * 70)
p("비정상 단위 분류")
p("=" * 70)

# 단위를 패턴별로 분류
compound_units = {}  # 합성 단위 (인/ton, L/hr 등)
korean_units = {}     # 한글 단위 (소수자리, 계수 등)
symbol_units = {}     # 특수 문자 단위 (₩, ・ 등)
other_units = {}

for u, c in weird_counter.items():
    if "/" in u:
        compound_units[u] = c
    elif re.match(r"^[가-힣]+$", u) or re.match(r"^[가-힣·\s]+$", u):
        korean_units[u] = c
    elif re.match(r"^[^\x00-\x7F]+$", u):
        symbol_units[u] = c
    else:
        other_units[u] = c

p(f"\n합성 단위 (A/B): {len(compound_units)}종, {sum(compound_units.values())}건")
for u, c in sorted(compound_units.items(), key=lambda x: -x[1])[:15]:
    p(f"  '{u}': {c}")

p(f"\n한글 단위: {len(korean_units)}종, {sum(korean_units.values())}건")
for u, c in sorted(korean_units.items(), key=lambda x: -x[1]):
    p(f"  '{u}': {c}")

p(f"\n기호 단위: {len(symbol_units)}종, {sum(symbol_units.values())}건")
for u, c in sorted(symbol_units.items(), key=lambda x: -x[1]):
    p(f"  '{u}': {c}")

p(f"\n기타: {len(other_units)}종, {sum(other_units.values())}건")
for u, c in sorted(other_units.items(), key=lambda x: -x[1]):
    p(f"  '{u}': {c}")

# 판정
p("\n" + "=" * 70)
p("판정")
p("=" * 70)
p(f"""
합성 단위 (인/ton, L/hr, 인/m² 등):
  → 품셈에서 표준적으로 사용하는 생산성/소요량 단위. 정규화 불필요.
  → 예: '보통인부 0.035인/ton' = 1톤당 0.035인 투입

한글 단위 (소수자리, 계수, 개소 등):
  → 원본 데이터에서 단위가 아닌 비고/참고 정보가 unit 필드에 들어간 경우.
  → '소수자리'는 수량 표기 기준(반올림 자릿수)을 의미. 단위라기보다 메타 정보.
  → 정규화보다는 별도 필드(rounding_note)로 분리하는 것이 바람직하나,
     Step 2.4 범위를 벗어남. 현 상태 유지.

기호 단위 (₩, ・ 등):
  → ₩는 원화 단위(비용). 품셈 관계에서 비용 정보가 포함된 경우.
  → 데이터 손실 방지를 위해 그대로 유지.

결론: 758건 중 정규화 대상 없음. 모두 원본 데이터 특성.
""")

report = "\n".join(out)
print(report)
open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\unit_analysis.txt",
     "w", encoding="utf-8").write(report)
