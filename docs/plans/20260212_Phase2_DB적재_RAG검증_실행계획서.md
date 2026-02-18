# Phase 2 DB 적재 & RAG 검증 실행 계획서

> **작성일**: 2026-02-12 (수) 20:30  
> **선행 작업**: `20260212_Phase2_파이프라인_재실행_결과보고서.md` (step1~step5 완료)  
> **목표**: 교정된 데이터를 Supabase DB에 적재하고, RAG 챗봇으로 정확도를 검증한다

---

## 0. 현재 상태 요약

| 구분                | 상태         | 비고                             |
| ------------------- | ------------ | -------------------------------- |
| step1 (규칙 추출)   | ✅ 완료       | 18,218 엔티티, 9,642 관계        |
| step2 (LLM 추출)    | ✅ 완료       | 1,702 청크, 65분 소요            |
| step3 (병합)        | ✅ 완료       | 33,298 엔티티, 31,593 관계       |
| step4 (정규화)      | ✅ 완료       | 17,091 엔티티 (중복 제거)        |
| step5 (검증)        | ✅ 4/6 PASS   | E5/E6 FAIL (LLM 품질, 기존 수준) |
| **step6 (DB 적재)** | ❌ **미실행** | 이 계획서의 대상                 |
| **step7 (임베딩)**  | ❌ **미실행** | 이 계획서의 대상                 |
| **RAG 챗봇 검증**   | ❌ **미실행** | 이 계획서의 대상                 |

---

## 1. 실행 단계 (총 5단계)

### Phase 1: Supabase DB 백업 & 초기화

#### 1-1. 기존 DB 데이터 백업

기존 Supabase 테이블의 데이터 건수와 상태를 확인하여 롤백 지점을 확보한다.

```sql
-- Supabase SQL Editor에서 실행
SELECT 'graph_entities' AS tbl, COUNT(*) AS cnt FROM graph_entities
UNION ALL
SELECT 'graph_relationships', COUNT(*) FROM graph_relationships
UNION ALL
SELECT 'graph_global_relationships', COUNT(*) FROM graph_global_relationships
UNION ALL
SELECT 'graph_chunks', COUNT(*) FROM graph_chunks;
```

**기록해야 할 데이터**:
- 각 테이블의 기존 행 수
- 마지막 업데이트 시각

#### 1-2. 기존 관계 테이블 초기화

step6은 관계를 `INSERT` (upsert가 아니라)로 적재하므로, 기존 관계가 중복 생성될 수 있다.
**반드시 관계 테이블을 먼저 비운다.**

```sql
-- ⚠️ 주의: 기존 관계 데이터 삭제
TRUNCATE TABLE graph_relationships;
TRUNCATE TABLE graph_global_relationships;
```

> **판단 기준**: 엔티티(`graph_entities`)와 청크(`graph_chunks`)는 `upsert`(ON CONFLICT ... UPDATE)로 적재되므로 TRUNCATE 불필요.

#### 1-3. 사전 검증 (Dry Run)

실제 적재 전에 데이터 변환만 수행하여 문제 여부를 확인한다.

```bash
py phase2_extraction/step6_supabase_loader.py --dry-run
```

**확인 항목**:
- entities 건수 (예상: ~17,091)
- extractions 건수 (예상: ~2,105)
- FK 검증에서 orphaned 관계 건수

---

### Phase 2: Step 6 — Supabase 데이터 적재

#### 2-1. 환경 변수 확인

`.env` 파일에 다음 값이 설정되어 있는지 확인한다.

| 변수                        | 용도                  | 주의                                |
| --------------------------- | --------------------- | ----------------------------------- |
| `SUPABASE_URL`              | Supabase 프로젝트 URL | `https://xxx.supabase.co`           |
| `SUPABASE_SERVICE_ROLE_KEY` | Service Role 키       | ⚠️ anon key가 아닌 service_role 필수 |
| `GEMINI_API_KEY`            | Gemini API 키         | step7에서 사용                      |

#### 2-2. 전체 적재 실행

```bash
py phase2_extraction/step6_supabase_loader.py
```

**내부 처리 순서**:

| 내부 Phase | 처리 내용      | 대상 테이블                  | 방식                | 예상 건수 |
| ---------- | -------------- | ---------------------------- | ------------------- | --------: |
| Phase 2    | 엔티티 적재    | `graph_entities`             | upsert (500건/배치) |   ~17,091 |
| Phase 3    | 관계 적재      | `graph_relationships`        | insert (500건/배치) |  ~27,000+ |
| Phase 4    | 전역 관계 적재 | `graph_global_relationships` | insert (500건/배치) |     ~500+ |
| Phase 5    | 청크 적재      | `graph_chunks`               | upsert (200건/배치) |    ~2,105 |

