# Phase 4: 토큰 분리 ILIKE 폴백 — 구현 결과 보고서

> 작성일: 2026-02-15 | Edge Function v73 | 배포 완료

## 1. 문제 정의

`graphClarify`의 전략 1(Section ILIKE 매칭)에서 중간에 수식어가 포함된 Section 이름을 찾지 못하는 구조적 문제.

| 검색어      | ILIKE 패턴      | DB Section 이름       | 결과  |
| ----------- | --------------- | --------------------- | :---: |
| 강판용접    | `%강판용접%`    | 강판 **전기아크**용접 |   ❌   |
| 밸브취부    | `%밸브취부%`    | 밸브 취부             |   ❌   |
| Fitting취부 | `%Fitting취부%` | Fitting 취부          |   ❌   |

## 2. 근본 원인 (3-Layer)

| #   | 레이어            | 원인                                                                        |
| --- | ----------------- | --------------------------------------------------------------------------- |
| 1   | **DeepSeek 응답** | `work_name: "강판용접 강판용접 Plate Welding"` 같은 비결정적 중복/영문 혼입 |
| 2   | **ILIKE 패턴**    | `%강판용접%`는 "강판 전기아크용접"의 중간 수식어과 불일치                   |
| 3   | **코드 참조**     | `sections` 변수를 직접 참조하여 토큰 분리 결과 누락                         |

## 3. 해결 — 3단계 코드 수정

### 3-1. searchTerms[0] 정규화

DeepSeek 비결정적 응답 대응. 한글 토큰 추출 + 중복 제거.

```typescript
const koreanTokens = [...new Set(raw.match(/[가-힣]{2,}/g) || [])];
searchTerms[0] = koreanTokens.join('');
// "강판용접 강판용접 Plate Welding" → "강판용접"
```

### 3-2. 전략 1-B: 2음절 분리 ILIKE 폴백

공백 없는 4글자+ 한글 복합어를 반으로 분리하여 AND ILIKE 조건 생성.

```typescript
// "강판용접" → ["강판", "용접"]
if (tokens.length === 1 && tokens[0].length >= 4) {
    const halfLen = Math.ceil(word.length / 2);
    tokens = [word.substring(0, halfLen), word.substring(halfLen)];
}
// → WHERE name ILIKE '%강판%' AND name ILIKE '%용접%'
```

### 3-3. effectiveSections 참조 통합

전략 1 결과(정확 매칭 or 토큰 분리)를 통합하여 이후 로직에서 일관되게 사용.

```diff
- const prelimResults = [...(sections || []), ...sectionChildWorkTypes, ...];
+ const prelimResults = [...effectiveSections, ...sectionChildWorkTypes, ...];
```

## 4. DB 시뮬레이션 테스트 (사전 검증)

| #   | 검색어       | 1-A 정확 | 1-B 토큰 분리 |     판정     |
| --- | ------------ | :------: | :-----------: | :----------: |
| 1   | 강판용접     |  ❌ 0건   |     ✅ 1건     |  핵심 해결   |
| 2   | 강관용접     |  ✅ 1건   |     ✅ 1건     |  기존 유지   |
| 3   | 밸브취부     |  ❌ 0건   |     ✅ 1건     |     해결     |
| 4   | 용접배관     |  ✅ 3건   |     ✅ 3건     |     동일     |
| 5   | 콘크리트타설 |   1건    |      5건      | clarify 확대 |
| 6   | Fitting취부  |  ❌ 0건   |     ✅ 1건     |     해결     |
| 7   | 거푸집설치   |  ❌ 0건   |      7건      | clarify 확대 |

## 5. API 검증 결과 (v73)

| #   | 검색어          | 수정 전 |  수정 후  | 상세                                   |
| --- | --------------- | :-----: | :-------: | -------------------------------------- |
| 1   | **강판용접**    |  ❌ 0건  | ✅ **6건** | V형/U형/공통/X형/Fillet sub_section 칩 |
| 2   | **밸브취부**    |  ❌ 0건  | ✅ **5건** | Screwed/Welding Type sub_section 칩    |
| 3   | **Fitting취부** |  ❌ 0건  | ✅ **3건** | Fitting/Flange/밸브 취부 section 칩    |
| 4   | **강관용접**    |  ✅ 3건  | ✅ **3건** | 기존 동작 완전 유지                    |

## 6. 안전장치

| #   | 조건                               | 목적                      |
| --- | ---------------------------------- | ------------------------- |
| 1   | 전략 1-A가 **0건일 때만** 1-B 실행 | 기존 정확 매칭 보호       |
| 2   | `searchTerms[0].length >= 4`       | 짧은 단어 과분리 방지     |
| 3   | 토큰 **2개 이상**일 때만 AND 조건  | 단일 토큰 중복 실행 방지  |
| 4   | `.limit(10)`                       | 결과 과다 방지            |
| 5   | 한글 15자 초과 시 폴백             | DeepSeek 비정상 응답 대응 |

## 변경 파일

- `clarify.ts` — L357-377 (정규화), L643-672 (전략 1-B), L712/L743 (effectiveSections)
