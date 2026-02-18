# Phase 3-C: chunk 본문 키워드 검색 Fallback 구현 계획서
> 작성일: 2026-02-13 21:41  
> 상태: 📋 계획 수립  
> 우선순위: ★★★★★  
> 예상 공수: 30~40분

---

## 1. 목표

**엔티티 이름에 없지만 chunk 본문에만 존재하는 검색어**를 찾을 수 있는 새로운 검색 레이어(Layer 3)를 추가한다.

### 해결 대상
| 검색어   | chunk text 포함 수 | 엔티티 이름 존재 | 현재 상태   |
| -------- | -----------------: | ---------------: | ----------- |
| 장비편성 |                4건 |              0건 | ❌ 검색 불가 |
| 인력편성 |                3건 |              0건 | ❌ 검색 불가 |
| 작업편성 |                2건 |              1건 | ⚠️ 부분      |
| 장비소요 |                1건 |              0건 | ❌ 검색 불가 |
| 작업능력 |               18건 |              2건 | ⚠️ 부분      |

---

## 2. 현재 검색 파이프라인 분석

### 2-1. targetSearch 함수 (L1309-1404)

```
targetSearch(analysis, embedding, question)
│
├─ 1단계: ILIKE 정확 매칭 (work_name + spec)
│  → graph_entities.name ILIKE '%{work_name}%{spec}%'
│  → type IN ('WorkType', 'Section')
│  → 성공 시 return (similarity=1.0)
│
├─ 1단계 fallback: work_name만 ILIKE
│  → 성공 시 return (similarity=0.98)
│
├─ 2단계: keywords 기반 ILIKE
│  → graph_entities.name ILIKE '%{kw1}%{kw2}%'
│  → 성공 시 return (similarity=0.95)
│
├─ 2단계 fallback: work_name 단독 ILIKE
│  → 성공 시 return (similarity=0.90)
│
└─ 3단계: 벡터 검색 (search_entities_typed RPC)
   → graph_entities.embedding 유사도
   → type_filter: ["Section", "WorkType"]
   → threshold 0.4
   → 항상 return (벡터 결과)
```

### 2-2. 문제 흐름 추적: "장비 편성 관련 품셈"

```
analyzeIntent("장비 편성 관련 품셈")
→ LLM 응답 (추정):
  {
    intent: "clarify_needed" 또는 "search",
    work_name: "장비 편성",
    spec: null,
    keywords: ["장비", "편성"]
  }

targetSearch 진입:
├─ 1단계: work_name="장비 편성", spec=null → spec 없으므로 skip
├─ 2단계: keywords=["장비", "편성"]
│  → ILIKE '%장비%편성%' on graph_entities.name
│  → 0건 (엔티티 이름에 "장비편성" 포함된 것 없음)
├─ 2단계 fallback: work_name="장비 편성"
│  → ILIKE '%장비 편성%' on graph_entities.name  
│  → 0건
└─ 3단계: 벡터 검색
   → "장비 편성" 임베딩과 가장 유사한 엔티티
   → "공통장비"(Section), "부수장비"(Section) 등 반환
   → 이것들이 최종 결과 → 오답 ❌
```

### 2-3. 왜 3단계 벡터 검색이 정답을 못 찾는가

벡터 검색 대상: `graph_entities.embedding` (엔티티 이름의 임베딩)

| 정답 엔티티     | 엔티티 이름         | "장비 편성"과 유사도    |
| --------------- | ------------------- | ----------------------- |
| 5-2-2의 Section | "고압분사 주입공법" | 낮음 ← 이름이 완전 다름 |
| 5-3-1의 Section | "기성말뚝 기초"     | 낮음 ← 이름이 완전 다름 |

| 오답 엔티티    | 엔티티 이름 | "장비 편성"과 유사도 |
| -------------- | ----------- | -------------------- |
| 2-12의 Section | "공통장비"  | 높음 ← "장비" 포함   |
| 8-6의 Section  | "부수장비"  | 높음 ← "장비" 포함   |

→ 임베딩 유사도가 **이름의 단어 겹침**에 의존하므로, 정답 엔티티는 구조적으로 반환 불가.

