# Graph DB 대규모 데이터 교정 상세 구현계획서

> **작성일**: 2026-02-12
> **기반 문서**: `20260212_Anti_GraphDB_데이터교정_검토체크리스트.md` + 상세 검토 결과
> **대상 시스템**: 건설공사 표준품셈 GraphRAG
> **실행 환경**: 로컬 PC (Python 3.12+), Supabase PostgreSQL

---

## 1. 작업 목표

Graph DB에 적재된 엔티티/관계 데이터의 **수치 불일치, 직종 혼동, 대량 누락** 문제를 해결한다.

### 1.1 현황 요약

| 항목 | 수치 | 비고 |
|---|---:|---|
| 전체 엔티티 | 16,424개 | |
| LLM 추출 (오류 가능) | 12,026개 (73%) | 매트릭스 테이블 등 복잡 구조에서 오류 |
| 규칙 추출 (정확) | 3,483개 (21%) | Case A/B/C만 지원 |
| 규칙 미인식 → D_기타 분류 | **937+ section** | step1 스킵 → LLM 폴백 |

### 1.2 검토에서 발견된 문제 (검토 체크리스트 보완)

| # | 문제 | 심각도 | 체크리스트 원본 판정 | 실제 검증 결과 |
|---|---|---|---|---|
| 1 | 직종 혼동 | **심각** | "모든 SCH에서 플랜트용접공 통일" | LLM 추출 5개 샘플은 직종 정확, 나머지 148개는 **미추출** |
| 2 | 수치 전면 불일치 | **심각** | "DB 값 0.244, 0.294 등 불일치" | llm_entities.json에는 해당 값 없음 → **step3~5 병합/정규화 과정에서 변형 가능성** |
| 3 | 특별인부 허위 생성 | 중간 | "LLM이 TIG 구조 혼동" | C-0955-B에서는 미발견, C-0955-C/D 확인 필요 |
| 4 | 규격 대량 누락 | **심각** | "SCH 100~160 일부 누락" | 153개 조합 중 **5개만 추출** (97% 누락) |
| 5 | **테이블 타입 오분류** (신규) | **심각** | 미언급 | `T-13-2-3-02`가 `D_기타`로 분류 → step1 자체가 실행 불가 |
| 6 | **step3~5 데이터 변형** (신규) | 중간 | 미언급 | LLM 추출값(정확) → DB 적재값(불정확) 경로 추적 필요 |

### 1.3 근본 원인 (보완)

```
① PDF → MD 파싱           ✅ 정확
② MD → chunks JSON        ✅ 정확 (매트릭스 데이터 온전)
③ chunks → 테이블 타입 분류  ❌ 매트릭스 테이블을 D_기타로 오분류
④ step1 규칙 추출           ❌ D_기타 스킵 + 매트릭스 패턴 미지원 (이중 실패)
⑤ step2 LLM 추출           ⚠️ 극소수 샘플만 추출 (153개 중 5개), 추출된 값은 정확
⑥ step3~5 병합/정규화       ⚠️ LLM 정확값이 최종 DB에서 변형 가능성 (미확인)
```

---

## 2. 전체 아키텍처

```
Phase 0: 사전 분석
  ├─ 0-1. 미인식 테이블 전수 조사 (analyze_unhandled_tables.py)
  ├─ 0-2. step3~5 데이터 변형 경로 추적 (trace_data_transform.py)
  └─ 0-3. 영향 section 우선순위 분류

Phase 1: 테이블 분류 + 규칙 추출기 강화
  ├─ 1-1. Phase 1 테이블 타입 분류기 개선 (step2_table_parser.py)
  ├─ 1-2. step1에 Case D (매트릭스) 패턴 추가
  ├─ 1-3. step1에 Case E (복합 직종 헤더) 패턴 추가
  └─ 1-4. step1에 Case F (범위 값) 패턴 추가

Phase 2: 전체 재추출
  ├─ 2-1. step1 재실행 → 커버리지 측정
  ├─ 2-2. step2 LLM 재실행 (잔여 청크)
  └─ 2-3. step3(병합) → step4(정규화) → step5(검증) 순차 실행

Phase 3: 원본 대조 검증
  ├─ 3-1. chunks.json 원본 대비 수치 100% 대조
  ├─ 3-2. 직종 매핑 정확성 검증
  └─ 3-3. 불일치 건수 0건 확인

Phase 4: DB 교체 + 서빙 검증
  ├─ 4-1. 기존 DB 백업
  ├─ 4-2. step6(DB 적재) → step7(임베딩 재생성)
  └─ 4-3. RAG 챗봇 검증
```

---

## 3. Phase 0: 사전 분석

### 3.1 미인식 테이블 전수 조사

