# 📋 Phase 2 RAG 디버깅 세션 종합보고서 (LATEST)

> **작성일**: 2026-02-13 02:33 KST  
> **상태**: 🟡 진행 중 (키워드 폴백 성공, LLM 컨텍스트 최적화 미완)  
> **이전 보고서**: `20260212_Phase2_세션종합보고서.md`

---

## 1. 세션 목표 및 달성 현황

| #   | 목표                                                     | 상태   | 비고                              |
| --- | -------------------------------------------------------- | ------ | --------------------------------- |
| 1   | φ 정규화 로직 완성 (`normalize_name` + `normalize_spec`) | ✅ 완료 | step4_normalizer.py 수정 완료     |
| 2   | 파이프라인 재실행 (step4→step6→step7)                    | ✅ 완료 | 정상 로드, 임베딩 0건 (ID 보존)   |
| 3   | DB 데이터 정합성 검증                                    | ✅ 완료 | `강관용접(200, SCH 40)` 정상 존재 |
| 4   | 벡터 검색 정확도 개선 — 키워드 폴백                      | ✅ 완료 | Edge Function v13 배포            |
| 5   | RAG 챗봇 테스트 케이스 PASS                              | 🟡 일부 | 검색은 성공, LLM 답변 품질 미달   |
| 6   | LLM 컨텍스트 과부하 해결                                 | ❌ 미완 | 다음 세션에서 처리                |

---

## 2. 완료된 작업 상세

### 2-1. φ 정규화 (Phase A) — ✅ 완료

**문제**: step1은 `강관용접(200, SCH 40)`, step2 LLM은 `강관용접(φ200, SCH 40)` 으로 추출 → 동일 엔티티 2벌 분리

**수정 파일**: `phase2_extraction/step4_normalizer.py`

```python
# normalize_name() — 라인 89~95
name = re.sub(r'[φΦø∅ɸ]\s*(?=\d)', '', name)

# normalize_spec() — 라인 123~133
spec = re.sub(r'[φΦø∅ɸ]\s*(?=\d)', '', spec)
```

**검증 결과** (`_check_phi.py`):
- φ 제거 전: `강관용접(200, SCH 40)` + `강관용접(φ200, SCH 40)` = 2개
- φ 제거 후: `강관용접(200, SCH 40)` = 1개로 통합 ✅

---

### 2-2. 키워드 폴백 검색 (Phase B) — ✅ 완료

**문제**: 벡터 검색이 `강관용접(250, SCH 140)`을 Top 1로 반환 (sim=0.889), 정작 `강관용접(200, SCH 40)`은 Top 5에 없음

**원인**: 임베딩 모델(gemini-embedding-001)이 "200mm"와 "(200,"의 의미적 연결을 못 함

**해결**: Edge Function `searchEntities`에 키워드 폴백 로직 추가

```
[흐름]
1. 질문에서 규격 숫자 추출: "200", "SCH 40"
2. 벡터 Top 5에 해당 숫자 포함 엔티티 있는지 확인
3. 없으면 → ILIKE 폴백: %강관용접%200%SCH 40%
4. 폴백 결과를 Top 1에 삽입 (similarity=1.0)
```

**수정 함수 3개**:
- `searchEntities(embedding, question)` — question 파라미터 추가
- `extractSpecNumbers(question)` — 질문에서 구경/SCH 추출
- `keywordFallbackSearch(question, specNumbers)` — ILIKE 폴백

**배포**: Edge Function `rag-chat` v13 → Supabase 배포 완료

**검증 결과**:
```
Before (v11): Top 1 = 강관용접(250, SCH 140) sim=0.889 ❌
After  (v13): Top 1 = 강관용접(200, SCH 40)  sim=1.000 ✅
```

---

### 2-3. DB 데이터 검증 — ✅ 정상

```sql
-- W-0788 강관용접(200, SCH 40) 의 관계
REQUIRES_LABOR: 플랜트용접공 0.287인, 0.294인
REQUIRES_LABOR: 특별인부 0.172인
USES_MATERIAL:  용접봉(kg) 0.9
BELONGS_TO:     강관용접 (Section)

-- RPC get_related_resources('W-0788') → 5개 관계 정상 반환
```

