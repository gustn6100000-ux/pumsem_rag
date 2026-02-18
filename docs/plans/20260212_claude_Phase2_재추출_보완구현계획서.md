# Phase 2 재추출 보완 상세 구현계획서

> **작성일**: 2026-02-12
> **기반 문서**: `phase2_execution_plan.md` 상세 검토 결과
> **목적**: 기존 실행 계획의 사전 조건 미충족, step3 병합 버그, 백업/롤백 절차 누락 등을 보완
> **실행 환경**: 로컬 PC (Python 3.12+), Supabase PostgreSQL

---

## 1. 기존 실행 계획의 문제점 요약

| # | 문제 | 심각도 | 보완 위치 |
|---|---|---|---|
| 1 | Case D 매트릭스 추출 **미구현** 상태에서 실행 가정 | 치명 | 2장 |
| 2 | chunks.json 테이블 타입 패치 **미언급** | 높음 | 3장 |
| 3 | step3 관계 병합에서 **LLM 수치가 테이블보다 우선** 적용 | 높음 | 4장 |
| 4 | step1 **단위 테스트** 없이 전체 실행 | 중간 | 5장 |
| 5 | 기존 출력 파일 **백업** 절차 없음 | 중간 | 6장 |
| 6 | **실패 시 롤백** 절차 없음 | 중간 | 7장 |
| 7 | step2 **비용 정량 추정** 없음 | 낮음 | 8장 |

---

## 2. 선행 조건 1: Case D 매트릭스 추출 구현

> **수정 파일**: `phase2_extraction/step1_table_extractor.py`

### 2.1 현재 상태

```
step1_table_extractor.py에서 "matrix", "Case D", "매트릭스" 검색 → 매칭 0건
```

step1은 Case A(노무/장비 열 패턴), Case B(이름-규격-단위-수량), Case C(전치 테이블) 3가지만 지원한다.
매트릭스 테이블(구경 x SCH)은 `D_기타`로 분류되어 step1이 완전 스킵하고 있다.

### 2.2 구현할 함수 목록

#### (1) `is_matrix_table(table) -> bool`

매트릭스 테이블 판별 함수. `extract_from_a_table()` 진입 전에 호출.

```python
def is_matrix_table(table: dict) -> bool:
    """매트릭스 테이블 판별 (헤더 50%+ 숫자 + 데이터행에 직종 키워드)"""
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if len(headers) < 4 or not rows:
        return False

    # 기준 1: 첫 열 제외 헤더 중 50% 이상이 숫자
    numeric_count = sum(
        1 for h in headers[1:]
        if re.match(r'^\d+$', str(h).strip())
    )
    if numeric_count / max(len(headers) - 1, 1) < 0.5:
        return False

    # 기준 2: 상단 3행 내에 직종 키워드 존재
    _JOB_KW = ["용접공", "플랜트", "인부", "배관공", "기능공", "기사", "철공",
               "기능사", "목공", "미장공", "도장공", "조적공"]
    for row in rows[:3]:
        row_text = " ".join(str(v) for v in row.values())
        if any(kw in row_text for kw in _JOB_KW):
            return True

    # 기준 3: 직종 키워드 없어도 첫 열이 구경 패턴이면 매트릭스
    data_row_count = sum(
        1 for row in rows
        if re.match(r'^[φΦ]?\s*\d+', str(row.get(headers[0], "")).replace(" ", ""))
    )
    if data_row_count >= 3:
        return True

    return False
```

#### (2) `detect_matrix_meta(table) -> tuple[dict, dict, list]`

매트릭스 상단 메타행(직종/단위)과 데이터행을 분리.