> **신규 파일**: `phase2_extraction/analyze_unhandled_tables.py`

#### 목적
`D_기타`로 분류된 테이블 중 실제로는 품셈 데이터를 포함하는 테이블의 패턴을 분류한다.

#### 입력/출력

```
입력: phase1_output/chunks.json
출력: phase2_output/unhandled_table_analysis.json
```

#### 분석 로직

```python
# 의사 코드
for chunk in chunks:
    for table in chunk["tables"]:
        if table["type"] == "D_기타":
            pattern = detect_pattern(table)
            # pattern 종류:
            #   "matrix"     → 헤더=SCH, 행=구경, 값=수량 (구경×SCH 매트릭스)
            #   "multi_job"  → 직종 정보가 행 데이터에 포함
            #   "range_val"  → 셀 값이 "16.5~25.1" 형태 범위
            #   "material"   → 자재 소요량 테이블
            #   "unknown"    → 판별 불가
            report[pattern].append({
                "table_id": table["table_id"],
                "section_id": chunk["section_id"],
                "headers": table["headers"],
                "row_count": len(table["rows"]),
                "sample_row": table["rows"][0] if table["rows"] else None,
            })
```

#### 매트릭스 테이블 탐지 기준

```python
def is_matrix_table(table):
    """매트릭스 테이블 판별"""
    headers = table["headers"]
    rows = table["rows"]

    # 기준 1: 헤더 중 50% 이상이 숫자 (SCH 번호, 구경 등)
    numeric_headers = sum(1 for h in headers[1:] if re.match(r'^\d+$', str(h).strip()))
    if numeric_headers / max(len(headers) - 1, 1) < 0.5:
        return False

    # 기준 2: 데이터 행에 숫자 셀이 다수
    numeric_cells = 0
    total_cells = 0
    for row in rows[2:]:  # 첫 2행은 헤더 보조행(직종, 단위) 가능성
        for key, val in row.items():
            if key == headers[0]:
                continue
            total_cells += 1
            if isinstance(val, (int, float)) or re.match(r'^[0-9.]+$', str(val).strip()):
                numeric_cells += 1

    return total_cells > 0 and numeric_cells / total_cells > 0.3
```

#### 출력 포맷

```json
{
  "summary": {
    "total_d_tables": 482,
    "matrix": 67,
    "multi_job": 23,
    "range_val": 89,
    "material": 145,
    "unknown": 158
  },
  "matrix_tables": [
    {
      "table_id": "T-13-2-3-02",
      "section_id": "13-2-3",
      "title": "강관용접",
      "headers": ["SCH No.", "20", "30", "40", ...],
      "row_count": 18,
      "meta_rows": [
        {"type": "job_type", "data": {"20": "용접공", "40": "플랜트 용접공", ...}},
        {"type": "unit", "data": {"20": "(인)", "40": "(인)", ...}}
      ],
      "data_rows_count": 15,
      "estimated_entities": 135
    }
  ]
}
```

### 3.2 step3~5 데이터 변형 경로 추적

> **신규 파일**: `phase2_extraction/trace_data_transform.py`

#### 목적
LLM이 정확하게 추출한 값(예: φ200 SCH 20 = 0.287)이 최종 DB에서 다른 값(0.244)으로 변형되는 경로를 추적한다.

#### 추적 대상

```
llm_entities.json → merged_entities.json → normalized_entities.json → Supabase DB
```

#### 핵심 로직

```python
# 13-2-3 강관용접 섹션의 데이터 추적
target_section = "13-2-3"
target_values = {
    ("강관용접 (φ 200, SCH 20)", "용접공"): 0.287,
    ("강관용접 (φ 15, SCH 40)", "플랜트 용접공"): 0.066,
}

# 각 단계별 파일에서 해당 값 검색
for step_file in [llm_entities, merged_entities, normalized_entities]:
    find_value_in_step(step_file, target_section, target_values)
    # → 어느 단계에서 값이 변형/소실되는지 보고
```

#### 출력: 변형 보고서

```json
{
  "trace_results": [
    {
      "entity": "강관용접 (φ 200, SCH 20)",
      "labor": "용접공",
      "expected": 0.287,
      "llm_entities": {"found": true, "value": 0.287},
      "merged_entities": {"found": true, "value": 0.287},
      "normalized_entities": {"found": true, "value": 0.287},
      "supabase_db": {"found": true, "value": "???"},
      "verdict": "DB 적재 단계에서 변형 / 또는 다른 청크 데이터와 충돌"
    }
  ]
}
```

### 3.3 영향 section 우선순위 분류

`D_기타` 테이블을 포함하는 937개 section을 **교정 우선순위**로 분류한다.

