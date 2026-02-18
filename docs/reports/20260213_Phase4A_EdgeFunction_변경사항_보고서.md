# Phase 4A: Edge Function 코드 개선 — 변경사항 상세 보고서

> **작성일**: 2026-02-13  
> **대상 파일**: `rag-chat/index.ts`  
> **배포 버전**: v44 → v45  
> **배포 상태**: ✅ 완료 (2026-02-13 23:33 KST)

---

## 1. 변경 목적

Phase 4 데이터 정합성 분석에서 발견된 **검색 결과 중복 문제**와 **UX 혼란 요소**를 Edge Function 코드 레벨에서 즉시 수정.

### 해결 대상 문제

| #   | 문제                                        | 영향                       | 심각도   |
| --- | ------------------------------------------- | -------------------------- | -------- |
| 1   | 동일 공종의 미세 이름 차이로 중복 결과 반환 | 사용자 혼란, LLM 중복 답변 | 🔴 High   |
| 2   | WT ≤ 10일 때 "하위 절" 언급 → UX 혼란       | 잘못된 안내 메시지         | 🟡 Medium |
| 3   | `_debug` 필드가 프로덕션 응답에 노출        | 보안/성능 우려             | 🟡 Medium |

---

## 2. 변경 내용 상세

### 2.1 `deduplicateResults` 유틸리티 함수 추가

**위치**: L389~L403 (신규 삽입)

```typescript
// ─── E-2.5. WorkType 중복 제거 ───
// Why: 같은 표에서 V형/U형 등 소제목이 분리 추출되어
//      "인 력(인)" vs "인력(인)" 같은 미세 차이로 중복 엔티티 존재.
//      검색 결과에서 normalized_name 기준으로 가장 유사도 높은 것만 유지.
function deduplicateResults<T extends { name: string; similarity?: number }>(results: T[]): T[] {
    const seen = new Map<string, T>();
    for (const r of results) {
        const normKey = r.name.replace(/\s+/g, '').toLowerCase();
        const existing = seen.get(normKey);
        if (!existing || (r.similarity ?? 0) > (existing.similarity ?? 0)) {
            seen.set(normKey, r);
        }
    }
    return Array.from(seen.values());
}
```

**설계 결정**:
- **정규화 전략**: 공백 제거 + 소문자 변환으로 "인 력(인)" ↔ "인력(인)" 동일 취급
- **우선순위**: 동일 정규화 이름의 중복 시, `similarity` 점수가 높은 엔티티를 유지
- **제네릭 타입**: `{ name: string; similarity?: number }`를 만족하는 모든 타입에 적용 가능

---

### 2.2 `graphClarify` Step 2: WorkType 중복 제거 적용

**위치**: L1100~L1115

```typescript
// Phase 4A: 이름 정규화 기준 중복 제거
if (workTypes.length > 0) {
    const uniqueWTs = new Map<string, any>();
    for (const wt of workTypes) {
        const normKey = wt.name.replace(/\s+/g, '').toLowerCase();
        if (!uniqueWTs.has(normKey)) {
            uniqueWTs.set(normKey, wt);
        }
    }
    const beforeCount = workTypes.length;
    workTypes = Array.from(uniqueWTs.values());
    if (beforeCount !== workTypes.length) {
        console.log(`[graphClarify] Step 2: dedup ${beforeCount} → ${workTypes.length}개`);
    }
}
```

**적용 시점**: Step 2에서 `source_section`으로 하위 WorkType 조회 후, 옵션 생성 전  
**효과**: "인 력(인)" vs "인력(인)" 같은 V형/U형 분리 추출 중복 제거

---

### 2.3 `graphClarify` 메시지 분기 개선

**위치**: L1152~L1167

**변경 전**:
```typescript
// WT > 0 && childSections > 0 → 항상 "N개 하위 절(M개 작업)" 언급
clarifyMessage = `**${sectionPath}** 품셈에는 ${childSections.length}개 하위 절이 있습니다.`;
```