```python
def detect_matrix_meta(
    table: dict,
) -> tuple[dict[str, str], dict[str, str], list[dict]]:
    """
    Returns:
        job_type_map: {"20": "용접공", "40": "플랜트 용접공", ...}
        unit_map: {"20": "인", "40": "인", ...}
        data_rows: [{"SCH No.": "φ 15", "40": 0.066, ...}, ...]
    """
    headers = table["headers"]
    rows = table["rows"]

    job_type_map: dict[str, str] = {}
    unit_map: dict[str, str] = {}
    data_rows: list[dict] = []

    _JOB_KW = ["공", "기사", "인부", "기능", "기술자"]
    _UNIT_VALS = {"(인)", "인", "(대)", "대", "(㎥)", "(m²)"}
    _META_KW = ["직종", "단위", "mm", "구분", "구경"]

    for row in rows:
        first_val = str(row.get(headers[0], "")).replace(" ", "")

        # 메타행: 첫 열이 "직종구경", "mm" 등
        if any(kw in first_val for kw in _META_KW):
            for h in headers[1:]:
                val = str(row.get(h, "")).strip()
                if any(kw in val for kw in _JOB_KW):
                    job_type_map[str(h)] = val
                elif val in _UNIT_VALS:
                    unit_map[str(h)] = val.strip("()")
        # 데이터행: 첫 열이 구경 값
        elif re.match(r'^[φΦ]?\s*\d+', first_val):
            data_rows.append(row)

    return job_type_map, unit_map, data_rows
```

#### (3) `extract_from_matrix_table(...) -> tuple[list, list, list]`

매트릭스에서 엔티티/관계를 생성하는 핵심 함수.

```python
def extract_from_matrix_table(
    table: dict,
    chunk_id: str,
    section_id: str,
    section_title: str,
    unit_basis: str = "",
) -> tuple[list[Entity], list[Relationship], list[str]]:
    """Case D: 매트릭스 테이블 (구경 x SCH) 추출"""
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    warnings: list[str] = []

    headers = table.get("headers", [])
    job_type_map, unit_map, data_rows = detect_matrix_meta(table)

    if not job_type_map:
        warnings.append(f"매트릭스 테이블이지만 직종 메타행 없음: {headers}")
        return entities, relationships, warnings

    if not data_rows:
        warnings.append(f"매트릭스 테이블이지만 데이터행 없음: {headers}")
        return entities, relationships, warnings

    for row in data_rows:
        # 구경 추출
        diameter_raw = str(row.get(headers[0], "")).strip()
        diameter = re.sub(r'\s+', '', diameter_raw)  # "φ 15" → "φ15"

        for sch_header in headers[1:]:
            sch_str = str(sch_header).strip()

            # 수치 파싱
            qty_val, raw_val = parse_cell_value(row.get(sch_header))
            if qty_val is None or qty_val == 0:
                continue

            # 직종 결정
            job_name = job_type_map.get(sch_str, "")
            if not job_name:
                warnings.append(
                    f"SCH {sch_str}의 직종 미매핑 (구경={diameter})"
                )
                continue
            job_name = normalize_entity_name(job_name)

            # 단위 결정
            unit_val = unit_map.get(sch_str, "인")

            # WorkType 엔티티
            spec_str = f"{diameter}, SCH {sch_str}"
            wt_name = f"{section_title} ({spec_str})"

            wt_entity = Entity(
                type=EntityType.WORK_TYPE,
                name=wt_name,
                spec=spec_str,
                unit=unit_basis if unit_basis else None,
                source_chunk_id=chunk_id,
                source_section_id=section_id,
                source_method="table_rule",
            )
            entities.append(wt_entity)

            # Labor 엔티티
            labor_entity = Entity(
                type=EntityType.LABOR,
                name=job_name,
                unit=unit_val,
                quantity=qty_val,
                source_chunk_id=chunk_id,
                source_section_id=section_id,
                source_method="table_rule",
            )
            entities.append(labor_entity)

            # REQUIRES_LABOR 관계
            rel = Relationship(
                source=wt_name,
                source_type=EntityType.WORK_TYPE,
                target=job_name,
                target_type=EntityType.LABOR,
                type=RelationType.REQUIRES_LABOR,
                quantity=qty_val,
                unit=unit_val,
                per_unit=unit_basis if unit_basis else None,
                source_chunk_id=chunk_id,
            )
            relationships.append(rel)

    return entities, relationships, warnings
```

#### (4) `extract_from_chunk()` 분기 수정