| 우선순위 | 기준 | 예상 건수 | 교정 방법 |
|---|---|---:|---|
| P1 (긴급) | 매트릭스 테이블 포함, 수치 데이터 대량 | ~70 | Case D 규칙 추가 |
| P2 (중요) | 복합 직종/범위 값 포함 | ~110 | Case E/F 규칙 추가 |
| P3 (보통) | 자재 소요량 등 단순 테이블 | ~150 | 기존 LLM 추출로 충분 |
| P4 (관찰) | 텍스트/참조 정보 (D_기타 정당) | ~600 | 교정 불필요 |

---

## 4. Phase 1: 테이블 분류 + 규칙 추출기 강화

### 4.1 테이블 타입 분류기 개선

> **수정 파일**: `phase1_preprocessing/step2_table_parser.py`
> **수정 파일**: `phase1_preprocessing/config.py`

#### 현재 문제

`classify_table()` 함수의 `A_품셈` 판별 기준:

```python
# 현재: 헤더 키워드 2개 이상 매칭
a_keywords = ["수량", "단위", "인", "대", "수 량", "단 위"]
if sum(1 for kw in a_keywords if kw in header_text) >= 2:
    return "A_품셈"
```

매트릭스 테이블 `["SCH No.", "20", "30", "40", ...]` → "인", "대" 등이 헤더에 없음 → `D_기타`로 분류됨.

전치 테이블 보강 로직도 존재하지만:

```python
# 행 첫 열에 노무 키워드가 있는지 검사
_LABOR_ROW_KEYWORDS = ["인부", "철공", "용접공", ...]
```

매트릭스 테이블의 첫 열은 `"직종 구경"`, `"(인)"`, `"mm"`, `"φ 15"` 등이므로 이 검사도 통과하지 못함.

#### 개선 방안

```python
# config.py에 추가
TABLE_TYPE_KEYWORDS = {
    "A_품셈": ["수량", "단위", "인", "대", "수 량", "단 위"],
    "B_규모기준": ["억", "m²", "규모", "직접노무비"],
    "C_구분설명": ["구분", "내용", "구 분", "내 용"],
    # 신규 추가
    "A_매트릭스_직종키워드": ["용접공", "플랜트", "인부", "배관공", "기능공"],
}
```

```python
# step2_table_parser.py - classify_table() 수정

def classify_table(headers, rows):
    # 기존 A_품셈 판별 (유지)
    ...

    # 기존 전치 테이블 감지 (유지)
    ...

    # [신규] 매트릭스 테이블 감지
    # 조건: (1) 헤더에 숫자열이 50% 이상 + (2) 행 데이터에 직종 키워드 포함
    if rows and len(headers) >= 4:
        numeric_headers = sum(
            1 for h in headers[1:]
            if re.match(r'^\d+$', str(h).strip())
        )
        if numeric_headers / (len(headers) - 1) >= 0.5:
            # 행 데이터에서 직종 키워드 검색
            matrix_job_keywords = TABLE_TYPE_KEYWORDS["A_매트릭스_직종키워드"]
            for row in rows[:3]:  # 첫 3행만 검사 (메타행)
                row_text = " ".join(str(v) for v in row)
                if any(kw in row_text for kw in matrix_job_keywords):
                    return "A_품셈"  # 매트릭스 품셈 테이블

    # B_규모기준, C_구분설명 (기존 유지)
    ...

    return "D_기타"
```

#### 영향 범위

- 이 수정은 `chunks.json` 내 테이블의 `type` 필드를 변경함
- **Phase 1 재실행 필요**: `step2_table_parser.py` 수정 후 Phase 1 전체 재파싱 또는 `chunks.json` 직접 패치
- **권장**: `chunks.json`을 직접 패치하는 보조 스크립트 작성 (Phase 1 전체 재실행 비용 회피)

```python
# patch_table_types.py (신규)
# chunks.json에서 D_기타 테이블 중 매트릭스 패턴을 A_품셈으로 재분류
```

### 4.2 Case D: 매트릭스 테이블 패턴

> **수정 파일**: `phase2_extraction/step1_table_extractor.py`

#### 대상 구조

```
헤더:    [SCH No. | 20  | 30  | 40     | 60     | 80     | ...]
행 0:    [직종구경 | 용접공 | 용접공 | 플랜트용접공 | 플랜트용접공 | ...]  ← 직종 메타행
행 1:    [직종구경 | (인) | (인) | (인)    | (인)    | ...]           ← 단위 메타행
행 2:    [mm      |     |     |        |        | ...]              ← 구분 메타행
행 3:    [φ 15    |     |     | 0.066  |        | 0.075  | ...]     ← 데이터행
행 4:    [20      |     |     | 0.075  |        | 0.083  | ...]
...
행 17:   [350     | 0.442| 0.462| 0.537 | 0.760  | 0.940  | ...]
```