**변경 후**:
```typescript
if (workTypes.length > 0 && childSections.length > 0) {
    if (workTypes.length <= 10) {
        // WorkType 개별 표시 모드 → 하위 절 언급 제거
        clarifyMessage = `**${sectionPath}** 품셈은 ${workTypes.length}개 작업으로 분류되어 있습니다.\n어떤 작업의 품셈을 찾으시나요?`;
    } else {
        // 하위 절 단위로 표시 모드 → 절+작업 수 안내
        clarifyMessage = `**${sectionPath}** 품셈에는 ${childSections.length}개 분류(총 ${workTypes.length}개 작업)가 있습니다.\n분류를 선택해 주세요.`;
    }
}
```

**설계 근거**:
- WT ≤ 10: 개별 WorkType을 칩으로 표시 → "하위 절"이라는 표현은 실체와 불일치
- WT > 10: 하위 절 단위로 그룹화 → "N개 분류(총 M개 작업)" 형태가 적합
- "하위 절"이라는 기술적 용어 대신 "분류", "작업"이라는 사용자 친화적 용어 사용

---

### 2.4 `targetSearch` 반환값 중복 제거

**위치**: L1607, L1614

**변경 전**:
```typescript
// chunk fallback 결과
return [...chunkResults, ...vectorResults.filter(e => !chunkIds.has(e.id))];
// 벡터 결과
return vectorResults;
```

**변경 후**:
```typescript
// chunk fallback 결과
return deduplicateResults([
    ...chunkResults,
    ...vectorResults.filter(e => !chunkIds.has(e.id)),
]);
// 벡터 결과
return deduplicateResults(vectorResults);
```

**효과**: 최종 검색 결과가 어디에서 왔든(벡터/chunk/ILIKE) 정규화 이름 기준 중복 제거 보장

---

### 2.5 `_debug` 필드 전면 제거

**영향 범위**: 4개 위치

| 위치                                                                     | 변경 내용                                      |
| ------------------------------------------------------------------------ | ---------------------------------------------- |
| `ClarifyResult` 인터페이스 (L1005~L1008)                                 | `_debug?: any` 필드 삭제                       |
| `graphClarify` 함수 내 반환문 (L1169, L1376, L1402, L1443, L1474, L1490) | 모든 `_debug` 속성 삭제                        |
| `graphClarify` 함수 내 수집 로직                                         | `debugInfo` 객체 및 관련 데이터 수집 코드 제거 |
| `handleChat` clarify_needed 분기 (L2122~L2136)                           | 응답 객체에서 `_debug` 제거                    |

**설계 근거**:
- 프로덕션 응답에 내부 디버그 정보(SQL 쿼리, 중간 결과 등) 노출은 보안 위험
- 디버그 데이터 수집 자체가 불필요한 연산 비용 발생
- 향후 디버깅 시 `console.log`와 Supabase Edge Function 로그로 대체

---

## 3. 영향 분석 (Impact Check)

### 3.1 변경으로 인한 부작용 없음

| 관점                | 영향          | 상세                                                      |
| ------------------- | ------------- | --------------------------------------------------------- |
| **API 응답 스키마** | ✅ 호환        | `_debug` 필드는 프론트엔드에서 미사용 (optional 필드였음) |
| **검색 결과 품질**  | ✅ 개선        | 중복 제거로 LLM 입력 컨텍스트 정확도 향상                 |
| **성능**            | ✅ 미미한 개선 | `_debug` 수집 로직 제거로 미세 성능 향상                  |
| **프론트엔드**      | ✅ 무영향      | `index.html`은 `_debug` 필드를 참조하지 않음              |

### 3.2 미해결 사항 (Phase 4B/4C에서 처리)

| 항목                 | 설명                                    | 담당 Phase          |
| -------------------- | --------------------------------------- | ------------------- |
| DB 레벨 중복 엔티티  | W-0996 vs W-0997 같은 동일 엔티티 병합  | Phase 4B (SQL 패치) |
| SCH ↔ 두께 규격 오류 | 강판에 "SCH"가 적용된 124개 WorkType    | Phase 4B (SQL 패치) |
| 빈 chunk 텍스트      | 908/2105건(43.1%) 빈 텍스트             | Phase 4B (SQL 보강) |
| 파이프라인 근본 수정 | `step1_table_extractor.py` V형/U형 분리 | Phase 4C (Python)   |

---

## 4. 코드 위치 맵