**예상 소요 시간**: 약 5~10분

**성공 확인 기준**:
- 각 Phase의 `success == total`
- errors = 0
- 로그 파일: `phase2_output/logs/step6_loader_YYYYMMDD_HHMMSS.log`

#### 2-3. 적재 결과 검증

```sql
-- 적재 후 건수 확인
SELECT 'graph_entities' AS tbl, COUNT(*) AS cnt FROM graph_entities
UNION ALL
SELECT 'graph_relationships', COUNT(*) FROM graph_relationships
UNION ALL
SELECT 'graph_global_relationships', COUNT(*) FROM graph_global_relationships
UNION ALL
SELECT 'graph_chunks', COUNT(*) FROM graph_chunks;
```

| 테이블                     | 예상 건수 |
| -------------------------- | --------: |
| graph_entities             |   ~17,091 |
| graph_relationships        |  ~27,000+ |
| graph_global_relationships |     ~500+ |
| graph_chunks               |    ~2,105 |

#### 2-4. 단계별 실행 (문제 발생 시)

특정 Phase에서 에러가 발생하면 개별 실행 가능:

```bash
py phase2_extraction/step6_supabase_loader.py --phase 2   # 엔티티만
py phase2_extraction/step6_supabase_loader.py --phase 3   # 관계만
py phase2_extraction/step6_supabase_loader.py --phase 4   # 전역 관계만
py phase2_extraction/step6_supabase_loader.py --phase 5   # 청크만
```

---

### Phase 3: Step 7 — 임베딩 생성

#### 3-1. 임베딩 Dry Run

```bash
py phase2_extraction/step7_embedding_generator.py --dry-run
```

**확인 항목**:
- `embedding IS NULL`인 엔티티/청크 건수
- 임베딩 텍스트 샘플 (처음 10건) 내용 확인

#### 3-2. 임베딩 생성 실행

```bash
py phase2_extraction/step7_embedding_generator.py
```

**내부 처리 순서**:

| Phase   | 대상           | 모델                 | 차원 | 배치    | 예상 건수 |
| ------- | -------------- | -------------------- | ---- | ------- | --------: |
| Phase 1 | graph_entities | gemini-embedding-001 | 768  | 100건씩 |   ~17,091 |
| Phase 2 | graph_chunks   | gemini-embedding-001 | 768  | 100건씩 |    ~2,105 |

**핵심 기능**:
- Keyset 페이징: `embedding IS NULL`인 행만 처리 → 이미 임베딩된 행은 스킵
- Exponential Backoff: 429/500/503 에러 시 자동 재시도 (최대 5회)
- RPC bulk_update_embeddings: 100건씩 묶어 서버사이드 업데이트 (속도 100x)
- NaN/Inf 벡터 검증 후 업데이트

**예상 소요 시간**: 
- 엔티티: ~17,091건 / 100건 배치 = ~171 API 호출 → 약 10~15분
- 청크: ~2,105건 / 100건 배치 = ~22 API 호출 → 약 2~3분
- **총 약 15~20분**

**예상 비용**: Gemini embedding-001 API (19,196건 × ~$0.00001/건 ≈ **~$0.20**)

#### 3-3. 임베딩 결과 검증

```sql
-- 임베딩 NULL 잔존 확인
SELECT 'entities_null' AS chk, COUNT(*) FROM graph_entities WHERE embedding IS NULL
UNION ALL
SELECT 'entities_ok', COUNT(*) FROM graph_entities WHERE embedding IS NOT NULL
UNION ALL
SELECT 'chunks_null', COUNT(*) FROM graph_chunks WHERE embedding IS NULL
UNION ALL
SELECT 'chunks_ok', COUNT(*) FROM graph_chunks WHERE embedding IS NOT NULL;
```

**성공 기준**: `*_null = 0`

#### 3-4. 대상별 개별 실행 (필요 시)

```bash
py phase2_extraction/step7_embedding_generator.py --target entities  # 엔티티만
py phase2_extraction/step7_embedding_generator.py --target chunks    # 청크만
```

> **재실행 안전성**: step7은 `embedding IS NULL`인 행만 처리하므로, 재실행해도 이미 임베딩된 행은 스킵. 에러 발생 시 재실행하면 잔여 건만 처리.

---

### Phase 4: RAG 챗봇 검증

#### 4-1. 핵심 테스트 케이스 (7건)

