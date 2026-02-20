# 검색 품질 고도화 & 데이터 추출 정밀화 구현 계획

파이프라인 아키텍처 개편(spec 분리, 팽창 제어, CrossRef 격리)이 완료된 이후 남은 2건의 잔존 이슈를 순차적으로 해결합니다.

## Track B: 검색 체감 즉각 개선 (동의어 쿼리 확장)

### 문제 정의

사용자 쿼리 **"pe관"** 검색 시 **2개 분야**만 반환되고, PE관과 직접 관련된 토목부문 항목 3개가 누락됩니다.

| 상태 | entity 이름 | section | 원인 |
|------|------------|---------|------|
| ✅ 매칭 | PE드럼 설치 및 해체 | 2-9-4 | 이름에 "PE" 포함 |
| ✅ 매칭 | 가교화 폴리에틸렌관 접합 및 배관 | 1-6-3#2 | 이름에 "폴리에틸렌" 포함 |
| ❌ 누락 | **소켓융착 접합 및 부설** | 6-5-3 | 이름에 "PE" 없음 |
| ❌ 누락 | **바트융착 접합 및 부설** | 6-5-4 | 이름에 "PE" 없음 |
| ❌ 누락 | **새들융착 접합** | 6-5-4 | 이름에 "PE" 없음 |

> [!IMPORTANT]
> 기존 분석에서 "11-2-1 (버트 융착식)"을 PE관 관련으로 분류했으나, DB 확인 결과 **"수성페인트 붓칠"**로 PE관과 무관합니다. 실제 누락 항목은 토목부문(6-5-3, 6-5-4)의 3개입니다.

### 근본 원인

`targetSearch` (search.ts L360-416)의 2단계 키워드 ILIKE 검색에서 결과가 발견되면 **조기 반환**(L396 `if (data && data.length > 0)`)하기 때문에, 3단계 벡터 검색이나 4단계 chunk 폴백이 실행되지 않습니다. "PE관" 쿼리는 2건이 ILIKE에서 매칭되므로 벡터 검색 없이 반환됩니다.

---

### B-1: 도메인 동의어 사전 (DOMAIN_SYNONYM_MAP)

#### [MODIFY] [search.ts](file:///G:/My%20Drive/Antigravity/edge-function/search.ts)

기존 `ABBREVIATION_MAP` (L10-17) 바로 아래에 **건설 도메인 동의어 사전** `DOMAIN_SYNONYM_MAP`을 추가합니다.

```typescript
// ─── 건설 도메인 동의어 사전 ───
// Why: "PE관" 검색 시 "바트융착", "소켓융착" 등
//      이름에 "PE"가 없지만 PE관 작업인 entity를 검색망에 포함
const DOMAIN_SYNONYM_MAP: Record<string, string[]> = {
    "PE관": ["바트융착", "소켓융착", "새들융착", "폴리에틸렌", "HDPE", "버트융착"],
    "폴리에틸렌관": ["바트융착", "소켓융착", "새들융착", "PE관", "HDPE"],
    "융착": ["바트융착", "소켓융착", "새들융착", "PE관", "폴리에틸렌"],
    "가스관": ["PE관", "폴리에틸렌", "바트융착", "소켓융착"],
    "용접": ["TIG", "MIG", "MAG", "CO2", "아크용접", "가스용접", "피복아크"],
    "배관": ["강관", "폴리에틸렌관", "PVC관", "PE관", "동관", "스테인리스관"],
    "도장": ["페인트", "도료", "방청", "하도", "상도", "중도"],
    "방수": ["아스팔트방수", "시트방수", "도막방수", "실링"],
    "철근": ["배근", "이음", "정착", "가공조립"],
};
```

#### 신규 함수: `expandDomainSynonyms`

```typescript
export function expandDomainSynonyms(terms: string[]): string[] {
    const expanded: string[] = [];
    for (const term of terms) {
        for (const [key, synonyms] of Object.entries(DOMAIN_SYNONYM_MAP)) {
            if (term.includes(key) || key.includes(term)) {
                expanded.push(...synonyms);
            }
        }
    }
    return [...new Set(expanded)]; // 중복 제거
}
```

#### 검색 파이프라인 통합 (targetSearch 2단계 수정)

`targetSearch` 함수의 2단계 키워드 ILIKE (L384-394) `orClauses` 구성 시 도메인 동의어를 추가합니다.

```diff
 const mixedExpansions = expandMixedTerms(ilikeTerms);
+const domainExpansions = expandDomainSynonyms(ilikeTerms);
 const orClauses = [
     ...ilikeTerms.map(t => `name.ilike.%${t}%`),
     ...mixedExpansions.map(p => `name.ilike.${p}`),
+    ...domainExpansions.map(s => `name.ilike.%${s}%`),
 ].join(",");
```