```python
# 기존 코드 (step1_table_extractor.py:553~566)
for table in tables:
    table_type = table.get("type", "")

    if table_type == "A_품셈":
        # [추가] 매트릭스 우선 검사
        if is_matrix_table(table):
            ents, rels, warns = extract_from_matrix_table(
                table, chunk_id, section_id, title,
                unit_basis=chunk.get("unit_basis", ""),
            )
        else:
            ents, rels, warns = extract_from_a_table(
                table, chunk_id, section_id, title
            )
    elif table_type == "B_규모기준":
        ents, rels, warns = extract_from_b_table(
            table, chunk_id, section_id, title
        )
    elif table_type == "D_기타":
        # [추가] D_기타에서도 매트릭스 재검사 (chunks.json 미패치 대비)
        if is_matrix_table(table):
            ents, rels, warns = extract_from_matrix_table(
                table, chunk_id, section_id, title,
                unit_basis=chunk.get("unit_basis", ""),
            )
        else:
            ents, rels, warns = [], [], []
    else:
        ents, rels, warns = [], [], []
```

---

## 3. 선행 조건 2: chunks.json 테이블 타입 패치

> **신규 파일**: `phase2_extraction/patch_table_types.py`

### 3.1 필요 이유

step2의 `select_llm_target_chunks()` 조건 2:

```python
# step2_llm_extractor.py:374-378
table_types = {t.get("type", "") for t in tables}
if table_types <= {"D_기타", "C_구분설명"}:
    targets.append(chunk)  # ← LLM 대상으로 선정
```

**chunks.json에서 테이블 타입이 `D_기타`로 남아 있으면**, step1에서 매트릭스 추출에 성공하더라도 step2가 해당 청크를 LLM 대상으로 중복 처리한다. 이때 step3에서 LLM 관계가 우선 적용되는 버그(4장)와 결합하여 **정확한 규칙 추출 수치가 LLM 부정확 수치로 덮어쓰여질 수 있다.**

### 3.2 패치 스크립트

```python
# patch_table_types.py
"""chunks.json의 D_기타 테이블 중 매트릭스 패턴을 A_품셈으로 재분류"""
import json
import re
from pathlib import Path

CHUNKS_FILE = Path(__file__).resolve().parent.parent / "phase1_output" / "chunks.json"
BACKUP_FILE = CHUNKS_FILE.with_suffix(".json.bak_20260212")


def is_matrix_table(table: dict) -> bool:
    """is_matrix_table과 동일 로직 (step1에서 import 불가 시 중복 정의)"""
    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if len(headers) < 4 or not rows:
        return False

    numeric_count = sum(
        1 for h in headers[1:]
        if re.match(r'^\d+$', str(h).strip())
    )
    if numeric_count / max(len(headers) - 1, 1) < 0.5:
        return False

    _JOB_KW = ["용접공", "플랜트", "인부", "배관공", "기능공", "기사", "철공",
               "기능사", "목공", "미장공", "도장공", "조적공"]
    for row in rows[:3]:
        row_text = " ".join(str(v) for v in row.values())
        if any(kw in row_text for kw in _JOB_KW):
            return True

    data_row_count = sum(
        1 for row in rows
        if re.match(r'^[φΦ]?\s*\d+',
                     str(row.get(headers[0], "")).replace(" ", ""))
    )
    return data_row_count >= 3


def patch():
    data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))

    # 백업
    BACKUP_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"백업 완료: {BACKUP_FILE}")

    patched = 0
    patched_tables = []

    for chunk in data["chunks"]:
        for table in chunk.get("tables", []):
            if table.get("type") == "D_기타" and is_matrix_table(table):
                table["type"] = "A_품셈"
                patched += 1
                patched_tables.append({
                    "table_id": table.get("table_id"),
                    "section_id": chunk.get("section_id"),
                    "title": chunk.get("title"),
                    "headers": table.get("headers"),
                })

    # 저장
    CHUNKS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n패치 완료: {patched}개 테이블 D_기타 → A_품셈")
    for t in patched_tables:
        print(f"  {t['table_id']} | {t['section_id']} {t['title']} | {t['headers'][:3]}...")

    return patched_tables


if __name__ == "__main__":
    patch()
```