이 테스트는 이번 교정의 핵심 목표인 **매트릭스 테이블(13-2-3 강관용접) 수치 정확도**를 검증한다.

| #        | 질문                         | 기대 답변                 | 검증 포인트                         |
| -------- | ---------------------------- | ------------------------- | ----------------------------------- |
| **TC-1** | "강관용접 200mm SCH 40 품셈" | 플랜트 용접공 **0.287인** | 수치 정확성                         |
| **TC-2** | "강관용접 200mm SCH 20 품셈" | **용접공** 0.287인        | 직종 구분 (용접공 vs 플랜트 용접공) |
| **TC-3** | "강관용접 200mm SCH 80 품셈" | 플랜트 용접공 **0.362인** | SCH별 수치 차이                     |
| **TC-4** | "강관용접 전체 규격"         | 17구경 × 9SCH 전체 데이터 | 대량 데이터 누락 없음               |
| **TC-5** | "강관용접 φ15 SCH 80 품셈"   | 플랜트 용접공 **0.075인** | 소구경 검증                         |
| **TC-6** | "강관용접 φ350 SCH 20 품셈"  | **용접공** 0.442인        | 대구경 + 용접공 직종                |
| **TC-7** | "강관용접 φ15 SCH 20 품셈"   | **데이터 없음** (빈 셀)   | 빈 값 처리 검증                     |

#### 4-2. 검증 방법

1. **챗봇 UI**: `https://pumsem-chat.pages.dev` 접속 후 직접 질문
2. **Edge Function 직접 호출** (curl/Postman):
   ```bash
   curl -X POST https://[PROJECT_ID].supabase.co/functions/v1/rag-chat \
     -H "Authorization: Bearer [ANON_KEY]" \
     -H "Content-Type: application/json" \
     -d '{"question": "강관용접 200mm SCH 40 품셈"}'
   ```

#### 4-3. 판정 기준

| 등급          | 조건                                                    | 조치                                      |
| ------------- | ------------------------------------------------------- | ----------------------------------------- |
| ✅ **PASS**    | TC-1~3, TC-5~6 수치 100% 일치 + TC-7 "데이터 없음" 처리 | 완료                                      |
| ⚠️ **PARTIAL** | TC-1~3 수치 일치, TC-4~7 일부 미흡                      | Edge Function 디버깅                      |
| ❌ **FAIL**    | TC-1~3 중 수치 불일치                                   | step3 병합 로직 또는 step6 변환 로직 점검 |

#### 4-4. 디버깅 도구 (FAIL 시)

```sql
-- 특정 엔티티 조회
SELECT id, name, type, properties
FROM graph_entities
WHERE name LIKE '%강관용접%' AND name LIKE '%200%' AND name LIKE '%SCH 40%';

-- 해당 엔티티의 관계 조회
SELECT r.relation, r.properties,
       s.name AS source_name, t.name AS target_name
FROM graph_relationships r
JOIN graph_entities s ON r.source_id = s.id
JOIN graph_entities t ON r.target_id = t.id
WHERE s.name LIKE '%강관용접%200%SCH 40%'
   OR t.name LIKE '%강관용접%200%SCH 40%';
```

---

### Phase 5: 추가 검증 & 보강 (선택사항)

#### 5-1. 기타 섹션 교차 검증

강관용접 외에 다른 섹션에서도 수치 정확도를 확인한다.

| #   | 질문 예시                | 섹션         |
| --- | ------------------------ | ------------ |
| 1   | "콘크리트 타설 품셈 1m³" | 6-3 콘크리트 |
| 2   | "철근 가공 조립 D13"     | 6-4 철근     |
| 3   | "거푸집 합판 보통"       | 6-5 거푸집   |
| 4   | "터널 숏크리트 품셈"     | 10-x 터널    |

#### 5-2. E5/E6 품질 개선 (장기 과제)

현재 E5(LLM 감사) 0.402, E6(할루시네이션) 45%의 FAIL은 LLM 추출 품질 자체의 한계.

**개선 방안** (다음 세션에서 검토):

| 방안             | 설명                                      | 예상 효과       | 난이도 |
| ---------------- | ----------------------------------------- | --------------- | ------ |
| 프롬프트 개선    | Few-shot 예시 추가, Chain-of-Thought 강화 | E5 +0.1~0.2     | 중     |
| 2-pass 추출      | 1차 추출 후 검증 LLM 호출                 | E6 -10~15%      | 높     |
| 수동 검수        | 의심 29건 수동 확인 + 수정                | E6 즉시 해결    | 낮     |
| 테이블 추출 확대 | step1의 D_기타 규칙 커버리지 확대         | step2 대상 감소 | 중     |