---

## 3. 구현 설계

### 3-1. 설계 원칙

1. **비침습적 추가**: 기존 Layer 1~3에 영향 없이 새 Layer 4를 **뒤에 추가**
2. **조건부 실행**: Layer 1~3이 **충분한 결과를 반환하면 Layer 4는 skip**
3. **chunk → 섹션 → WorkType 경로**: chunk text 매칭 → 해당 섹션의 WorkType 반환
4. **복합어 탐지**: "장비 편성" → "장비편성"으로 결합하여 검색 (띄어쓰기 변형 대응)

### 3-2. 신규 함수: `chunkTextFallbackSearch`

**위치**: `index.ts` L291 부근 (keywordFallbackSearch 아래)

```typescript
// ─── Layer 4: chunk 본문 텍스트 키워드 검색 ───
// Why: "장비편성", "인력편성" 등 엔티티 이름에 없지만 
//      chunk 본문에만 존재하는 소제목/용어를 검색
async function chunkTextFallbackSearch(
    question: string
): Promise<EntityResult[]> {
    // 1. 질문에서 한글 키워드 추출 (stopWords 적용하되, 복합어 우선)
    const koreanWords = question.match(/[가-힣]{2,}/g) || [];
    const contextStopWords = new Set([
        "품셈", "알려줘", "얼마", "관련", "어떻게", "무엇"
    ]);  // "장비", "인력" 등은 여기서 제거하지 않음! (복합어 구성요소)
    
    const filteredWords = koreanWords.filter(w => !contextStopWords.has(w));
    if (filteredWords.length === 0) return [];
    
    // 2. 복합어 생성: ["장비", "편성"] → "장비편성" 도 시도
    const compoundPatterns: string[] = [];
    // 원본 키워드 쌍을 붙인 복합어
    for (let i = 0; i < filteredWords.length - 1; i++) {
        compoundPatterns.push(filteredWords[i] + filteredWords[i + 1]);
    }
    // 원본 키워드 전체 결합
    if (filteredWords.length >= 2) {
        compoundPatterns.push(filteredWords.join(''));
    }
    
    // 3. chunk text에서 ILIKE 검색 (복합어 우선, 원본 키워드 폴백)
    const searchPatterns = [
        ...compoundPatterns.map(p => `%${p}%`),
        // 원본 키워드 조합 (띄어쓰기 포함)
        `%${filteredWords.join('%')}%`,
    ];
    
    for (const pattern of searchPatterns) {
        const { data: matchedChunks } = await supabase
            .from("graph_chunks")
            .select("section_id, title, department, chapter")
            .ilike("text", pattern)
            .limit(10);
        
        if (matchedChunks && matchedChunks.length > 0) {
            // 중복 section_id 제거
            const uniqueSections = new Map<string, any>();
            matchedChunks.forEach((c: any) => {
                if (!uniqueSections.has(c.section_id)) {
                    uniqueSections.set(c.section_id, c);
                }
            });
            
            const sectionIds = Array.from(uniqueSections.keys());
            console.log(`[chunkTextFallback] pattern="${pattern}" → `
                + `${sectionIds.length}개 섹션 매칭: `
                + sectionIds.join(', '));
            
            // 4. 매칭된 섹션의 WorkType 조회
            const { data: wtData } = await supabase
                .from("graph_entities")
                .select("id, name, type, properties, source_section")
                .eq("type", "WorkType")
                .in("source_section", sectionIds)
                .limit(15);
            
            if (wtData && wtData.length > 0) {
                // WorkType 반환 (chunk text 매칭이므로 similarity 0.85)
                return (wtData as any[]).map(e => ({
                    id: e.id,
                    name: e.name,
                    type: e.type,
                    properties: e.properties || {},
                    similarity: 0.85,
                    source_section: e.source_section,
                }));
            }
            
            // WorkType 없으면 → Section 엔티티 반환
            const { data: sectionEntities } = await supabase
                .from("graph_entities")
                .select("id, name, type, properties, source_section")
                .eq("type", "Section")
                .in("source_section", sectionIds)
                .limit(10);
            
            if (sectionEntities && sectionEntities.length > 0) {
                return (sectionEntities as any[]).map(e => ({
                    id: e.id,
                    name: e.name,
                    type: e.type,
                    properties: e.properties || {},
                    similarity: 0.80,
                    source_section: e.source_section,
                }));
            }
        }
    }
    
    return [];
}
```