### 3.3 실행 순서

```
1. python patch_table_types.py     ← chunks.json 패치 (자동 백업 포함)
2. python step1_table_extractor.py ← Case D 포함 step1 재실행
3. python step2_llm_extractor.py   ← 패치된 타입 기준으로 대상 선별
```

---

## 4. step3 관계 병합 버그 수정

> **수정 파일**: `phase2_extraction/step3_relation_builder.py`

### 4.1 현재 버그

`merge_chunk_extractions()` 함수 (`step3_relation_builder.py:52~127`):

```python
# 현재 코드 (104-117행)
# 관계 병합: LLM 먼저, 테이블은 LLM에 없는 것만 추가
for rel in llm_ext.get("relationships", []):  # ← LLM 우선
    key = _rel_key(rel)
    merged_rels.append(rel)
    llm_rel_keys.add(key)

for trel in table_ext.get("relationships", []):
    key = _rel_key(trel)
    if key not in llm_rel_keys:  # ← 같은 키면 테이블 무시
        merged_rels.append(trel)
```

**문제**: 주석에는 "quantity/unit: 테이블 우선"이라고 했지만, 실제로는 LLM 관계가 우선 적용되고 테이블 관계는 버려짐.

### 4.2 수정안

```python
# 수정 코드: 테이블 관계의 수치를 우선 적용

# ── 관계 병합 ──
merged_rel_map: dict[str, dict] = {}  # key → rel
merged_rels: list[dict] = []

# LLM 관계 먼저 등록
for rel in llm_ext.get("relationships", []):
    key = _rel_key(rel)
    if key not in merged_rel_map:
        merged_rel_map[key] = rel
        merged_rels.append(rel)

# 테이블 관계: 새로운 키면 추가, 기존 키면 수치 덮어쓰기
for trel in table_ext.get("relationships", []):
    key = _rel_key(trel)
    if key in merged_rel_map:
        # [수정] 테이블의 quantity/unit으로 보강 (테이블 우선)
        existing = merged_rel_map[key]
        if trel.get("quantity") is not None:
            existing["quantity"] = trel["quantity"]
        if trel.get("unit"):
            existing["unit"] = trel["unit"]
        if trel.get("per_unit"):
            existing["per_unit"] = trel["per_unit"]
        existing["source_method"] = "merged"
    else:
        merged_rel_map[key] = trel
        merged_rels.append(trel)

merged["relationships"] = merged_rels
```

### 4.3 엔티티 병합도 동일 패턴 적용

```python
# 기존 코드 (82-100행) 수정
for tent in table_ext.get("entities", []):
    key = _entity_key(tent)
    if key in merged_ent_map:
        existing = merged_ent_map[key]
        # [수정] 테이블 수치가 있으면 항상 덮어쓰기 (기존: None일 때만)
        if tent.get("quantity") is not None:
            existing["quantity"] = tent["quantity"]
        if tent.get("unit") and tent.get("unit") != existing.get("unit"):
            existing["unit"] = tent["unit"]
        existing["confidence"] = max(
            existing.get("confidence", 0),
            tent.get("confidence", 0),
        )
        existing["source_method"] = "merged"
    else:
        tent_copy = {**tent, "source_method": "table_rule"}
        merged_entities.append(tent_copy)
        merged_ent_map[key] = tent_copy
```

---

## 5. step1 단위 테스트

> **신규 파일**: `phase2_extraction/test_case_d.py`

### 5.1 목적

전체 재실행 전에 13-2-3 강관용접 1개 section으로 Case D 동작을 검증한다.

### 5.2 테스트 스크립트