#### 매트릭스 인식 로직

```python
def detect_matrix_meta(table):
    """매트릭스 테이블의 메타행(직종/단위)과 데이터행을 분리"""
    headers = table["headers"]
    rows = table["rows"]

    meta_rows = []   # 직종/단위 정보를 담은 상단 행
    data_rows = []   # 실제 수치 데이터 행

    for row in rows:
        first_val = str(row.get(headers[0], "")).replace(" ", "")

        # 메타행 판별: 첫 열이 "직종", "단위", "mm" 등
        if any(kw in first_val for kw in ["직종", "단위", "mm", "구분", "구경"]):
            meta_rows.append(row)
        # 데이터행 판별: 첫 열이 구경 값 (φ 15, 20, 25, ...)
        elif re.match(r'^[φΦ]?\s*\d+', first_val):
            data_rows.append(row)

    # 직종 매핑 구성: SCH 번호 → 직종명
    job_type_map = {}  # {"20": "용접공", "40": "플랜트 용접공", ...}
    unit_map = {}      # {"20": "인", "40": "인", ...}

    for meta_row in meta_rows:
        for h in headers[1:]:
            val = str(meta_row.get(h, "")).strip()
            if any(kw in val for kw in ["공", "기사", "인부", "기능"]):
                job_type_map[h] = val
            elif val in ("(인)", "인", "(대)", "대"):
                unit_map[h] = val.strip("()")

    return job_type_map, unit_map, data_rows
```

#### 엔티티/관계 생성 로직

```python
def extract_from_matrix_table(table, chunk_id, section_id, section_title):
    """Case D: 매트릭스 테이블 추출"""
    entities = []
    relationships = []
    warnings = []

    headers = table["headers"]
    job_type_map, unit_map, data_rows = detect_matrix_meta(table)

    if not job_type_map:
        warnings.append(f"매트릭스 테이블이지만 직종 메타행 없음: {headers}")
        return entities, relationships, warnings

    for row in data_rows:
        # 구경 추출: "φ 15" → "φ15", "200" → "200"
        diameter_raw = str(row.get(headers[0], "")).strip()
        diameter = re.sub(r'\s+', '', diameter_raw)

        for sch_header in headers[1:]:
            qty_val, raw_val = parse_cell_value(row.get(sch_header))
            if qty_val is None or qty_val == 0:
                continue

            # 직종 결정 (메타행에서)
            job_name = job_type_map.get(sch_header, "")
            if not job_name:
                warnings.append(f"SCH {sch_header}의 직종 미매핑, 구경={diameter}")
                continue
            job_name = normalize_entity_name(job_name)

            # 단위 결정
            unit_val = unit_map.get(sch_header, "인")

            # WorkType 엔티티: "강관용접 (φ200, SCH 40)"
            spec_str = f"{diameter}, SCH {sch_header}"
            wt_name = f"{section_title} ({spec_str})"

            wt_entity = Entity(
                type=EntityType.WORK_TYPE,
                name=wt_name,
                spec=spec_str,
                unit="개소당",  # section의 unit_basis에서 가져옴
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
                source_chunk_id=chunk_id,
            )
            relationships.append(rel)

    return entities, relationships, warnings
```

#### 기대 효과 (13-2-3 강관용접 예시)

| 항목 | 현재 (step1) | 교정 후 (Case D) |
|---|---:|---:|
| WorkType 엔티티 | 0개 | **~100개** (17구경 x 9SCH, 빈 셀 제외) |
| Labor 엔티티 | 0개 | **~100개** |
| REQUIRES_LABOR 관계 | 0개 | **~100개** |
| 직종 정확도 | N/A | **100%** (메타행 기반) |
| 수치 정확도 | N/A | **100%** (원본 셀 직접 사용) |

### 4.3 Case E: 복합 직종 헤더 패턴

#### 대상 구조

일부 테이블에서 직종 정보가 헤더의 상위 그룹에 포함되는 패턴:

```
헤더 예시: ["구경", "SCH", "용접공_1일당", "플랜트용접공_1일당", "특별인부_1일당"]
```

이 패턴은 **현재 Case A에서 이미 처리 가능**하나, 직종명이 `_1일당`, `_합계` 등 접미사를 포함할 때 정확한 직종 추출이 안 되는 경우가 있음.

#### 수정 사항