---

### B-2: LLM 동의어 확장 (후속 — 이번 구현 범위 외)

`analyzeIntent`의 시스템 프롬프트에 도메인 동의어 생성 지시를 추가하는 방안. B-1 적용 후 커버리지가 부족한 경우에만 후속 구현합니다.

---

## Track A: 데이터 추출 정밀화 (200mm 수량 누락 진단)

### 문제 정의

W-0895 (200mm PE관) entity는 정상 생성되었으나, 구체적인 수량 데이터(인력/장비/자재)가 비어있습니다.

### 진단 순서 (구현 전 선행 확인)

#### Step 1: 원본 chunk 데이터 확인

```sql
SELECT chunk_id, section_id, 
       LEFT(text_content, 500) as text_preview
FROM graph_chunks 
WHERE section_id = '11-3#2'
ORDER BY chunk_id
LIMIT 10;
```

이 쿼리로 200mm 규격의 수량 데이터가 **chunk 본문에 존재하는지** 확인합니다.

- **존재 O** → Step 2 LLM 추출 프롬프트 개선 필요 (Track A-2)
- **존재 X** → Step 1 Markdown 테이블 파싱 개선 필요 (Track A-1)

#### Step 2: Step 2 LLM 추출 결과 확인

```sql
SELECT ge.name, ge.properties, gr.type as rel_type,
       gr.properties as rel_props
FROM graph_entities ge
LEFT JOIN graph_relationships gr ON ge.id = gr.source_entity_id
WHERE ge.entity_id = 'W-0895'
LIMIT 20;
```

#### Step 3: 원인 분류 후 액션

| 원인 계층 | 액션 | 소요 |
|----------|------|------|
| Step 1 (Markdown 변환) | `step1_table_extractor.py` 매트릭스 테이블 파싱 로직 개선 | 2-3시간 |
| Step 2 (LLM 추출) | `step2_llm_extractor.py` 프롬프트에 CoT/Few-shot 추가 | 1시간 + API 비용 |

> [!WARNING]
> Track A는 Step 2 재실행 시 **LLM API 비용** (2,105 chunk × Gemini 호출)이 발생합니다. 진단 완료 후 재실행 여부를 별도 확인합니다.

---

## 수정 파일 요약

| Track | 파일 | 변경 내용 |
|-------|------|----------|
| B-1 | [search.ts](file:///G:/My%20Drive/Antigravity/edge-function/search.ts) | `DOMAIN_SYNONYM_MAP` 사전 + `expandDomainSynonyms` 함수 추가, `targetSearch` 2단계에 통합 |
| A (진단) | SQL 쿼리 | chunk 본문 내 200mm 데이터 존재 여부 확인 |
| A-1 (조건부) | [step1_table_extractor.py](file:///G:/My%20Drive/Antigravity/pipeline/phase2_extraction/step1_table_extractor.py) | 매트릭스 테이블 열 파싱 정밀도 개선 |
| A-2 (조건부) | [step2_llm_extractor.py](file:///G:/My%20Drive/Antigravity/pipeline/phase2_extraction/step2_llm_extractor.py) | 추출 프롬프트 CoT/Few-shot 보강 |

---

## Verification Plan

### Track B-1 자동 검증

1. **배포 후 API 테스트** — `"pe관"` 쿼리 전송, 반환된 `clarification.options` 수가 **4개 이상**인지 확인:
   ```powershell
   # "pe관" 쿼리 → 4개 이상 분야 반환 확인
   $body = [System.Text.Encoding]::UTF8.GetBytes('{"question":"pe관","history":[]}')
   Invoke-WebRequest -Uri "https://bfomacoarwtqzjfxszdr.supabase.co/functions/v1/rag-chat" ...
   # -> clarification.options 배열에 소켓융착, 바트융착 포함 여부 확인
   ```

2. **회귀 테스트** — 기존 정상 쿼리가 깨지지 않았는지 확인:
   - `"가스용 PE관 융착품셈"` → clarify 반환 (기존 5개 분야)
   - `"보일러 드럼 설치"` → answer 반환 (5~8초 이내)
   - `"강관용접 200mm"` → answer 또는 clarify 반환

3. **브라우저 테스트** — `https://main.antigravity-chatbot.pages.dev/` 에서 사용자가 "pe관" 입력 시 4개 이상 분야 칩 표시 확인

### Track A 진단 검증

1. SQL 쿼리로 chunk 본문에 200mm 수량 존재 여부 확인
2. 결과에 따라 Step 1/Step 2 어느 계층에 문제인지 판정 후 사용자에게 보고
