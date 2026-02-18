# 의도 분석 프롬프트 재설계 — Walkthrough

> 작성일: 2026-02-15  
> 근본 원인: LLM이 중복 키워드 생성 → `searchTerms.join("%")` 과잉 패턴 → ILIKE 매칭 실패  
> 적용 스킬: `prompt-engineering-patterns`, `llm-structured-extraction`

---

## 변경 사항

### 1. INTENT_SYSTEM_PROMPT 재설계

**파일:** `edge-function/index.ts` L900-953

**AS-IS:**
- 한글→영문 변환 규칙 15줄 하드코딩 (예: `"크러셔" → ["크러셔", "Crusher"]`)
- keywords와 work_name 중복 허용
- Few-shot 예시 없이 규칙만 나열

**TO-BE:**
- 구조: **Role → Output Schema → Hard Constraints → 의도 분류 → Few-shot 5개**
- 한글→영문 변환 규칙 **제거** (프롬프트에서 제약 규칙으로 전환)
- **"keywords에 work_name 포함 금지"** 명시적 제약 추가
- **Few-shot 예시 5개** 추가 (TIG용접, 강관용접, 용접, 크러셔, 잡철물)

**핵심 제약 (추가):**
```
1. keywords에 work_name과 동일하거나 work_name의 부분어를 절대 포함하지 마라
   - work_name="TIG용접"이면 keywords에 "TIG", "TIG용접", "용접" 모두 금지
2. 한글 외래어가 공종명이면 work_name에 영문 원어 사용
   한글 원어는 keywords에 1개만 추가
```

---

### 2. `buildSearchTerms()` 유틸리티 추가

**파일:** `edge-function/index.ts` L1181-1194

```typescript
function buildSearchTerms(work_name: string | null, keywords: string[]): string[] {
    const terms: string[] = [];
    if (work_name) terms.push(work_name);
    for (const kw of keywords) {
        if (!kw || kw.length < 2) continue;
        // work_name에 이미 포함된 키워드는 제외
        if (terms.some(t => t.includes(kw) || kw.includes(t))) continue;
        if (terms.includes(kw)) continue;
        terms.push(kw);
    }
    return terms;
}
```

**효과:** `["TIG용접", "TIG", "TIG용접"]` → `["TIG용접"]` 정제

---

### 3. ILIKE 패턴 전략 변경

| 위치                       | AS-IS                               | TO-BE                             |
| -------------------------- | ----------------------------------- | --------------------------------- |
| graphClarify 전략2 (L1399) | `"%" + searchTerms.join("%") + "%"` | `searchTerms[0]` 단독 + 보조 OR절 |
| targetSearch 2단계 (L1771) | `"%" + searchTerms.join("%") + "%"` | `searchTerms[0]` 단독 패턴        |

**AS-IS 패턴 예시:**
```
searchTerms = ["TIG용접", "TIG", "TIG용접"]
→ workPattern = "%TIG용접%TIG%TIG용접%"  ← DB에 매칭 불가능
```

**TO-BE 패턴 예시:**
```
searchTerms = ["TIG용접"]  (buildSearchTerms로 정제)
→ primaryPattern = "%TIG용접%"            ← W-0631 정상 매칭
```

---

## 테스트 결과

| #   | 쿼리                    | type      | 핵심 결과                                                               | 판정 |
| --- | ----------------------- | --------- | ----------------------------------------------------------------------- | ---- |
| 1   | "TIG용접 품셈"          | `clarify` | **W-0631 (13-2-3)** TIG용접                                             | ✅    |
| 2   | "강관용접 200mm SCH 40" | `answer`  | source_section=13-2-3, similarity=0.98                                  | ✅    |
| 3   | "용접"                  | `clarify` | 5개 분야 (용접선부속, 플랜트용접, 궤도용접, 강관용접, 강판전기아크용접) | ✅    |
| 4   | "잡철물"                | `clarify` | 2개 부문 (건축 8-3-1, 기계설비 9-1-2)                                   | ✅    |

> **핵심 검증:** "TIG용접 품셈" → **W-0631 (13-2-3)** 정상 반환.  
> 이전에 13-2-4 (강판전기아크용접) 만 반환되던 문제 해결됨.

---

## 근본 원인 → 해결 요약

```
[원인] INTENT_SYSTEM_PROMPT가 "tig" → ["TIG", "TIG용접"] 변환 규칙 포함
       → LLM이 work_name="TIG용접" + keywords=["TIG", "TIG용접"] 중복 생성
       → searchTerms.join("%") = "%TIG용접%TIG%TIG용접%"
       → ILIKE 매칭 실패 → 13-2-4 폴백

[해결] (1) 프롬프트에서 변환 규칙 제거 + 중복 금지 명시
       (2) buildSearchTerms()로 중복 자동 제거
       (3) ILIKE 패턴을 work_name 단독 우선으로 변경
       → "%TIG용접%" → W-0631 (13-2-3) 정상 매칭
```