```python
# extract_labor_name_from_header() 개선 (step1_table_extractor.py)

def extract_labor_name_from_header(header: str) -> str:
    """composite 헤더에서 노무 명칭 추출 (강화)"""
    if "_" in header:
        parts = header.split("_")
        # "합계" 포함 → 스킵
        if any("합계" in p.replace(" ", "") for p in parts):
            return ""
        # "1일당" 등 접미사 제거 후 마지막 유의미 파트
        meaningful_parts = [
            p for p in parts
            if p.replace(" ", "") not in ("1일당", "합계", "인원수", "소계")
        ]
        name = meaningful_parts[-1].strip() if meaningful_parts else ""
    else:
        name = header.strip()

    return normalize_entity_name(name)
```

### 4.4 Case F: 범위 값 패턴

#### 대상 구조

자재 소요량 테이블에서 값이 범위로 표현:

```
| 철판두께 (mm) | 산소(ℓ)      | LPG(kg)       |
|           3  | 16.5~25.1    | 0.016~0.025   |
|           6  | 39.6~103     | 0.039~0.101   |
```

#### 수정 사항

```python
# parse_cell_value() 개선 (step1_table_extractor.py)

def parse_cell_value(val) -> tuple[float | None, str]:
    """셀 값 → (수치, 원본문자열) 변환 (범위 값 지원 추가)"""
    # 기존 로직 유지...

    s = str(val).strip()

    # [신규] 범위 값: "16.5~25.1" → 중간값 또는 None + 원본 보존
    range_match = re.match(r'^([0-9.,]+)\s*[~～\-]\s*([0-9.,]+)$', s)
    if range_match:
        try:
            low = float(range_match.group(1).replace(",", ""))
            high = float(range_match.group(2).replace(",", ""))
            # 범위 값은 엔티티 properties에 low/high로 보존
            # 수량 값으로는 None 반환 (범위는 단일 수치가 아니므로)
            return None, s  # 수량=None, 원본 보존
        except ValueError:
            pass

    # 기존 로직 계속...
```

```python
# 범위 값 전용 파서 (신규)
def parse_range_value(val) -> tuple[float | None, float | None, str]:
    """범위 값 → (low, high, 원본) 파싱"""
    s = str(val).strip()
    range_match = re.match(r'^([0-9.,]+)\s*[~～\-]\s*([0-9.,]+)$', s)
    if range_match:
        try:
            low = float(range_match.group(1).replace(",", ""))
            high = float(range_match.group(2).replace(",", ""))
            return low, high, s
        except ValueError:
            pass
    return None, None, s
```

#### 범위 값 엔티티 생성

```python
# Material 엔티티에 범위 정보 보존
material_entity = Entity(
    type=EntityType.MATERIAL,
    name="산소",
    unit="ℓ",
    quantity=None,  # 범위이므로 단일 수치 없음
    properties={
        "range_low": 16.5,
        "range_high": 25.1,
        "range_raw": "16.5~25.1",
        "spec": "철판두께 3mm",
    },
    source_method="table_rule",
)
```

### 4.5 extract_from_a_table 분기 수정

```python
# step1_table_extractor.py - extract_from_chunk() 수정

def extract_from_chunk(chunk):
    for table in tables:
        table_type = table.get("type", "")

        if table_type == "A_품셈":
            # [신규] 매트릭스 패턴 먼저 검사
            if is_matrix_table(table):
                ents, rels, warns = extract_from_matrix_table(
                    table, chunk_id, section_id, title
                )
            else:
                # 기존 Case A/B/C 로직
                ents, rels, warns = extract_from_a_table(
                    table, chunk_id, section_id, title
                )
        elif table_type == "B_규모기준":
            ents, rels, warns = extract_from_b_table(...)
        elif table_type == "D_기타":
            # [신규] D_기타에서도 매트릭스 패턴 재검사
            # (Phase 1 재실행 없이도 동작하도록 안전장치)
            if is_matrix_table(table):
                ents, rels, warns = extract_from_matrix_table(
                    table, chunk_id, section_id, title
                )
            else:
                ents, rels, warns = [], [], []
        else:
            ents, rels, warns = [], [], []
```

---

## 5. Phase 2: 전체 재추출

### 5.1 step1 재실행

```bash
cd G:\내 드라이브\Cluade code\python_code\phase2_extraction
python step1_table_extractor.py
```

#### 기대 결과

| 지표 | 현재 | 목표 |
|---|---:|---:|
| 규칙 추출 엔티티 | 3,483개 | **6,000개+** |
| 엔티티 커버리지 | ~60% | **85%+** |
| D_기타 스킵 테이블 | 937개 | **200개 이하** |

#### 커버리지 측정