---

## 2. 실행 명령어 요약

```bash
# ── Phase 1: 사전 준비 ──
# 1-1. DB 현황 확인 (Supabase SQL Editor)
# 1-2. 관계 테이블 초기화 (Supabase SQL Editor)
# 1-3. Dry Run
py phase2_extraction/step6_supabase_loader.py --dry-run

# ── Phase 2: DB 적재 ──
py phase2_extraction/step6_supabase_loader.py

# ── Phase 3: 임베딩 생성 ──
# 3-1. Dry Run
py phase2_extraction/step7_embedding_generator.py --dry-run
# 3-2. 실행
py phase2_extraction/step7_embedding_generator.py

# ── Phase 4: RAG 검증 ──
# 챗봇 UI 또는 curl로 TC-1~7 실행
```

---

## 3. 예상 소요 시간 & 비용

| 단계                   | 예상 시간 | 예상 비용  | 비고                                    |
| ---------------------- | --------- | ---------- | --------------------------------------- |
| Phase 1 (사전 준비)    | ~5분      | -          | SQL 실행 + dry-run                      |
| Phase 2 (step6 적재)   | ~10분     | -          | Supabase API rate limit 방지 sleep 포함 |
| Phase 3 (step7 임베딩) | ~20분     | ~$0.20     | Gemini embedding-001                    |
| Phase 4 (RAG 검증)     | ~15분     | ~$0.01     | 챗봇 질의 7건                           |
| **합계**               | **~50분** | **~$0.21** |                                         |

---

## 4. 롤백 계획

### 4-1. step6 적재 실패 시

```sql
-- 엔티티는 upsert이므로 이전 상태로 자동 보존
-- 관계만 초기화 후 재실행
TRUNCATE TABLE graph_relationships;
TRUNCATE TABLE graph_global_relationships;
```

```bash
py phase2_extraction/step6_supabase_loader.py --phase 3
py phase2_extraction/step6_supabase_loader.py --phase 4
```

### 4-2. step7 임베딩 실패 시

step7은 `embedding IS NULL` 조건으로만 처리하므로, 에러 발생 시 **재실행**하면 잔여 건만 처리됨. 별도 롤백 불필요.

```bash
py phase2_extraction/step7_embedding_generator.py  # 재실행으로 자동 복구
```

### 4-3. 전체 롤백 (최악의 경우)

백업 JSON 파일에서 이전 데이터로 복원:

```bash
# 이전 데이터 복원
cp phase2_output/backup_20260212_1744/normalized_entities.json phase2_output/normalized_entities.json
py phase2_extraction/step6_supabase_loader.py
py phase2_extraction/step7_embedding_generator.py
```

---

## 5. 체크리스트

### Phase 1: 사전 준비
- [ ] `.env` 파일 3개 키 확인 (SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GEMINI_API_KEY)
- [ ] 기존 DB 건수 기록
- [ ] `TRUNCATE graph_relationships` + `graph_global_relationships` 실행
- [ ] `step6 --dry-run` 성공 확인

### Phase 2: DB 적재
- [ ] `step6_supabase_loader.py` 실행
- [ ] Phase 2~5 모두 success == total 확인
- [ ] errors = 0 확인
- [ ] 적재 후 DB 건수 확인

### Phase 3: 임베딩 생성
- [ ] `step7 --dry-run` 사전 확인
- [ ] `step7_embedding_generator.py` 실행
- [ ] `embedding IS NULL` 잔존 = 0 확인

### Phase 4: RAG 검증
- [ ] TC-1: "강관용접 200mm SCH 40 품셈" → 플랜트 용접공 0.287인
- [ ] TC-2: "강관용접 200mm SCH 20 품셈" → **용접공** 0.287인
- [ ] TC-3: "강관용접 200mm SCH 80 품셈" → 플랜트 용접공 0.362인
- [ ] TC-4: "강관용접 전체 규격" → 17구경 × 9SCH 전체 데이터
- [ ] TC-5: "강관용접 φ15 SCH 80 품셈" → 플랜트 용접공 0.075인
- [ ] TC-6: "강관용접 φ350 SCH 20 품셈" → 용접공 0.442인
- [ ] TC-7: "강관용접 φ15 SCH 20 품셈" → 데이터 없음

### 최종 완료
- [ ] 검증 결과 기록 (PASS/PARTIAL/FAIL)
- [ ] 결과 보고서 업데이트
- [ ] 임시 스크립트 정리 (`_backup_phase2.py`, `_check_step1.py`, `_check_step3.py`)
