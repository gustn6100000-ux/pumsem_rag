# Edge Function Phase 1 리팩토링 결과

> **일시**: 2026-02-16 | **대상**: `index.ts`, `clarify.ts`, `context.ts`

## 변경 요약

| Phase | 변경 내용                                            | 대상 파일                | 줄 변화                                  |
| ----- | ---------------------------------------------------- | ------------------------ | ---------------------------------------- |
| 1-1   | `makeAnswerResponse`/`makeClarifyResponse` 헬퍼 추출 | `index.ts`, `context.ts` | **-131줄** (index), **+105줄** (context) |
| 1-2   | `subSectionDrillDown()` 중복 제거                    | `clarify.ts`             | **-25줄**                                |

**총합**: 2564줄 → 2513줄 (**순 -51줄**)

---

## Phase 1-1: 응답 조립 헬퍼 추출

`handleChat` 내 **15곳**에 반복되던 응답 객체 조립 코드를 `context.ts`의 3개 함수로 통합:

| 함수                    | 역할                                       | 호출 횟수 |
| ----------------------- | ------------------------------------------ | --------- |
| `makeAnswerResponse()`  | "answer" 타입 응답 (token_usage 자동 계산) | 11곳      |
| `makeClarifyResponse()` | "clarify" 타입 응답 (옵션 + selector 포함) | 4곳       |
| `makeEmptySearchInfo()` | 빈 search_info (향후 사용 예비)            | 예비      |

### 효과

- 응답 구조 변경 시 **1곳만 수정** (기존 15곳)
- `index.ts` 1128줄 → 997줄 (**-131줄**)

---

## Phase 1-2: subSectionDrillDown 중복 제거

`clarify.ts` 내 Step 2(L496~545)와 케이스 A(L984~1034)에서 **~90줄 동일 로직** 반복 → 58줄 공통 함수 1개 + 2×5줄 호출로 교체.

### subSectionDrillDown() 시그니처

```typescript
function subSectionDrillDown(
    workTypes: any[],
    sectionPath: string,
    sectionId: string,
    sectionName: string,
    queryPrefix?: string
): ClarifyResult | null
```

- sub_section이 2개 이상이면 `ClarifyResult` 반환, 아니면 `null`
- `clarify.ts` 1170줄 → 1145줄 (**-25줄**)

---

## 검증 결과 (3/3 통과)

| 테스트           | 입력                  | 결과     | 세부                               |
| ---------------- | --------------------- | -------- | ---------------------------------- |
| greeting         | `"안녕"`              | ✅ 200 OK | type=answer, 380자                 |
| clarify          | `"강관용접 품셈"`     | ✅ 200 OK | type=clarify, 3 options, 5825ms    |
| entity 직접 조회 | `entity_id: "W-0788"` | ✅ 200 OK | type=answer, 1 source, 2248 tokens |

---

## 파일별 최종 줄 수

| 파일         | Before   | After    | 변화    |
| ------------ | -------- | -------- | ------- |
| `index.ts`   | 1128     | 997      | -131    |
| `clarify.ts` | 1170     | 1145     | -25     |
| `context.ts` | 266      | 371      | +105    |
| **합계**     | **2564** | **2513** | **-51** |