```python
# 재실행 후 자동 출력되는 커버리지 통계로 확인
# 추가 검증: 13-2-3 강관용접 section에서 예상 엔티티 수 확인
expected = {
    "section": "13-2-3",
    "worktype_count": ">=100",   # 17구경 x 9SCH (빈 셀 제외)
    "labor_entities": ["용접공", "플랜트 용접공"],
    "sample_check": {
        "강관용접 (φ200, SCH 20)": {"labor": "용접공", "qty": 0.287},
        "강관용접 (φ200, SCH 40)": {"labor": "플랜트 용접공", "qty": 0.287},
        "강관용접 (φ200, SCH 80)": {"labor": "플랜트 용접공", "qty": 0.362},
    }
}
```

### 5.2 step2 LLM 재실행 (잔여 청크)

step1에서 커버하지 못한 잔여 청크에 대해서만 LLM 추출을 수행한다.

```python
# step2_llm_extractor.py는 기존 코드 사용
# 단, step1에서 이미 추출된 청크는 스킵하는 로직이 내장되어 있음
python step2_llm_extractor.py
```

#### LLM 의존도 변화 예상

| 항목 | 현재 | 교정 후 |
|---|---:|---:|
| LLM 처리 청크 | ~937개 | **~200개** |
| LLM 추출 엔티티 비율 | 73% | **~30%** |
| 규칙 추출 엔티티 비율 | 21% | **~65%** |

### 5.3 step3 ~ step5 순차 실행

```bash
python step3_relation_builder.py    # 병합
python step4_normalizer.py          # 정규화
python step5_extraction_validator.py  # 검증
```

#### step3~5 주의사항

Phase 0의 데이터 변형 추적 결과를 반영하여, 병합/정규화 로직에서 수치 변형이 발생하는 부분이 있다면 함께 수정해야 한다.

가능한 변형 원인:
- step3에서 같은 section의 중복 엔티티 병합 시 수치가 평균/합산되는 경우
- step4에서 이름 정규화 시 서로 다른 구경의 WorkType이 같은 이름으로 합쳐지는 경우
- **`normalized_name`** 생성 시 spec 정보가 소실되어 서로 다른 엔티티가 동일 키로 병합

---

## 6. Phase 3: 원본 대조 검증

### 6.1 수치 100% 대조 검증기

> **신규 파일**: `phase2_extraction/verify_original_match.py`

#### 핵심 로직

```python
def verify_against_original(chunks_file, extraction_file):
    """chunks.json 원본 데이터와 추출 결과의 수치 100% 일치 확인"""

    chunks = load_json(chunks_file)["chunks"]
    extractions = load_json(extraction_file)

    mismatches = []
    total_checked = 0

    for chunk in chunks:
        for table in chunk.get("tables", []):
            if table["type"] not in ("A_품셈",):
                continue

            # 원본 테이블에서 모든 수치 셀 추출
            for row in table["rows"]:
                for header, value in row.items():
                    num_val, _ = parse_cell_value(value)
                    if num_val is None:
                        continue

                    total_checked += 1

                    # 추출 결과에서 같은 값을 가진 엔티티/관계 찾기
                    found = find_matching_entity(
                        extractions,
                        chunk["section_id"],
                        chunk["chunk_id"],
                        num_val,
                    )
                    if not found:
                        mismatches.append({
                            "section_id": chunk["section_id"],
                            "table_id": table["table_id"],
                            "header": header,
                            "original_value": num_val,
                            "status": "NOT_FOUND",
                        })

    return {
        "total_checked": total_checked,
        "mismatches": len(mismatches),
        "match_rate": (total_checked - len(mismatches)) / total_checked,
        "details": mismatches,
    }
```

#### 합격 기준

| 지표 | 기준 |
|---|---|
| 수치 일치율 | **100%** (0건 불일치) |
| 직종 매핑 일치율 | **100%** |
| 누락 셀 | **0건** (원본에 값이 있는 셀은 반드시 추출) |

### 6.2 직종 매핑 검증

```python
def verify_job_types(chunks_file, extraction_file):
    """매트릭스 테이블의 직종 매핑 정확성 검증"""

    # 원본 매트릭스 테이블에서 SCH → 직종 매핑 추출
    # 추출 결과에서 해당 WorkType의 Labor 관계 직종 비교
    # 불일치 건수 보고

    expected = {
        "13-2-3": {
            "SCH 20": "용접공",
            "SCH 30": "용접공",
            "SCH 40": "플랜트 용접공",
            "SCH 60": "플랜트 용접공",
            "SCH 80": "플랜트 용접공",
            "SCH 100": "플랜트 용접공",
            "SCH 120": "플랜트 용접공",
            "SCH 140": "플랜트 용접공",
            "SCH 160": "플랜트 용접공",
        }
    }
```

---

## 7. Phase 4: DB 교체 + 서빙 검증