### 3-3. targetSearch 수정: Layer 4 삽입 지점

**위치**: `index.ts` L1388~1404 (3단계 벡터 검색 직전 또는 직후)

**삽입 전략**: 3단계 벡터 검색 **이후**, 결과가 **Section만** 반환되었거나 **유사도가 낮을 때** 실행

```
현재 흐름:
  1단계 ILIKE → 2단계 키워드 → 3단계 벡터 → return

수정 후:
  1단계 ILIKE → 2단계 키워드 → 3단계 벡터 → [4단계 chunk text] → return
                                                 ↑
                                          조건: 3단계 결과가
                                          Section만이거나
                                          similarity < 0.7
```

**구체적 코드 수정:**

```typescript
// 3단계: 벡터 검색 (기존 코드)
const { data, error } = await supabase.rpc("search_entities_typed", { ... });
// ...
console.log(`[targetSearch] 3단계 벡터 검색: ${(data || []).length}건`);

const vectorResults = (data || []) as EntityResult[];

// 4단계: chunk text fallback (신규)
// Why: 벡터 결과가 Section만이거나 유사도 낮을 때,
//      chunk 본문에만 존재하는 용어("장비편성" 등)를 검색
const hasGoodMatch = vectorResults.some(
    e => e.type === 'WorkType' && e.similarity >= 0.7
);
if (!hasGoodMatch) {
    console.log(`[targetSearch] 4단계 chunk text fallback 시도`);
    const chunkResults = await chunkTextFallbackSearch(question);
    if (chunkResults.length > 0) {
        console.log(`[targetSearch] 4단계 chunk text: ${chunkResults.length}건`);
        // chunk 결과를 벡터 결과 앞에 배치 (우선순위 부여)
        const chunkIds = new Set(chunkResults.map(e => e.id));
        return [
            ...chunkResults,
            ...vectorResults.filter(e => !chunkIds.has(e.id)),
        ];
    }
}

return vectorResults;
```

### 3-4. 실행 흐름 시뮬레이션: "장비 편성 관련 품셈"

```
targetSearch 진입:
├─ 1단계: spec null → skip
├─ 2단계: keywords=["장비", "편성"]
│  → ILIKE '%장비%편성%' on entities.name → 0건 → skip
├─ 3단계: 벡터 검색
│  → "공통장비"(Section, sim=0.72), "부수장비"(Section, sim=0.68) 등
│  → Section만 반환, WorkType 없음
│
├─ 조건 검사: hasGoodMatch = false (WorkType + sim≥0.7 없음)
│
└─ 4단계: chunkTextFallbackSearch("장비 편성 관련 품셈")
   ├─ 한글 추출: ["장비", "편성", "관련", "품셈"]
   ├─ 경량 stopWords: "관련", "품셈" 제거 → ["장비", "편성"]
   ├─ 복합어 생성: "장비편성"
   ├─ chunk text ILIKE '%장비편성%'
   │  → 5-2-2(고압분사 주입공법), 5-3-1(기성말뚝 기초),
   │    5-3-2(말뚝박기용 천공), 5-3-5(현장타설말뚝) → 4개 섹션
   ├─ 해당 섹션의 WorkType 조회
   │  → "고압분사 주입공법", "기성말뚝 기초" 등
   └─ return [WorkType 결과] + [기존 벡터 결과] → 정답! ✅
```

---

## 4. 설계 상세

### 4-1. stopWords 전략 분리

**핵심: 두 개의 독립적 stopWords 세트 사용**

| 세트                                 | 용도                   | 포함 단어                                                 | "장비" 포함? |
| ------------------------------------ | ---------------------- | --------------------------------------------------------- | :----------: |
| **keywordFallback stopWords** (기존) | 엔티티 이름 ILIKE 검색 | "품셈", "인력", "인공", "수량", "단위", "장비", "자재" 등 |    ✅ 유지    |
| **chunkText stopWords** (신규)       | chunk 본문 텍스트 검색 | "품셈", "알려줘", "얼마", "어떻게", "무엇" 만             |   ❌ 미포함   |