```python
# test_case_d.py
"""Case D 매트릭스 추출 단위 테스트 (13-2-3 강관용접)"""
import json
from pathlib import Path

from step1_table_extractor import (
    is_matrix_table,
    detect_matrix_meta,
    extract_from_matrix_table,
    extract_from_chunk,
)

CHUNKS_FILE = Path(__file__).resolve().parent.parent / "phase1_output" / "chunks.json"


def test_13_2_3():
    data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))

    # C-0955-B 청크 찾기
    target = None
    for chunk in data["chunks"]:
        if chunk["chunk_id"] == "C-0955-B":
            target = chunk
            break

    assert target is not None, "C-0955-B 청크 미발견"

    table = target["tables"][0]
    print(f"테이블: {table['table_id']}, 타입: {table['type']}")
    print(f"헤더: {table['headers']}")
    print(f"행 수: {len(table['rows'])}")

    # 테스트 1: 매트릭스 판별
    assert is_matrix_table(table), "FAIL: 매트릭스 테이블로 판별되어야 함"
    print("\n[PASS] 테스트 1: 매트릭스 판별 성공")

    # 테스트 2: 메타행 분리
    job_map, unit_map, data_rows = detect_matrix_meta(table)
    print(f"\n직종 매핑: {job_map}")
    print(f"단위 매핑: {unit_map}")
    print(f"데이터행 수: {len(data_rows)}")

    assert job_map.get("20") == "용접공", f"FAIL: SCH 20 = {job_map.get('20')}"
    assert job_map.get("30") == "용접공", f"FAIL: SCH 30 = {job_map.get('30')}"
    assert job_map.get("40") == "플랜트 용접공", f"FAIL: SCH 40 = {job_map.get('40')}"
    assert job_map.get("160") == "플랜트 용접공", f"FAIL: SCH 160 = {job_map.get('160')}"
    assert len(data_rows) == 15, f"FAIL: 데이터행 {len(data_rows)}개 (예상 15)"
    print("[PASS] 테스트 2: 메타행 분리 성공")

    # 테스트 3: 엔티티/관계 생성
    ents, rels, warns = extract_from_matrix_table(
        table, "C-0955-B", "13-2-3", "강관용접",
        unit_basis="개소당",
    )
    print(f"\n엔티티: {len(ents)}개")
    print(f"관계: {len(rels)}개")
    print(f"경고: {warns}")

    wt_count = sum(1 for e in ents if e.type.value == "WorkType")
    labor_count = sum(1 for e in ents if e.type.value == "Labor")
    print(f"  WorkType: {wt_count}개, Labor: {labor_count}개")

    assert wt_count >= 80, f"FAIL: WorkType {wt_count}개 (최소 80 예상)"
    assert labor_count >= 80, f"FAIL: Labor {labor_count}개 (최소 80 예상)"
    assert len(rels) >= 80, f"FAIL: 관계 {len(rels)}개 (최소 80 예상)"
    print("[PASS] 테스트 3: 엔티티/관계 생성 성공")

    # 테스트 4: 핵심 값 정확성 검증
    checks = [
        ("강관용접 (φ200, SCH 20)", "용접공", 0.287),
        ("강관용접 (φ200, SCH 40)", "플랜트용접공", 0.287),
        ("강관용접 (φ200, SCH 80)", "플랜트용접공", 0.362),
        ("강관용접 (φ350, SCH 20)", "용접공", 0.442),
        ("강관용접 (φ15, SCH 160)", "플랜트용접공", 0.087),
    ]

    for wt_name, expected_labor, expected_qty in checks:
        found = False
        for rel in rels:
            # 이름 비교 시 공백 제거
            src_norm = rel.source.replace(" ", "")
            wt_norm = wt_name.replace(" ", "")
            tgt_norm = rel.target.replace(" ", "")
            lab_norm = expected_labor.replace(" ", "")

            if src_norm == wt_norm and tgt_norm == lab_norm:
                assert rel.quantity == expected_qty, (
                    f"FAIL: {wt_name} → {expected_labor} "
                    f"수량 {rel.quantity} != {expected_qty}"
                )
                found = True
                break
        assert found, f"FAIL: {wt_name} → {expected_labor} 관계 미발견"

    print("[PASS] 테스트 4: 핵심 값 정확성 검증 성공")

    # 테스트 5: 빈 셀 미생성 확인
    empty_check = "강관용접 (φ15, SCH 20)"
    for rel in rels:
        assert rel.source.replace(" ", "") != empty_check.replace(" ", ""), (
            f"FAIL: 빈 셀 {empty_check}에 대해 관계가 생성됨 (qty={rel.quantity})"
        )
    print("[PASS] 테스트 5: 빈 셀 미생성 확인")

    print("\n===== 전체 테스트 통과 =====")


if __name__ == "__main__":
    test_13_2_3()
```