### 7.1 기존 DB 백업

```sql
-- Supabase SQL Editor
CREATE TABLE graph_entities_backup_20260212 AS SELECT * FROM graph_entities;
CREATE TABLE graph_relationships_backup_20260212 AS SELECT * FROM graph_relationships;
CREATE TABLE graph_chunks_backup_20260212 AS SELECT * FROM graph_chunks;
```

### 7.2 DB 적재 + 임베딩 재생성

```bash
python step6_supabase_loader.py     # 기존 테이블 TRUNCATE + 재적재
python step7_embedding_generator.py  # 임베딩 벡터 재생성
```

### 7.3 RAG 챗봇 검증 테스트케이스

#### 필수 검증 항목 (8건)

| # | 질문 | 기대 응답 | 검증 포인트 |
|---|---|---|---|
| 1 | "강관용접 200mm SCH 40 품셈" | 플랜트 용접공 **0.287인** | 수치 정확성 |
| 2 | "강관용접 200mm SCH 20 품셈" | **용접공** 0.287인 | 직종 구분 (용접공 vs 플랜트) |
| 3 | "강관용접 200mm SCH 80 품셈" | 플랜트 용접공 **0.362인** | SCH별 수치 차이 |
| 4 | "강관용접 전체 규격" | 17구경 x 9SCH 전체 데이터 | 대량 데이터 누락 없음 |
| 5 | "강관용접 φ15 SCH 80 품셈" | 플랜트 용접공 **0.075인** | 소구경 검증 |
| 6 | "강관용접 φ350 SCH 20 품셈" | **용접공** 0.442인 | 대구경 + 용접공 직종 |
| 7 | "강관용접 φ15 SCH 20 품셈" | **데이터 없음** (빈 셀) | 빈 값 처리 검증 |
| 8 | "TIG 용접 φ200 품셈" | 별도 테이블의 정확한 값 | 테이블 간 데이터 격리 |

#### 확장 검증 항목 (랜덤 샘플)

- 매트릭스 테이블이 있는 다른 section에서 랜덤 3건 추출하여 검증
- Phase 0에서 식별된 P1(긴급) section 중 랜덤 5건 검증

---

## 8. 파일 변경 요약

### 신규 파일

| 파일 | 용도 | Phase |
|---|---|---|
| `phase2_extraction/analyze_unhandled_tables.py` | D_기타 테이블 전수 분석 | 0-1 |
| `phase2_extraction/trace_data_transform.py` | step3~5 변형 추적 | 0-2 |
| `phase2_extraction/patch_table_types.py` | chunks.json 테이블 타입 패치 | 1-1 |
| `phase2_extraction/verify_original_match.py` | 원본 대조 검증 | 3 |

### 수정 파일

| 파일 | 수정 내용 | Phase |
|---|---|---|
| `phase1_preprocessing/config.py` | `TABLE_TYPE_KEYWORDS`에 매트릭스 직종 키워드 추가 | 1-1 |
| `phase1_preprocessing/step2_table_parser.py` | `classify_table()` 매트릭스 분기 추가 | 1-1 |
| `phase2_extraction/step1_table_extractor.py` | Case D 추가, Case E/F 개선, D_기타 재검사 | 1-2~1-4 |

### 기존 파일 (수정 없음, 재실행만)

| 파일 | 용도 | Phase |
|---|---|---|
| `step2_llm_extractor.py` | 잔여 청크 LLM 재추출 | 2-2 |
| `step3_relation_builder.py` | 병합 | 2-3 |
| `step4_normalizer.py` | 정규화 | 2-3 |
| `step5_extraction_validator.py` | 검증 | 2-3 |
| `step6_supabase_loader.py` | DB 적재 | 4-2 |
| `step7_embedding_generator.py` | 임베딩 재생성 | 4-2 |

---

## 9. 실행 순서 체크리스트

### Phase 0: 사전 분석

- [ ] **0-1**: `analyze_unhandled_tables.py` 작성 및 실행
  - [ ] D_기타 테이블 패턴별 분류 결과 확인
  - [ ] 매트릭스 테이블 목록 및 예상 엔티티 수 산출
- [ ] **0-2**: `trace_data_transform.py` 작성 및 실행
  - [ ] llm_entities → merged → normalized → DB 각 단계별 값 추적
  - [ ] 변형 발생 지점 식별 (있을 경우 step3~5 수정 계획 수립)
- [ ] **0-3**: 영향 section 우선순위 분류 (P1~P4)

### Phase 1: 추출기 강화