---

## 3. 남은 이슈 (다음 세션)

### 3-1. 🔴 LLM 컨텍스트 과부하 (Critical)

**현상**: 키워드 폴백으로 `강관용접(200, SCH 40)` (W-0788)을 Top 1에 올렸으나, LLM이 "직접적인 품셈 정보를 찾을 수 없습니다"라고 답변

**원인 분석**:
1. `expandGraph`에서 `expandSectionWorkTypes`가 section 13-2-3의 **모든 WorkType** (15개) 관계를 확장
2. 결과: **395개 관계** → LLM 입력 9,275 토큰
3. W-0788의 직접 관계 5개가 395개 속에 묻혀 LLM이 식별 실패

**해결 방안** (우선순위순):

#### A안. expandGraph 조건부 스킵 (권장)
```
조건: similarity === 1.0 (키워드 정확 매칭) 엔티티
처리: expandSectionWorkTypes 호출 생략
결과: 5개 직접 관계만 → LLM이 정확 답변 가능
```

#### B안. buildContext 필터링
```
조건: 질문의 규격 키워드 포함 work_type_name만 우선 출력
```

#### C안. LLM 프롬프트 강화
```
추가 지시: "질문에서 명시된 규격(200, SCH 40)의 데이터를 최우선으로 답변하라"
```

**권장**: A안 + C안 동시 적용

---

### 3-2. 🟡 테스트 케이스 전체 PASS

현재 `_test_rag.py` 5개 TC 중 0개 PASS. 키워드 폴백만으로는 부족하며, LLM 컨텍스트 최적화(3-1) 해결 후 재테스트 필요.

---

## 4. 수정된 파일 목록

| 파일                                    | 변경 내용                                  | 상태       |
| --------------------------------------- | ------------------------------------------ | ---------- |
| `phase2_extraction/step4_normalizer.py` | φ 정규화 (normalize_name + normalize_spec) | ✅ 완료     |
| `supabase/functions/rag-chat/index.ts`  | 키워드 폴백 검색 추가, v13 배포            | ✅ 배포됨   |
| `_check_phi.py`                         | φ 정규화 검증 스크립트                     | ✅ 유틸리티 |
| `_test_tc1.py`                          | TC-1 단일 테스트 스크립트                  | ✅ 유틸리티 |
| `_db_verify.py`                         | DB 데이터 검증 스크립트                    | ✅ 유틸리티 |

---

## 5. Edge Function 배포 이력

| 버전    | 날짜           | 변경 내용                           |
| ------- | -------------- | ----------------------------------- |
| v10     | 2026-02-12     | 기존 벡터 검색 전용                 |
| v11     | 2026-02-12     | work_type_name 주입, Note 내용 보강 |
| v12     | 2026-02-13     | (빈 파일 배포 실수 — 즉시 교체)     |
| **v13** | **2026-02-13** | **키워드 폴백 검색 추가 (LATEST)**  |

---

## 6. 다음 세션 실행 계획

```
Phase C: LLM 컨텍스트 최적화 (예상 30분)
├── C-1. expandGraph에 skipSectionExpansion 플래그 추가
│    - similarity === 1.0 엔티티 → 직접 관계만 사용
│    - 나머지 벡터 매칭 엔티티 → 기존 section 확장 유지
├── C-2. SYSTEM_PROMPT에 규격 우선 지시 추가
│    - "질문에서 명시된 규격의 데이터를 최우선으로 답변"
├── C-3. Edge Function v14 배포
├── C-4. _test_rag.py 전체 테스트 실행
│    - 목표: 5/5 PASS
└── C-5. 최종 보고서 작성
```

---

## 7. 환경 정보

| 항목                | 값                               |
| ------------------- | -------------------------------- |
| Supabase Project ID | `bfomacoarwtqzjfxszdr`           |
| Edge Function       | `rag-chat` v13                   |
| 임베딩 모델         | `gemini-embedding-001` (768차원) |
| LLM 모델            | `gemini-2.0-flash`               |
| DB 엔티티 수        | 17,442                           |
| DB 관계 수          | 26,295                           |
| DB 청크 수          | 2,105                            |
| 일위대가 항목 수    | 6,992                            |
