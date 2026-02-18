# Phase 2: 전체 재추출 실행 계획서 (수정판)

> **작성일**: 2026-02-12  
> **기반**: 클로드 코드 구현계획서 검토 결과 + 현재 코드 상태 반영  
> **목적**: step3 병합 버그 수정 + step2 중복 방지 + 전체 파이프라인 재실행  

---

## 1. 현재 상태 요약

### 1.1 완료된 작업

| 항목 | 상태 | 근거 |
|---|---|---|
| Case D 매트릭스 추출 구현 | ✅ 완료 | `step1_table_extractor.py` L183~515에 `is_matrix_table`, `extract_from_matrix_table`, `_extract_d1_metarow`, `_extract_d2_compound` 4개 함수 구현됨 |
| 13-2-3 강관용접 검증 | ✅ 완료 | 10/10 데이터 포인트 MATCH, 922 entities / 793 relationships |
| `extract_from_chunk()` 분기 | ✅ 완료 | L902~914에서 `D_기타`/`C_구분설명` 테이블에 대해 `is_matrix_table()` 재검사 후 규칙 추출 수행 |

### 1.2 발견된 문제 (미수정)

| # | 문제 | 심각도 | 수정 대상 |
|---|---|---|---|
| **P1** | step3 관계 병합에서 **LLM 수치가 테이블보다 우선** 적용 | 🔴 높음 | `step3_relation_builder.py` L104~117 |
| **P2** | step2 `select_llm_target_chunks()`가 **D_기타 테이블을 무조건 LLM 대상으로 선정** | 🟡 중간 | `step2_llm_extractor.py` L374~378 |
| **P3** | step3 엔티티 병합에서 테이블 수치 보강이 **None일 때만** 작동 | 🟡 중간 | `step3_relation_builder.py` L87~90 |

---

## 2. 수정 작업 (총 2건)

### 2.1 [P1+P3] step3 병합 버그 수정

> **수정 파일**: `phase2_extraction/step3_relation_builder.py`

#### 현재 코드 (관계 병합 L104~117)

```python
# 문제: LLM 관계가 먼저 등록되고, 같은 키의 테이블 관계가 버려짐
for rel in llm_ext.get("relationships", []):
    key = _rel_key(rel)
    merged_rels.append(rel)
    llm_rel_keys.add(key)

for trel in table_ext.get("relationships", []):
    key = _rel_key(trel)
    if key not in llm_rel_keys:  # ← 같은 키면 테이블 무시됨!
        merged_rels.append(trel)
```

#### 수정안

```python
# 수정: LLM 관계를 기본으로, 테이블 수치(quantity/unit)로 덮어쓰기
merged_rel_map: dict[str, dict] = {}
merged_rels: list[dict] = []

for rel in llm_ext.get("relationships", []):
    key = _rel_key(rel)
    if key not in merged_rel_map:
        merged_rel_map[key] = rel
        merged_rels.append(rel)

for trel in table_ext.get("relationships", []):
    key = _rel_key(trel)
    if key in merged_rel_map:
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
```

#### 현재 코드 (엔티티 병합 L87~90)

```python
# 문제: 테이블 quantity는 LLM이 None일 때만 덮어씀
if tent.get("quantity") is not None and existing.get("quantity") is None:
    existing["quantity"] = tent["quantity"]
if tent.get("unit") and not existing.get("unit"):
    existing["unit"] = tent["unit"]
```

#### 수정안

```python
# 수정: 테이블에 수치가 있으면 무조건 덮어쓰기 (테이블이 더 정확)
if tent.get("quantity") is not None:
    existing["quantity"] = tent["quantity"]
if tent.get("unit"):
    existing["unit"] = tent["unit"]
```

---

### 2.2 [P2] step2 대상 선별 보완

> **수정 파일**: `phase2_extraction/step2_llm_extractor.py`

#### 현재 코드 (L374~378)

```python
# 문제: D_기타/C_구분설명만 보고 LLM 대상으로 선정
# → step1에서 매트릭스 추출에 성공해도 중복 LLM 호출 발생
table_types = {t.get("type", "") for t in tables}
if table_types <= {"D_기타", "C_구분설명"}:
    targets.append(chunk)
    reasons["D_기타/C_구분설명 테이블만"] += 1
    continue
```

#### 수정안 (2가지 옵션)

**옵션 A (코드만 수정 — 추천)**: step1 결과에서 WorkType이 추출된 청크는 건너뛰기

```python
# 조건 2: D_기타/C_구분설명만 있는 테이블
# 단, step1에서 이미 WorkType을 추출한 청크는 제외
table_types = {t.get("type", "") for t in tables}
if table_types <= {"D_기타", "C_구분설명"}:
    has_worktype = any(e.type == EntityType.WORK_TYPE for e in s1.entities)
    if not has_worktype:
        targets.append(chunk)
        reasons["D_기타/C_구분설명 테이블만 (WorkType 없음)"] += 1
        continue
```

**옵션 B (chunks.json 패치)**: 별도 스크립트로 `D_기타` → `A_품셈` 재분류 후 실행

> 옵션 A가 코드 변경 최소, 데이터 파일 수정 불필요하여 권장.

---

## 3. 실행 순서