- [ ] **1-1**: `step2_table_parser.py` 매트릭스 분류 추가
  - [ ] `config.py`에 `A_매트릭스_직종키워드` 추가
  - [ ] `classify_table()` 매트릭스 분기 추가
  - [ ] `patch_table_types.py`로 chunks.json 패치 (또는 Phase 1 재실행)
- [ ] **1-2**: `step1_table_extractor.py`에 Case D 추가
  - [ ] `detect_matrix_meta()` 함수 구현
  - [ ] `extract_from_matrix_table()` 함수 구현
  - [ ] `extract_from_chunk()`에서 매트릭스 분기 추가
  - [ ] `is_matrix_table()` 판별 함수 구현
- [ ] **1-3**: Case E 복합 직종 헤더 개선
  - [ ] `extract_labor_name_from_header()` 접미사 처리 강화
- [ ] **1-4**: Case F 범위 값 개선
  - [ ] `parse_range_value()` 함수 추가
  - [ ] Material 엔티티에 range_low/high properties 보존
- [ ] **1-5**: 단위 테스트 (13-2-3 강관용접 1개 section)
  - [ ] step1 단독 실행 → 예상 엔티티/관계 수 확인
  - [ ] 직종 매핑 정확성 확인 (SCH 20/30=용접공, 40~160=플랜트)
  - [ ] 수치 정확성 확인 (φ200 SCH 20=0.287, SCH 80=0.362)

### Phase 2: 전체 재추출

- [ ] **2-1**: step1 재실행
  - [ ] 커버리지 85%+ 확인
  - [ ] 규칙 추출 엔티티 6,000개+ 확인
- [ ] **2-2**: step2 LLM 재실행 (잔여 청크)
  - [ ] LLM 처리 청크 200개 이하 확인
- [ ] **2-3**: step3 → step4 → step5 순차 실행
  - [ ] step3 병합 결과 검증 (수치 변형 없음 확인)
  - [ ] step4 정규화 결과 검증 (spec 정보 보존 확인)
  - [ ] step5 품질 검증 통과

### Phase 3: 원본 대조 검증

- [ ] **3-1**: `verify_original_match.py` 실행
  - [ ] 수치 일치율 100% 확인
  - [ ] 직종 매핑 일치율 100% 확인
  - [ ] 누락 셀 0건 확인
- [ ] **3-2**: 불일치 발견 시 → Phase 1로 돌아가서 수정

### Phase 4: DB 교체 + 서빙 검증

- [ ] **4-1**: 기존 DB 백업 (3개 테이블)
- [ ] **4-2**: step6 적재 → step7 임베딩 재생성
- [ ] **4-3**: RAG 챗봇 검증 (8건 필수 + 확장 8건)
  - [ ] "강관용접 200mm SCH 40 품셈" → 플랜트 용접공 0.287인
  - [ ] "강관용접 200mm SCH 20 품셈" → **용접공** 0.287인
  - [ ] "강관용접 200mm SCH 80 품셈" → 플랜트 용접공 0.362인
  - [ ] "강관용접 전체 규격" → 17구경 x 9SCH 전체 데이터
  - [ ] "강관용접 φ15 SCH 80 품셈" → 0.075인
  - [ ] "강관용접 φ350 SCH 20 품셈" → **용접공** 0.442인
  - [ ] "강관용접 φ15 SCH 20 품셈" → 데이터 없음 (빈 셀)
  - [ ] "TIG 용접 φ200 품셈" → 별도 테이블 값

---

## 10. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| 매트릭스 테이블이 예상보다 다양한 구조 | Phase 1 지연 | Phase 0 분석에서 모든 변형 식별, 점진적 Case 추가 |
| step3~5에서 수치 변형 발견 | Phase 2 수정 필요 | Phase 0-2 추적에서 조기 발견, 병합 로직 수정 |
| chunks.json 패치 후 다른 section 영향 | 기존 정상 데이터 손상 | 패치 전 원본 백업, 변경 section만 선별 패치 |
| LLM 재추출 비용/시간 | API 비용 증가 | step1 커버리지 최대화로 LLM 호출 최소화 |
| Supabase DB 용량 증가 (엔티티 2배) | 쿼리 성능 저하 | 인덱스 최적화, 임베딩 차원 검토 |

---

## 11. 완료 기준

| 기준 | 지표 | 목표 |
|---|---|---|
| 수치 정확성 | 원본 대비 수치 일치율 | **100%** |
| 직종 정확성 | 원본 대비 직종 매핑 일치율 | **100%** |
| 데이터 완전성 | 원본 수치 셀 중 추출된 비율 | **95%+** |
| 규칙 추출 비율 | 전체 엔티티 중 규칙 추출 비율 | **60%+** (현재 21%) |
| RAG 응답 정확성 | 검증 테스트케이스 통과율 | **100%** (16/16건) |