### 5.3 실행

```bash
cd G:\내 드라이브\Cluade code\python_code\phase2_extraction
python test_case_d.py
```

**이 테스트가 통과하기 전에는 전체 step1 재실행을 하지 않는다.**

---

## 6. 백업 절차

### 6.1 Phase 2 출력 파일 백업

step1~5 재실행 전에 기존 결과물을 보존한다.

```python
# 수동 실행 또는 스크립트화
import shutil
from pathlib import Path
from datetime import datetime

PHASE2_OUTPUT = Path(r"G:\내 드라이브\Cluade code\python_code\phase2_output")
BACKUP_DIR = PHASE2_OUTPUT / f"backup_{datetime.now().strftime('%Y%m%d_%H%M')}"

BACKUP_DIR.mkdir(exist_ok=True)

targets = [
    "table_entities.json",
    "llm_entities.json",
    "merged_entities.json",
    "normalized_entities.json",
]
for f in targets:
    src = PHASE2_OUTPUT / f
    if src.exists():
        shutil.copy2(src, BACKUP_DIR / f)
        print(f"  백업: {f}")
```

### 6.2 chunks.json 백업

`patch_table_types.py`에 자동 백업이 포함되어 있음 (`chunks.json.bak_20260212`).

### 6.3 Supabase DB 백업

Phase 4(DB 교체) 직전에 실행:

```sql
CREATE TABLE graph_entities_backup_20260212 AS SELECT * FROM graph_entities;
CREATE TABLE graph_relationships_backup_20260212 AS SELECT * FROM graph_relationships;
CREATE TABLE graph_chunks_backup_20260212 AS SELECT * FROM graph_chunks;
```

---

## 7. 롤백 절차

### 7.1 step1 실패 시

```bash
# table_entities.json 복원
copy phase2_output\backup_YYYYMMDD_HHMM\table_entities.json phase2_output\table_entities.json
```

### 7.2 chunks.json 패치 롤백

```bash
# 자동 백업에서 복원
copy phase1_output\chunks.json.bak_20260212 phase1_output\chunks.json
```

### 7.3 step3~5 실패 시

```bash
# 개별 파일 복원
copy phase2_output\backup_YYYYMMDD_HHMM\merged_entities.json phase2_output\
copy phase2_output\backup_YYYYMMDD_HHMM\normalized_entities.json phase2_output\
```

### 7.4 DB 적재 실패 시

```sql
-- Supabase SQL Editor
TRUNCATE graph_entities, graph_relationships, graph_chunks;
INSERT INTO graph_entities SELECT * FROM graph_entities_backup_20260212;
INSERT INTO graph_relationships SELECT * FROM graph_relationships_backup_20260212;
INSERT INTO graph_chunks SELECT * FROM graph_chunks_backup_20260212;
```

---

## 8. step2 비용 정량 추정

### 8.1 현재 비용

| 항목 | 값 |
|---|---|
| 대상 청크 | ~937개 |
| 평균 청크 토큰 | ~1,600 (chunks.json의 token_count) |
| 프롬프트 오버헤드 | ~500 토큰/요청 |
| 입력 토큰 합계 | 937 x 2,100 ≈ **1.97M 토큰** |
| 출력 토큰 (예상) | 937 x 800 ≈ **0.75M 토큰** |
| Gemini Flash 단가 | 입력 $0.075/1M, 출력 $0.30/1M |
| **총 비용** | 1.97 x 0.075 + 0.75 x 0.30 ≈ **$0.37** |
| 소요 시간 | 동시성 10 기준 약 **5~10분** |

### 8.2 교정 후 예상 비용