**이유:**
- keywordFallback: 엔티티 **이름**에서 검색 → "장비"는 36개 WT에 매칭, 노이즈 높음 → 제거 유지
- chunkText: chunk **본문**에서 검색 → "장비편성"은 4건만 매칭, 정확도 높음 → 제거 불필요

### 4-2. 복합어 탐지 로직

품셈 도메인의 복합어 패턴:

| 입력             | 토큰                     | 복합어 생성            | 검색 패턴         |
| ---------------- | ------------------------ | ---------------------- | ----------------- |
| "장비 편성"      | ["장비", "편성"]         | "장비편성"             | `%장비편성%`      |
| "인력 편성 품셈" | ["인력", "편성"]         | "인력편성"             | `%인력편성%`      |
| "작업 능력 확인" | ["작업", "능력", "확인"] | "작업능력", "능력확인" | `%작업능력%` 우선 |
| "콘크리트 타설"  | ["콘크리트", "타설"]     | "콘크리트타설"         | `%콘크리트타설%`  |

**우선순위**: 복합어 → 원본 키워드 조합 순서로 검색. 첫 번째 매칭에서 중단.

### 4-3. 성능 고려사항

| 항목                       | 영향                             | 대응                                              |
| -------------------------- | -------------------------------- | ------------------------------------------------- |
| chunk text ILIKE 쿼리 속도 | `text` 컬럼 full-scan            | limit(10)으로 제한, 조건부 실행                   |
| 추가 DB 호출 수            | 최대 +3회 (chunk → WT → Section) | 조건부 실행 (Layer 1~3 성공 시 skip)              |
| 전체 응답 지연             | 최대 +500ms                      | 벡터 결과가 좋으면(WorkType + sim≥0.7) 실행 안 함 |

### 4-4. 부작용 방지 체크리스트

| #   | 시나리오                | Layer 4 실행?         | 결과                                   |
| --- | ----------------------- | --------------------- | -------------------------------------- |
| 1   | "콘크리트 타설 품셈"    | ❌ (2단계에서 WT 매칭) | 기존과 동일                            |
| 2   | "강관용접 200mm SCH 40" | ❌ (1단계에서 매칭)    | 기존과 동일                            |
| 3   | "거푸집 설치"           | ❌ (2단계에서 매칭)    | 기존과 동일                            |
| 4   | "장비 편성 관련 품셈"   | ✅ (3단계 Section만)   | **신규: 기초공사 데이터 반환**         |
| 5   | "인력편성 품셈"         | ✅ (3단계 실패)        | **신규: 기초공사 데이터 반환**         |
| 6   | "작업능력 확인"         | ✅ (3단계 일부)        | **신규: 도로포장 등 데이터 추가**      |
| 7   | "안녕하세요"            | ❌ (intent=greeting)   | 영향 없음                              |
| 8   | "장비 품셈"             | ✅ (3단계 Section만)   | chunk에서 "장비" 포함 섹션 → 검토 필요 |

**시나리오 8 주의**: "장비 품셈"은 chunk text에 "장비" 포함 섹션이 매우 많을 수 있음.  
→ **대응**: 복합어 매칭을 우선하고, 단일 키워드("장비")만 남는 경우 chunk 검색을 **skip**하는 가드 조건 추가.

```typescript
// 가드 조건: 단일 키워드만 남은 경우 chunk text 검색 skip
// Why: "장비"만으로 chunk text 검색하면 수백 건 매칭 → 노이즈
if (filteredWords.length < 2 && compoundPatterns.length === 0) {
    console.log(`[chunkTextFallback] 단일 키워드만 → skip`);
    return [];
}
```

---

## 5. 구현 단계

### Step 1: 함수 작성 (10분)

| 작업                                | 위치                              | 내용             |
| ----------------------------------- | --------------------------------- | ---------------- |
| `chunkTextFallbackSearch` 함수 생성 | L291 (keywordFallbackSearch 아래) | 위 설계대로 구현 |

