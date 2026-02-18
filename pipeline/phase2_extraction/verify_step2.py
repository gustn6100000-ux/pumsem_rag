# -*- coding: utf-8 -*-
"""Step 2.2 LLM 추출 결과 품질 검증

검증 항목:
  Q1. 할루시네이션 검사: 추출된 엔티티 이름이 원본 텍스트에 실제 존재하는가?
  Q2. 관계 무결성: 관계의 source/target이 같은 청크의 엔티티 목록에 존재하는가?
  Q3. 고아 엔티티: 어떤 관계에도 참여하지 않는 엔티티 비율
  Q4. 커버리지: 엔티티가 0개인 청크 비율
  Q5. 수량 신뢰도: REQUIRES_LABOR 관계의 quantity가 합리적 범위(0.001~100)인가?
  Q6. 랜덤 샘플 5건: 사람이 직접 확인할 수 있도록 원본↔추출 대조표 출력
"""
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ─── 데이터 로드 ──────────────────────────────────────────────
BASE = Path(__file__).resolve().parent.parent
CHUNKS_FILE = BASE / "phase1_output" / "chunks.json"
LLM_FILE = BASE / "phase2_output" / "llm_entities.json"
TABLE_FILE = BASE / "phase2_output" / "table_entities.json"
REPORT_FILE = BASE / "phase2_output" / "quality_report.txt"

chunks_data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
chunk_map = {c["chunk_id"]: c for c in chunks_data["chunks"]}

llm_data = json.loads(LLM_FILE.read_text(encoding="utf-8"))
extractions = llm_data["extractions"]

lines = []
def log(msg=""):
    print(msg)
    lines.append(msg)


# ═══════════════════════════════════════════════════════════════
log("=" * 70)
log("  Step 2.2 LLM 추출 품질 검증 리포트")
log("=" * 70)

# ─── Q1. 할루시네이션 검사 ────────────────────────────────────
log("\n━━━ Q1. 할루시네이션 검사 (엔티티 이름이 원본에 존재하는가?) ━━━")
halluc_total = 0
halluc_miss = 0
halluc_examples = []

for ext in extractions:
    chunk = chunk_map.get(ext["chunk_id"])
    if not chunk:
        continue

    # 원본 텍스트 구성 (text + 테이블 내 모든 값)
    source_text = chunk.get("text", "")
    for t in chunk.get("tables", []):
        for h in t.get("headers", []):
            source_text += " " + h
        for row in t.get("rows", []):
            for v in row.values():
                source_text += " " + str(v)
    for n in chunk.get("notes", []):
        source_text += " " + str(n)

    # 공백 제거 후 비교 (품셈 문서에 '보 통 인 부' 같은 표기 많음)
    source_clean = source_text.replace(" ", "").lower()

    for ent in ext["entities"]:
        halluc_total += 1
        ent_name_clean = ent["name"].replace(" ", "").lower()

        # 이름의 핵심 부분(2글자 이상)이 원본에 있으면 통과
        if len(ent_name_clean) >= 2 and ent_name_clean in source_clean:
            continue
        # 짧은 이름(1글자)은 관대하게 통과
        if len(ent_name_clean) <= 1:
            continue

        # 부분 매칭 (이름의 앞 3글자)
        if len(ent_name_clean) >= 3 and ent_name_clean[:3] in source_clean:
            continue

        halluc_miss += 1
        if len(halluc_examples) < 10:
            halluc_examples.append({
                "chunk_id": ext["chunk_id"],
                "entity": ent["name"],
                "type": ent["type"],
            })

halluc_rate = halluc_miss / halluc_total * 100 if halluc_total else 0
status = "✅ 합격" if halluc_rate < 2 else "⚠️ 주의" if halluc_rate < 5 else "❌ 불합격"
log(f"  전체 엔티티: {halluc_total:,}")
log(f"  원본 매칭 실패: {halluc_miss:,} ({halluc_rate:.2f}%)")
log(f"  판정: {status} (기준: <2%)")
if halluc_examples:
    log(f"  실패 샘플:")
    for ex in halluc_examples[:5]:
        log(f"    [{ex['chunk_id']}] {ex['type']}: \"{ex['entity']}\"")