| 항목 | 값 |
|---|---|
| 대상 청크 | ~200개 (매트릭스 등 step1 커버) |
| 입력 토큰 합계 | 200 x 2,100 ≈ **0.42M 토큰** |
| 출력 토큰 | 200 x 800 ≈ **0.16M 토큰** |
| **총 비용** | 0.42 x 0.075 + 0.16 x 0.30 ≈ **$0.08** |
| 소요 시간 | ~**2분** |
| **절감율** | 비용 **78% 절감**, 시간 **60~80% 절감** |

---

## 9. 수정된 전체 실행 순서

```
[Phase 0] 사전 준비
  │
  ├─ 0-1. phase2_output 백업                    ← 6장
  │
  ├─ 0-2. Case D 구현                          ← 2장
  │       step1_table_extractor.py 수정
  │       (is_matrix_table, detect_matrix_meta,
  │        extract_from_matrix_table, 분기 수정)
  │
  ├─ 0-3. step3 병합 버그 수정                   ← 4장
  │       step3_relation_builder.py 수정
  │       (관계 병합에서 테이블 수치 우선)
  │
  └─ 0-4. 단위 테스트 실행 (test_case_d.py)      ← 5장
          ❌ 실패 → 0-2로 돌아가서 수정
          ✅ 통과 → Phase 1 진행
                │
[Phase 1] chunks.json 패치 + 재추출
  │
  ├─ 1-1. python patch_table_types.py           ← 3장
  │       (D_기타 → A_품셈 재분류, 자동 백업)
  │
  ├─ 1-2. python step1_table_extractor.py       ← 전체 재실행
  │       검증: 엔티티 6,000+개, 커버리지 85%+
  │       ❌ 미달 → Case D 로직 보완
  │
  ├─ 1-3. python step2_llm_extractor.py         ← 잔여 청크만
  │       검증: 대상 200개 이하
  │
  ├─ 1-4. python step3_relation_builder.py      ← 병합 (수정된 코드)
  │       검증: 13-2-3 관계에서 테이블 수치 확인
  │
  ├─ 1-5. python step4_normalizer.py            ← 정규화
  │       검증: spec 다른 WorkType 미합산 확인
  │
  └─ 1-6. python step5_extraction_validator.py  ← 품질 검증
          ❌ 실패 → 원인 분석 후 해당 단계 수정
          ✅ 통과 → Phase 2 진행
                │
[Phase 2] 원본 대조 + DB 교체
  │
  ├─ 2-1. python verify_original_match.py       ← 수치 100% 대조
  │       ❌ 불일치 → Phase 1로 회귀
  │
  ├─ 2-2. Supabase DB 백업 (SQL)                ← 6장
  │
  ├─ 2-3. python step6_supabase_loader.py       ← DB 적재
  │
  ├─ 2-4. python step7_embedding_generator.py   ← 임베딩 재생성
  │
  └─ 2-5. RAG 챗봇 검증 (8건 필수 테스트)
          ❌ 실패 → DB 롤백 (7장) + 원인 분석
          ✅ 통과 → 교정 완료
```

---

## 10. 파일 변경 요약

| 구분 | 파일 | 변경 내용 |
|---|---|---|
| **신규** | `phase2_extraction/patch_table_types.py` | chunks.json 테이블 타입 패치 |
| **신규** | `phase2_extraction/test_case_d.py` | Case D 단위 테스트 |
| **수정** | `phase2_extraction/step1_table_extractor.py` | Case D 함수 3개 추가 + 분기 수정 |
| **수정** | `phase2_extraction/step3_relation_builder.py` | 관계/엔티티 병합에서 테이블 수치 우선 |
| 재실행 | `step1` ~ `step7` | 코드 수정 없이 순차 재실행 |

---

## 11. 완료 기준

| 기준 | 지표 | 목표 |
|---|---|---|
| 단위 테스트 | test_case_d.py 5개 테스트 | **전체 통과** |
| step1 커버리지 | 규칙 추출 엔티티 수 | **6,000개+** (현재 3,483) |
| step2 대상 축소 | LLM 처리 청크 수 | **200개 이하** (현재 937) |
| step3 병합 정합성 | 테이블 수치 보존율 | **100%** |
| 원본 대조 | 수치 일치율 | **100%** |
| RAG 검증 | 8건 테스트케이스 통과율 | **100%** |
