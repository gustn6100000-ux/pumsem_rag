# Phase -1 Section 확장 비활성화 — 복수선택 데이터 정확도 개선

**일시**: 2026-02-15  
**배포**: Edge Function v81 (graph.ts + index.ts)  
**상태**: 배포 완료, 검증 완료

---

## 문제

사용자가 셀렉터에서 **강관용접(50, SCH 20), (65, SCH 20), (80, SCH 20)** 3개를 선택했으나:

- ❌ **선택된 3개 규격 미표시**
- ❌ 15/SCH 40, 15/SCH 80, 90/SCH 20, 100/SCH 20 등 **무관한 데이터 표시**
- ❌ 381개 관계가 context에 포함 → LLM 혼동

## 구조적 원인 (Root Cause)

```mermaid
graph LR
    A[사용자: 50,65,80 SCH20 선택] --> B[handleChat Phase -1]
    B --> C[expandGraph per entity]
    C --> D["expandSectionWorkTypes('13-2-3')"]
    D --> E[동일 section 전체 WorkType 30개 조회]
    E --> F[각 WorkType의 관계 병렬 조회]
    F --> G[381개 관계 → context 범람]
    G --> H[LLM: 모든 데이터 출력 시도]
```

`expandGraph` 함수의 `expandSectionWorkTypes`가 동일 section의 **모든 WorkType** (15~600mm, SCH 20~160) 관계를 확장하여 context에 포함시킴.

## 수정 내용

### 1. `graph.ts` — `expandGraph`에 `skipSectionExpansion` 플래그 추가

```diff
 export async function expandGraph(
     entityId: string,
-    entityType: string
+    entityType: string,
+    skipSectionExpansion: boolean = false
 ): Promise<RelatedResource[]> {
```

Section 타입과 WorkType/Note/Standard 타입의 `expandSectionWorkTypes` 호출을 조건부 처리:

```diff
-    if (["WorkType", "Note", "Standard"].includes(entityType)) {
+    if (!skipSectionExpansion && ["WorkType", "Note", "Standard"].includes(entityType)) {
```

### 2. `index.ts` — Phase -1에서 `skipSectionExpansion=true` 전달

```diff
-const relationsPromises = entities.map((e) => expandGraph(e.id, e.type));
+const relationsPromises = entities.map((e) => expandGraph(e.id, e.type, true));
```

**다른 Phase(자연어 검색 등)에서는 기존 동작 유지** (`skipSectionExpansion` 기본값 = false).

---

## 검증 결과

### 정량 비교 (동일 query: entity_id=W-0846,W-0868,W-0872)

| 지표        | v80 (이전)  | v81 (수정)  | 변화        |
| ----------- | ----------- | ----------- | ----------- |
| 관계 수     | 381개       | **9개**     | **▼ 97.6%** |
| 응답 크기   | 2,858 bytes | 2,217 bytes | ▼ 22%       |
| answer 길이 | 1,159자     | 691자       | ▼ 40%       |

### 정성 비교

**v80 (이전)**:
```
- 15 SCH40, 15 SCH80, 15 SCH160, 20 SCH160... 무관한 데이터제
- 50, 65, 80 SCH20 → 미포함 또는 혼재
- "해당 제품 못찾음" 경고
```

**v81 (수정)**:
```
| 구경(mm) | SCH | 직종         | 수량(인) |
| 50       | 20  | 플랜트용접공 | 0.083    | ✅ 정확
| 50       | 20  | 특별인부     | 0.049    | ✅
| 65       | 20  | 플랜트용접공 | 0.102    | ✅
| 65       | 20  | 특별인부     | 0.060    | ✅
| 80       | 20  | 플랜트용접공 | 0.110    | ✅
| 80       | 20  | 특별인부     | 0.065    | ✅
```
- 불필요한 데이터 **완전 제거**
- 선택된 3개 규격만 정확히 표시

---

## 영향 범위

| 경로                   | Phase -1 (entity_id 직접) | 자연어 검색 | section_id 검색 |
| ---------------------- | ------------------------- | ----------- | --------------- |
| expandSectionWorkTypes | ❌ 비활성화                | ✅ 기존 유지 | ✅ 기존 유지     |
| context 크기           | 최소화                    | 변경 없음   | 변경 없음       |