### Step 2: targetSearch 수정 (5분)

| 작업                            | 위치       | 내용                    |
| ------------------------------- | ---------- | ----------------------- |
| 3단계 벡터 검색 후 Layer 4 삽입 | L1400-1403 | 조건부 실행 + 결과 병합 |

### Step 3: 배포 (3분)

| 작업             | 명령어                                          |
| ---------------- | ----------------------------------------------- |
| 파일 복사 + 배포 | `copy → npx supabase functions deploy rag-chat` |

### Step 4: 테스트 (10분)

| #   | 테스트 쿼리             | 기대 결과                            |
| --- | ----------------------- | ------------------------------------ |
| T1  | "장비 편성 관련 품셈"   | 기초공사 섹션 반환 (5-2-2, 5-3-1 등) |
| T2  | "인력편성 품셈"         | 기초공사 인력편성 데이터             |
| T3  | "콘크리트 타설 품셈"    | 기존과 동일 (Layer 4 미실행)         |
| T4  | "강관용접 200mm SCH 40" | 기존과 동일 (Layer 4 미실행)         |
| T5  | "장비 품셈"             | 가드 조건으로 skip 확인              |
| T6  | "작업능력 확인"         | 도로포장 등 관련 데이터              |

### Step 5: 회귀 테스트 (5분)

기존 정상 동작하던 쿼리들의 결과가 동일한지 확인:
- "콘크리트 타설 인력" → 기존 결과 유지
- "거푸집 설치 품셈" → 기존 결과 유지
- "공통장비 품셈" (section_id=2-12) → B0 하위 절 탐색 동작 유지

---

## 6. 위험 분석 및 완화

### 6-1. 위험 요소

| 위험                            | 가능성 | 영향                 | 완화                                         |
| ------------------------------- | ------ | -------------------- | -------------------------------------------- |
| chunk text ILIKE 성능 저하      | 중     | 응답 지연 +500ms     | 조건부 실행으로 대부분 skip                  |
| 단일 키워드 chunk 검색 → 노이즈 | 높음   | 관련 없는 결과 반환  | 가드 조건 (filteredWords.length < 2 → skip)  |
| 기존 검색 결과 변경             | 낮음   | 사용자 혼란          | Layer 4는 기존 3단계가 좋은 결과 내면 skip   |
| 복합어 오탐                     | 낮음   | 잘못된 복합어로 매칭 | 복합어 우선이되, 원본 키워드 조합도 fallback |

### 6-2. 롤백 계획

Layer 4 전체가 별도 함수 + 조건부 호출이므로, 문제 발생 시:
1. targetSearch 내 `if (!hasGoodMatch)` 블록만 주석 처리
2. 재배포 → 즉시 원복 (< 3분)

---

## 7. 이번 수정의 효과 예측

| 구분            | 현재                      | 수정 후                |
| --------------- | ------------------------- | ---------------------- |
| "장비편성" 검색 | ❌ 오답 (공통장비 등 반환) | ✅ 기초공사 데이터 반환 |
| "인력편성" 검색 | ❌ 검색 불가               | ✅ 관련 섹션 반환       |
| "작업능력" 검색 | ⚠️ 2/18건만 매칭           | ✅ 18건 chunk 매칭      |
| 기존 정상 쿼리  | ✅                         | ✅ 영향 없음            |
| 검색 커버율     | ~97%                      | ~98%+                  |

---

## 8. 향후 확장 가능성

이번 구현은 **최소 침습적 Layer 4** 추가입니다. 향후:

| 확장                                   | 설명                                    | 효과                         |
| -------------------------------------- | --------------------------------------- | ---------------------------- |
| graph_chunks.embedding 벡터 검색       | chunk 본문 임베딩 생성 + 벡터 검색      | 의미 기반 chunk 검색 가능    |
| PostgreSQL Full-Text Search (tsvector) | text 컬럼에 GIN 인덱스 추가             | ILIKE 대비 10~100x 성능 향상 |
| 소제목 엔티티 자동 추출                | PDF 파싱 시 "4. 장비편성" → Entity 생성 | 근본 해결 (데이터 레벨)      |
