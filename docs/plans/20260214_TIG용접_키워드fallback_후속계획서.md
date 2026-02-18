# TIG 용접 키워드 fallback 보강 — 후속작업 계획서

> 작성일: 2026-02-14 | 선행작업: Phase 1-2 (RAW_TABLE 폴백 + 매트릭스 테이블) 완료

---

## 1. 문제 정의

### 현상
"TIG용접 품셈" 검색 시 `13-2-3 강관용접('18년 보완)` 섹션의 `W-0631 TIG(Tungsten Inert Gas)용접`에 도달하지 못함.

### 근본 원인
| 계층           | 원인                                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------------------- |
| 벡터 검색      | 임베딩 모델이 "TIG"를 "강판전기아크용접(13-2-4)"에 더 높은 유사도로 매칭                                |
| ILIKE 검색     | `%TIG%용접%` 패턴으로 `TIG(Tungsten Inert Gas)용접` 미매칭 — 괄호 안에 "Tungsten Inert Gas"가 끼어 있음 |
| `targetSearch` | 의도 분석에서 `work_name="TIG용접"`으로 추출 → ILIKE `%TIG용접%`는 "TIG(Tungsten..."을 매칭하지 못함    |

---

## 2. 해결 방안: 약칭 매핑 테이블

### 핵심 아이디어
건설 품셈에서 자주 사용되는 **약칭(abbreviation)↔정식 명칭** 매핑을 `search.ts`에 추가.  
ILIKE 검색 시 질문의 약칭을 정식 명칭으로도 변환하여 병렬 검색.

### 약칭 매핑 예시
```typescript
const ABBREVIATION_MAP: Record<string, string[]> = {
    "TIG": ["TIG(Tungsten Inert Gas)", "TIG용접", "Tungsten Inert Gas"],
    "MIG": ["MIG(Metal Inert Gas)", "MIG용접"],
    "MAG": ["MAG(Metal Active Gas)", "MAG용접"],
    "CO2": ["CO2 아크용접", "CO₂ 용접"],
};
```

---

## 3. 수정 대상 파일

### [MODIFY] [search.ts](file:///g:/My%20Drive/Antigravity/edge-function/search.ts)

#### 변경 1: `ABBREVIATION_MAP` 상수 추가 (L6 이후)
- TIG, MIG, MAG, CO2 등 건설 용접 약칭 매핑

#### 변경 2: `expandAbbreviations()` 함수 추가
- 질문에서 약칭 감지 → 정식 명칭 배열 반환
- 예: "TIG용접" → ["TIG(Tungsten Inert Gas)", "TIG용접", "Tungsten Inert Gas"]

#### 변경 3: `keywordFallbackSearch()` 수정 (L25-61)
- 기존 단일 ILIKE 패턴에 약칭 확장 패턴 OR 추가
- `name.ilike.%TIG용접%` → `name.ilike.%TIG용접%` **OR** `name.ilike.%TIG(Tungsten%`

#### 변경 4: `targetSearch()` 1단계 수정 (L254-281)
- work_name에서 약칭 감지 시 확장된 패턴으로도 병렬 검색
- 조건부 적용: 기존 ILIKE 실패 시에만 약칭 확장 실행

### [MODIFY] [index.ts](file:///g:/My%20Drive/Antigravity/edge-function/index.ts) (해당 시)
- `search.ts`와 동일 로직 동기화 (index.ts 내장 검색 함수가 있는 경우)

---

## 4. Verification Plan

### 자동 테스트
```bash
# 테스트 1: TIG용접 → 13-2-3 섹션 도달 확인
curl -X POST ... -d '{"question":"TIG용접 품셈"}'
# 기대: clarify 또는 answer에 "13-2-3" 포함

# 테스트 2: 기존 강관용접 200 SCH 40 동작 유지
curl -X POST ... -d '{"question":"강관용접 200mm SCH 40 품셈"}'
# 기대: 변경 없이 정상 동작
```

### 검증 항목
- [ ] TIG용접 → `W-0631 TIG(Tungsten Inert Gas)용접` (13-2-3) 매칭
- [ ] 강관용접 200 SCH 40 → 기존과 동일 응답
- [ ] 잡철물 제작 → 기존과 동일 응답
- [ ] 텍스 해체 → 기존과 동일 응답