```
[사전 준비]
│
├─ 0-1. phase2_output 백업
│       phase2_output/backup_20260212/ 에 기존 JSON 복사
│
├─ 0-2. step3 병합 버그 수정 (P1+P3)       ← 2.1장
│
├─ 0-3. step2 대상 선별 수정 (P2)           ← 2.2장
│
└─ 0-4. 검증: 13-2-3 단일 청크 테스트
        ❌ 실패 → 수정 반복
        ✅ 통과 → Phase 1 진행

[Phase 1] 파이프라인 재실행
│
├─ 1-1. python step1_table_extractor.py     ~30초
│       검증: 엔티티 수 증가 확인 (기존 3,483 → 6,000+ 예상)
│
├─ 1-2. python step2_llm_extractor.py       ~2~5분
│       검증: 대상 청크 200개 이하 (기존 937)
│
├─ 1-3. python step3_relation_builder.py    ~30초
│       검증: 테이블 수치 보존 확인
│
├─ 1-4. python step4_normalizer.py          ~30초
│
└─ 1-5. python step5_extraction_validator.py ~1분 (E5 LLM 포함 시 +3분)
        ❌ FAIL → 원인 분석 후 해당 단계 수정
        ✅ PASS → Phase 2 진행

[Phase 2] DB 교체
│
├─ 2-1. Supabase DB 백업 (SQL)
│
├─ 2-2. python step6_supabase_loader.py
│
├─ 2-3. python step7_embedding_generator.py
│
└─ 2-4. RAG 챗봇 검증
        ❌ 실패 → DB 롤백 + 원인 분석
        ✅ 통과 → 교정 완료
```

---

## 4. 비용 추정

### step2 (LLM) 비용

| 항목 | 기존 | 교정 후 (예상) |
|---|---:|---:|
| 대상 청크 | 937개 | ~200개 |
| 입력 토큰 | 1.97M | 0.42M |
| 출력 토큰 | 0.75M | 0.16M |
| Gemini Flash 비용 | $0.37 | **$0.08** |
| 소요 시간 | 5~10분 | **~2분** |
| **절감율** | - | **78%** |

### step7 (Embedding) 비용

| 항목 | 값 |
|---|---|
| 대상 | 엔티티 ~6,000개 + 청크 ~1,500개 |
| embedding-001 단가 | 무료 (1분 1,500건 제한) |
| 소요 시간 | ~5분 |

---

## 5. 백업/롤백 절차

### 5.1 백업 (실행 전)

```python
# phase2_output 백업
import shutil
from pathlib import Path
from datetime import datetime

PHASE2_OUTPUT = Path(r"G:\내 드라이브\Antigravity\python_code\phase2_output")
BACKUP_DIR = PHASE2_OUTPUT / f"backup_{datetime.now().strftime('%Y%m%d_%H%M')}"
BACKUP_DIR.mkdir(exist_ok=True)

for f in ["table_entities.json", "llm_entities.json",
          "merged_entities.json", "normalized_entities.json"]:
    src = PHASE2_OUTPUT / f
    if src.exists():
        shutil.copy2(src, BACKUP_DIR / f)
        print(f"  백업: {f}")
```

### 5.2 Supabase DB 백업 (Phase 2 직전)

```sql
CREATE TABLE graph_entities_backup_20260212 AS SELECT * FROM graph_entities;
CREATE TABLE graph_relationships_backup_20260212 AS SELECT * FROM graph_relationships;
CREATE TABLE graph_chunks_backup_20260212 AS SELECT * FROM graph_chunks;
```

### 5.3 롤백

```bash
# 파일 롤백: 백업 폴더에서 복사
copy phase2_output\backup_YYYYMMDD_HHMM\*.json phase2_output\

# DB 롤백
TRUNCATE graph_entities, graph_relationships, graph_chunks;
INSERT INTO graph_entities SELECT * FROM graph_entities_backup_20260212;
INSERT INTO graph_relationships SELECT * FROM graph_relationships_backup_20260212;
INSERT INTO graph_chunks SELECT * FROM graph_chunks_backup_20260212;
```

---

## 6. 파일 변경 요약

| 구분 | 파일 | 변경 내용 |
|---|---|---|
| **수정** | `step3_relation_builder.py` | 관계 병합: 테이블 수치 우선 적용 (L104~117) |
| **수정** | `step3_relation_builder.py` | 엔티티 병합: 테이블 수치 무조건 반영 (L87~90) |
| **수정** | `step2_llm_extractor.py` | D_기타 대상 선별 시 step1 성공 청크 제외 (L374~378) |
| 재실행 | `step1` ~ `step7` | 코드 수정 없이 순차 재실행 |

---

## 7. 완료 기준

| 기준 | 지표 | 목표 |
|---|---|---|
| step1 커버리지 | 규칙 추출 엔티티 수 | **6,000개+** (현재 3,483) |
| step2 대상 축소 | LLM 처리 청크 수 | **200개 이하** (현재 937) |
| step3 병합 | 테이블 수치 보존율 | **100%** |
| step5 검증 | E1~E6 자동 검증 | **전체 PASS** |
| RAG 검증 | 핵심 품셈 검색 정확도 | 만족 |