# ─── Q2. 관계 무결성 (source/target이 엔티티 목록에 존재) ─────
log("\n━━━ Q2. 관계 무결성 (source/target ↔ 엔티티 매칭) ━━━")
rel_total = 0
rel_orphan_source = 0
rel_orphan_target = 0
orphan_examples = []

for ext in extractions:
    ent_names = {e["name"] for e in ext["entities"]}
    # 정규화 이름도 포함
    ent_names_norm = {e.get("normalized_name", "") for e in ext["entities"]}
    all_names = ent_names | ent_names_norm

    for rel in ext["relationships"]:
        rel_total += 1
        src_ok = rel["source"] in all_names or rel["source"].replace(" ", "") in {n.replace(" ", "") for n in all_names}
        tgt_ok = rel["target"] in all_names or rel["target"].replace(" ", "") in {n.replace(" ", "") for n in all_names}

        if not src_ok:
            rel_orphan_source += 1
        if not tgt_ok:
            rel_orphan_target += 1
            if len(orphan_examples) < 5:
                orphan_examples.append({
                    "chunk_id": ext["chunk_id"],
                    "rel": rel["type"],
                    "source": rel["source"],
                    "target": rel["target"],
                    "issue": "source 없음" if not src_ok else "target 없음",
                })

src_rate = rel_orphan_source / rel_total * 100 if rel_total else 0
tgt_rate = rel_orphan_target / rel_total * 100 if rel_total else 0
log(f"  전체 관계: {rel_total:,}")
log(f"  source 매칭 실패: {rel_orphan_source:,} ({src_rate:.1f}%)")
log(f"  target 매칭 실패: {rel_orphan_target:,} ({tgt_rate:.1f}%)")
status2 = "✅" if max(src_rate, tgt_rate) < 5 else "⚠️"
log(f"  판정: {status2} (기준: <5%)")
if orphan_examples:
    log(f"  고아 관계 샘플:")
    for ex in orphan_examples[:3]:
        log(f"    [{ex['chunk_id']}] {ex['rel']}: {ex['source']} → {ex['target']} ({ex['issue']})")


# ─── Q3. 고아 엔티티 (관계에 참여하지 않는 엔티티) ────────────
log("\n━━━ Q3. 고아 엔티티 (어떤 관계에도 참여하지 않음) ━━━")
orphan_ent_total = 0
orphan_ent_count = 0
orphan_by_type = Counter()

for ext in extractions:
    rel_names = set()
    for rel in ext["relationships"]:
        rel_names.add(rel["source"])
        rel_names.add(rel["target"])

    for ent in ext["entities"]:
        orphan_ent_total += 1
        if ent["name"] not in rel_names:
            orphan_ent_count += 1
            orphan_by_type[ent["type"]] += 1

orphan_rate = orphan_ent_count / orphan_ent_total * 100 if orphan_ent_total else 0
log(f"  전체 엔티티: {orphan_ent_total:,}")
log(f"  고아 엔티티: {orphan_ent_count:,} ({orphan_rate:.1f}%)")
log(f"  유형별:")
for t, c in orphan_by_type.most_common():
    log(f"    {t}: {c}")
status3 = "✅" if orphan_rate < 10 else "⚠️" if orphan_rate < 20 else "❌"
log(f"  판정: {status3} (기준: <10%)")


# ─── Q4. 커버리지 (엔티티 0개 청크 비율) ──────────────────────
log("\n━━━ Q4. 커버리지 (빈 추출 청크 비율) ━━━")
empty_count = sum(1 for ext in extractions if len(ext["entities"]) == 0)
fail_count = sum(1 for ext in extractions if ext.get("confidence", 1) == 0.0)
coverage = (len(extractions) - empty_count) / len(extractions) * 100 if extractions else 0
log(f"  전체 청크: {len(extractions):,}")
log(f"  엔티티 0개: {empty_count:,}")
log(f"  실패 (conf=0): {fail_count:,}")
log(f"  커버리지: {coverage:.1f}%")
status4 = "✅" if coverage >= 90 else "⚠️" if coverage >= 80 else "❌"
log(f"  판정: {status4} (기준: ≥90%)")


