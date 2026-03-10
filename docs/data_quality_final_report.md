# 📊 품셈 데이터 전수 검증 및 보완 최종 보고서
> **작성일**: 2026-03-10  
> **스크립트**: validate_chunks_v2.py  
> **데이터**: 466개 base_section_id / 43개 MD 파일 / graph_chunks 테이블

---

## 1. 최종 결과 요약

| 항목 | 교정 전 | **교정 후** | 변화 |
|---|---|---|---|
| ✅ PASS (≥95%) | 88건 | **217건** | **+129** |
| ℹ️ INFO (80~95%) | 180건 | **202건** | +22 |
| ⚠️ WARN (50~80%) | 67건 | **23건** | **-44** |
| ❌ FAIL (<50%) | 131건 | **24건** | **-107** |
| 🔀 교차혼입 | 1건 | 1건 | — |
| **전체 Coverage** | **86.6%** | **108.1%** | **+21.5%p** |
| **DB 총 행** | 12,204행 | **15,244행** | **+3,040행** |

> Coverage > 100%는 DB에 원본보다 더 많은 행이 존재함을 의미 (기존 #N 접미사 chunk의 데이터 + 보완된 데이터가 중복 포함)

---

## 2. 수행 작업

### 2-1. 전수 검증 v2 스크립트 개발
- section_id `#N` 접미사를 base_id로 정규화하여 **정확한 MD↔DB 매칭** 구현
- 이전 v1 검증(Coverage 99.5%)에서 오탐으로 놓쳤던 **실제 누락 1,892행** 발견
- 관련 파일: `pipeline/scripts/validate_chunks_v2.py`

### 2-2. 기존 chunk 테이블 보완 (137건)
- **문제**: DB에 chunk는 있지만 `tables` 배열에 원본 테이블이 누락
- **해결**: 원본 MD에서 해당 section의 HTML 테이블을 파싱하여 기존 chunk의 `tables` 배열에 직접 삽입
- **효과**: FAIL 131 → 60건, Coverage 86.6% → 104.4%
- 관련 파일: `pipeline/scripts/fix_missing_tables.py`

### 2-3. 새 chunk 생성 (38건)
- **문제**: DB에 해당 section_id의 chunk 자체가 존재하지 않음
- **해결**: 원본 MD에서 데이터를 추출하여 `C-NEW-0001` ~ `C-NEW-0039` 새 chunk 생성
- **효과**: FAIL 60 → 24건, Coverage 104.4% → 108.1%
- 관련 파일: `pipeline/scripts/create_missing_chunks.py`

### 2-4. 파이프라인 근본 코드 수정 (3개 함수)
V형/U형 교차혼입 방지를 위한 파이프라인 코드 수정:

| 함수 | 파일 | 수정 내용 |
|---|---|---|
| `make_entity_key` | step4_normalizer.py | sub_section을 키에 포함하여 V형/U형 병합 방지 |
| `_smart_inherit_sub_section` | step3_relation_builder.py | 같은 chunk 내에서만 sub_section 상속 |
| `pick_representative` | step4_normalizer.py | sub_section 있는 엔티티 우선 참조 |

---

## 3. FAIL 교정 전 원인 분석

| 원인 | 건수 | 설명 |
|---|---|---|
| **chunk에 tables 누락** | 137건 | chunk 존재하지만 tables 배열 비어 있거나 부족 |
| **chunk 자체 부재** | 39건 | DB에 해당 section_id chunk 없음 |
| **교차혼입** | 1건 | V형/U형 데이터 혼합 (13-2-4) |
| **진짜 누락** | ~24건 | 원본 MD 파서 수준의 구조 문제 |

---

## 4. 잔여 FAIL 24건

교정 후 남은 24건은 다음 패턴:
- 원본 MD에 테이블이 있지만, MD 파서의 `<!-- SECTION -->` 태그 경계가 모호하여 다른 section에 포함
- 교차표(7컬럼 이상) 파싱 시 컬럼 수 축소
- 1건: `13-8` 쓰레기소각 — 원본 자체에 테이블 없음

---

## 5. 검증 데이터 현황

### Coverage 분포 (466개 section)

```
✅ PASS (≥95%):    ████████████████████████████████████  217건 (46.6%)
ℹ️ INFO (80~95%):  ██████████████████████████████████    202건 (43.3%)
⚠️ WARN (50~80%):  ████                                   23건 ( 4.9%)
❌ FAIL (<50%):     ████                                   24건 ( 5.2%)
```

### DB 행 수 변화

```
교정 전:  ████████████████████████████░░░░  12,204행 (86.6%)
교정 후:  ████████████████████████████████  15,244행 (108.1%)
원본 MD:  ████████████████████████████████  14,096행 (100%)
```

---

## 6. 관련 파일 목록

| 파일 | 설명 |
|---|---|
| `pipeline/scripts/validate_chunks_v2.py` | 전수 검증 스크립트 (base_id 정규화) |
| `pipeline/scripts/fix_missing_tables.py` | 기존 chunk 테이블 보완 스크립트 |
| `pipeline/scripts/create_missing_chunks.py` | 새 chunk 생성 스크립트 |
| `pipeline/scripts/output/audit_v2_report.json` | 최종 검증 결과 JSON |
| `pipeline/scripts/output/fix_results.json` | 교정 결과 로그 |
| `pipeline/phase2_extraction/step4_normalizer.py` | 파이프라인 수정 (make_entity_key, pick_representative) |
| `pipeline/phase2_extraction/step3_relation_builder.py` | 파이프라인 수정 (_smart_inherit_sub_section) |

---

## 7. 향후 권장 작업

| 순위 | 작업 | 효과 |
|---|---|---|
| 1 | 잔여 FAIL 24건 수동 검토 | Coverage 최적화 |
| 2 | 중복 행 정리 (Coverage > 100% 원인) | 데이터 정합성 |
| 3 | validate_chunks_v2.py CI 통합 | 재인제스트 시 자동 검증 |
| 4 | section_id 정규화 로직 파이프라인 반영 | 재인제스트 시 #N 문제 방지 |
