# TIG 용접 데이터 개선 — Walkthrough

> 작성일: 2026-02-14 | 배포 버전: v60

## 1. 변경 요약

### Phase 1: RAW_TABLE 원문 폴백 — [graph.ts](file:///g:/My%20Drive/Antigravity/edge-function/graph.ts)

그래프에 `REQUIRES_LABOR` 관계도 없고 `properties`에 `quantity`도 없는 WorkType(예: TIG용접)에 대해 `unit_costs` 테이블에서 원문을 검색하여 `RAW_TABLE` 가상 관계로 추가.

```diff
+ // Phase 1: Labor 관계도 없고 properties에도 quantity 없는 WT → unit_costs 원문 폴백
+ if (!hasLaborRel && !(wtProps.quantity && wtProps.unit)) {
+     const { data: rawData } = await supabase
+         .from("unit_costs").select("content, name")
+         .ilike("content", `%${workType.name}%`).limit(1);
+     // → RAW_TABLE 관계로 allRelations에 추가
+ }
```

### Phase 2: 매트릭스 테이블 병합 — [context.ts](file:///g:/My%20Drive/Antigravity/edge-function/context.ts)

`work_type_name`에서 매트릭스 패턴(`강관용접(200, SCH 40)`)을 감지하여, 구경×SCH 통합 테이블로 렌더링.

| 개선 전                    | 개선 후                         |
| -------------------------- | ------------------------------- |
| 122개 개별 테이블 (각 1행) | 1개 매트릭스 테이블 (구경×직종) |
| 토큰 낭비 큼               | 토큰 효율 대폭 개선             |

---

## 2. 테스트 결과

### ✅ 강관용접 200mm SCH 40

| 항목               | 결과                                   |
| ------------------ | -------------------------------------- |
| Status             | 200 OK                                 |
| relations_expanded | **663**                                |
| 인력               | 플랜트용접공 0.294인, 특별인부 0.172인 |
| 장비               | 용접기 0.03                            |
| 주의사항           | 비파괴검사, Nozzle, Sloping 등 6개     |

### ⚠️ TIG용접 단독 검색

| 현상                          | 원인                                                 |
| ----------------------------- | ---------------------------------------------------- |
| 강판전기아크용접(13-2-4) 반환 | 벡터 임베딩이 "TIG" 키워드에 13-2-4를 우선 매칭      |
| W-0631 미출현                 | 엔티티명 "TIG(Tungsten Inert Gas)용접" — 유사도 0.15 |

**→ 후속작업(Phase 3)으로 키워드 fallback 보강 필요**

---

## 3. 수정 파일

| 파일                                                                     | 변경 내용                               |
| ------------------------------------------------------------------------ | --------------------------------------- |
| [graph.ts](file:///g:/My%20Drive/Antigravity/edge-function/graph.ts)     | unit_costs 원문 폴백(RAW_TABLE) 추가    |
| [context.ts](file:///g:/My%20Drive/Antigravity/edge-function/context.ts) | RAW_TABLE 출력 + 매트릭스 테이블 렌더링 |