```
index.ts (2,432 lines, 107KB)
│
├── L1~L125      [A] 설정 & 유틸 (CORS, Rate Limit, 타입 정의)
├── L126~L163    [B] 임베딩 생성 (Gemini)
├── L165~L615    [C] 검색 파이프라인
│   ├── L177~L236    C-1. 벡터 검색 + 키워드 폴백
│   ├── L238~L291    규격 숫자 추출 + ILIKE 폴백
│   ├── L293~L387    Layer 4: chunk 본문 텍스트 검색
│   ├── L389~L403    ★ E-2.5. deduplicateResults (Phase 4A 신규)
│   ├── L405~L578    C-2. 그래프 확장 (1-hop + 계층)
│   ├── L580~L606    C-3. 일위대가 검색
│   └── L608~L650    C-4. 원문 청크 보강
├── L652~L810    [D] 컨텍스트 조합 (buildContext)
├── L812~L1494   [E] 의도 감지 + 명확화
│   ├── L840~L997    E-1. DeepSeek 의도 분석
│   ├── L999~L1494   ★ E-2. graphClarify (Phase 4A 수정: 중복제거+메시지)
│   └── L1496~L1615  ★ E-3. targetSearch (Phase 4A 수정: 중복제거)
├── L1617~L1783  [F] LLM 답변 생성 (DeepSeek → Gemini 폴백)
├── L1785~L2326  [G] 메인 핸들러 (handleChat)
│   └── L2117~L2137  ★ clarify_needed 분기 (Phase 4A: _debug 제거)
└── L2328~L2432  서버 진입점 (Deno.serve)

★ = Phase 4A에서 수정된 위치
```

---

## 5. 테스트 검증 방법

### 5.1 중복 제거 검증

```bash
# 강판 전기아크용접 검색 → 이전에 W-0996, W-0997 중복 반환되던 케이스
curl -X POST https://bfomacoarwtqzjfxszdr.supabase.co/functions/v1/rag-chat \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_RAG_API_KEY" \
  -d '{"question": "강판 전기아크용접 10mm 품셈"}'
```

**기대 결과**: 동일 정규화 이름의 엔티티가 1건만 반환

### 5.2 메시지 개선 검증

```bash
# 강관용접 검색 → 하위 절 있지만 WT ≤ 10인 케이스
curl -X POST https://bfomacoarwtqzjfxszdr.supabase.co/functions/v1/rag-chat \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_RAG_API_KEY" \
  -d '{"question": "강관용접"}'
```

**기대 결과**: "N개 하위 절" 대신 "N개 작업으로 분류" 메시지

### 5.3 _debug 제거 검증

```bash
# 모호한 질문 → clarify 응답
curl -X POST https://bfomacoarwtqzjfxszdr.supabase.co/functions/v1/rag-chat \
  -H "Content-Type: application/json" \
  -H "x-api-key: YOUR_RAG_API_KEY" \
  -d '{"question": "잡철물"}'
```

**기대 결과**: 응답 JSON에 `_debug` 필드 없음

---

## 6. 롤백 방법

Phase 4A 변경 전 버전(v44)으로 롤백 필요 시:

1. Git 또는 백업에서 이전 `index.ts` 복원
2. 동일 배포 절차로 재배포

```powershell
# 백업에서 복원 (백업이 있는 경우)
Copy-Item "G:\내 드라이브\...\index.ts.bak" `
  "C:\Users\lhs\sb_deploy\supabase\functions\rag-chat\index.ts" -Force

Set-Location "C:\Users\lhs\sb_deploy"
npx supabase functions deploy rag-chat --project-ref bfomacoarwtqzjfxszdr --no-verify-jwt
```

---

## 7. 다음 단계 (Phase 4B/4C)

| Phase    | 내용                                                    | 우선순위 | 예상 시간 |
| -------- | ------------------------------------------------------- | -------- | --------- |
| **4B**   | SQL 데이터 패치 (V형/U형 구분, SCH→두께, 빈 chunk 보강) | 🔴 High   | 30분      |
| **4C**   | Python 파이프라인 개선 (`step1_table_extractor.py`)     | 🟡 Medium | 2~3시간   |
| **검증** | 전수 데이터 검증 스크립트 (`step6_data_validator.py`)   | 🟡 Medium | 1시간     |