# ─── Q5. REQUIRES_LABOR 수량 합리성 ──────────────────────────
log("\n━━━ Q5. REQUIRES_LABOR 수량 합리성 (0.001~100 범위) ━━━")
labor_rels = []
labor_no_qty = 0
labor_outlier = 0
for ext in extractions:
    for rel in ext["relationships"]:
        if rel["type"] == "REQUIRES_LABOR":
            if rel.get("quantity") is None:
                labor_no_qty += 1
            else:
                labor_rels.append(rel["quantity"])
                if rel["quantity"] < 0.001 or rel["quantity"] > 100:
                    labor_outlier += 1

log(f"  REQUIRES_LABOR 관계: {len(labor_rels) + labor_no_qty:,}")
log(f"  수량 있음: {len(labor_rels):,}")
log(f"  수량 없음: {labor_no_qty:,}")
log(f"  이상치 (범위 밖): {labor_outlier:,}")
if labor_rels:
    labor_rels_sorted = sorted(labor_rels)
    log(f"  수량 분포: min={labor_rels_sorted[0]}, "
        f"p25={labor_rels_sorted[len(labor_rels)//4]}, "
        f"median={labor_rels_sorted[len(labor_rels)//2]}, "
        f"p75={labor_rels_sorted[3*len(labor_rels)//4]}, "
        f"max={labor_rels_sorted[-1]}")


# ─── Q6. 랜덤 샘플 5건 원본 대조 ─────────────────────────────
log("\n━━━ Q6. 랜덤 샘플 5건 — 원본 ↔ 추출 대조 ━━━")
# 엔티티가 있는 extraction만
valid_exts = [e for e in extractions if len(e["entities"]) >= 2 and len(e["relationships"]) >= 1]
random.seed(42)
samples = random.sample(valid_exts, min(5, len(valid_exts)))

for i, ext in enumerate(samples, 1):
    chunk = chunk_map.get(ext["chunk_id"], {})
    log(f"\n  ── 샘플 {i}: {ext['chunk_id']} ({ext.get('title', '')}) ──")

    # 원본 텍스트 (최대 200자)
    orig_text = chunk.get("text", "")[:200]
    log(f"  원본: {orig_text}...")

    # 추출 결과
    log(f"  LLM summary: {ext.get('summary', '')}")
    log(f"  confidence: {ext.get('confidence', 0)}")
    log(f"  엔티티 ({len(ext['entities'])}개):")
    for e in ext["entities"][:5]:
        log(f"    [{e['type']:10s}] {e['name']}"
            + (f" ({e.get('spec', '')})" if e.get('spec') else "")
            + (f" / {e.get('quantity', '')} {e.get('unit', '')}" if e.get('quantity') else ""))
    if len(ext["entities"]) > 5:
        log(f"    ... 외 {len(ext['entities'])-5}개")

    log(f"  관계 ({len(ext['relationships'])}개):")
    for r in ext["relationships"][:4]:
        qty_str = f" ({r.get('quantity', '')} {r.get('unit', '')})" if r.get("quantity") else ""
        log(f"    {r['source']} ──{r['type']}──→ {r['target']}{qty_str}")
    if len(ext["relationships"]) > 4:
        log(f"    ... 외 {len(ext['relationships'])-4}개")


# ─── 종합 ────────────────────────────────────────────────────
log("\n" + "=" * 70)
log("  종합 점수표")
log("=" * 70)
log(f"  Q1 할루시네이션  : {halluc_rate:.2f}% (목표 <2%)  {status}")
log(f"  Q2 관계 무결성   : src {src_rate:.1f}% / tgt {tgt_rate:.1f}% (목표 <5%)  {status2}")
log(f"  Q3 고아 엔티티   : {orphan_rate:.1f}% (목표 <10%)  {status3}")
log(f"  Q4 커버리지      : {coverage:.1f}% (목표 ≥90%)  {status4}")
log(f"  Q5 노무 수량     : 이상치 {labor_outlier}건")
log(f"  Q6 샘플 대조     : 위 5건 직접 확인 필요")
log("=" * 70)

# 파일 저장
REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
print(f"\n  리포트 저장: {REPORT_FILE}")
