# Phase 1 결과보고서 — sub_section DB 마이그레이션

> 실행일: 2026-02-15  
> 대상: `graph_entities` WorkType의 `properties.sub_section` 속성 추가

---

## 실행 요약

| 항목        | 값                  |
| ----------- | ------------------- |
| 대상 섹션   | 7개 (critical 등급) |
| 총 WorkType | **638개**           |
| 분류 완료   | 638개 (100%)        |
| 미분류      | **0건** ✅           |

---

## 섹션별 분류 결과

### 13-2-3 강관용접 (122개)

| sub_section     | 건수 | 분류 기준                |
| --------------- | ---- | ------------------------ |
| 1. 전기아크용접 | 121  | `name NOT ILIKE '%TIG%'` |
| 2. TIG용접      | 1    | `name ILIKE '%TIG%'`     |

### 13-2-4 강판 전기아크용접 (124개)

| sub_section             | 건수 | 분류 기준                                         |
| ----------------------- | ---- | ------------------------------------------------- |
| 1. 전기아크용접(V형)    | 33   | `인 력(인)` + `용접봉사용량` + 소요전력(두께3~15) |
| 2. 전기아크용접(U형)    | 15   | chunk M,N 단독 + `인력(인)` (공백없음)            |
| 2/3. U형·H형(공통)      | 48   | D+G/E+H/F+I 복수 chunk (LLM 합병 데이터)          |
| 4. 전기아크용접(X형)    | 22   | chunk J,K 단독 + `전력소비량`                     |
| 5. 전기아크용접(Fillet) | 6    | chunk L 단독 (V형 미포함)                         |

> **특이사항**: U형·H형은 동일 두께·항목의 테이블이라 LLM 추출 시 합병됨 → `2/3. U형·H형(공통)`으로 일괄 처리

### 13-1-5 Flange 취부 (180개)

| sub_section                 | 건수 | 분류 기준                       |
| --------------------------- | ---- | ------------------------------- |
| 1. Screwed Type             | 30   | `Steel 및 주철` 키워드          |
| 2. Seal Welded Screwed Type | 131  | 단독 압력등급 (10.5~105 kg/cm²) |
| 3. Slip-on Type             | 19   | `176` 또는 `21~27` 키워드       |

### 13-1-3 밸브 취부 (52개)

| sub_section                 | 건수 | 분류 기준            |
| --------------------------- | ---- | -------------------- |
| 1. Screwed Type             | 51   | 기본 패턴            |
| 2. Welder-Back Screwed Type | 1    | `Welder-Back` 키워드 |

### 13-1-4 Fitting 취부 (34개)

| sub_section          | 건수 | 분류 기준     |
| -------------------- | ---- | ------------- |
| 1. Screwed Type      | 25   | `SCH` 키워드  |
| 2. Butt Welding Type | 9    | `kg/cm2` 패턴 |

### 13-2-6 응력제거 (100개)

| sub_section                 | 건수 | 분류 기준                 |
| --------------------------- | ---- | ------------------------- |
| 1. Induction Heating Device | 98   | 응력제거(재질, 두께) 패턴 |
| 2. Gas Heating              | 1    | `Gas Heating` 키워드      |
| 3. 예열                     | 1    | `예열` 키워드             |

### 13-5-13 Boiler Feed Pump (26개)

| sub_section            | 건수 | 분류 기준                       |
| ---------------------- | ---- | ------------------------------- |
| 1. Turbine driven type | 26   | 전체 (Motor driven type 미존재) |

---

## 검증 SQL

```sql
-- 분류 완전성 검증
SELECT source_section,
  count(*) AS total,
  count(*) FILTER (WHERE properties->>'sub_section' IS NOT NULL) AS classified,
  count(*) FILTER (WHERE properties->>'sub_section' IS NULL) AS unclassified
FROM graph_entities
WHERE source_section IN ('13-2-3','13-2-4','13-1-5','13-1-3','13-1-4','13-5-13','13-2-6')
  AND type = 'WorkType'
GROUP BY source_section;
```

**결과**: 전 섹션 `unclassified = 0` ✅
