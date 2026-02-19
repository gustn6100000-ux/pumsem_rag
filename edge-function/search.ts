// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// search.ts — 검색 파이프라인 (벡터 + 키워드 + 타겟 + 청크 폴백)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import { supabase } from "./config.ts";
import type { EntityResult, IntentAnalysis } from "./types.ts";

// ─── 약칭(Abbreviation) → 정식 명칭 매핑 ───
// Why: "TIG용접" 검색 시 엔티티명 "TIG(Tungsten Inert Gas)용접"에
//      ILIKE %TIG%용접%이 매칭 안 됨 → 정식 명칭을 병렬 검색
const ABBREVIATION_MAP: Record<string, string[]> = {
    "TIG": ["TIG(Tungsten Inert Gas)", "Tungsten Inert Gas"],
    "MIG": ["MIG(Metal Inert Gas)", "Metal Inert Gas"],
    "MAG": ["MAG(Metal Active Gas)", "Metal Active Gas"],
    "CO2": ["CO2 아크", "CO₂"],
    "SMAW": ["SMAW(Shielded Metal Arc Welding)", "피복아크용접"],
    "SAW": ["SAW(Submerged Arc Welding)", "서브머지드아크용접"],
};

// ─── 질문에서 약칭 감지 → 확장 패턴 목록 반환 ───
export function expandAbbreviations(question: string): string[] {
    const expanded: string[] = [];
    for (const [abbr, aliases] of Object.entries(ABBREVIATION_MAP)) {
        // 대소문자 무관 검색 ("tig" → "TIG")
        if (question.toUpperCase().includes(abbr)) {
            expanded.push(...aliases);
        }
    }
    return expanded;
}

// ─── 영한 혼합어 분리 패턴 생성 ───
// Why: "PE관" 검색 시 ILIKE '%PE관%'은 "가스용 폴리에틸렌(PE)관"에 매칭 안 됨
//      PE와 관 사이에 ')' 괄호가 존재하기 때문.
//      "PE관" → "%PE%관%" 로 분리하여 중간 문자를 허용하는 완화 패턴 생성
export function expandMixedTerms(terms: string[]): string[] {
    const extra: string[] = [];
    for (const t of terms) {
        // 영문+한글 경계에서 분리: "PE관" → ["PE", "관"], "HDPE관" → ["HDPE", "관"]
        const parts = t.match(/[A-Za-z0-9]+|[가-힣]+/g);
        if (parts && parts.length >= 2) {
            // 원본("PE관")과 다른 완화 패턴("%PE%관%") 추가
            const relaxed = "%" + parts.join("%") + "%";
            const strict = `%${t}%`;
            if (relaxed !== strict) {
                extra.push(relaxed);
            }
        }
    }
    return extra;
}

// ─── 질문에서 규격 숫자 추출 ───
// "강관용접 200mm SCH 40" → ["200", "SCH 40"]
// "강관용접 φ350 SCH 20"  → ["350", "SCH 20"]
export function extractSpecNumbers(question: string): string[] {
    const nums: string[] = [];

    // 구경 숫자 추출 (200mm, φ200, 200A 등에서 숫자만)
    const diameterMatch = question.match(/(?:[φΦø∅]?\s*)(\d{2,4})\s*(?:mm|A|㎜)?/);
    if (diameterMatch) nums.push(diameterMatch[1]);

    // SCH 추출 (SCH 40, SCH40 등)
    const schMatch = question.match(/SCH\s*(\d+)/i);
    if (schMatch) nums.push(`SCH ${schMatch[1]}`);

    return nums;
}

// ─── ILIKE 기반 키워드 폴백 검색 ───
export async function keywordFallbackSearch(question: string, specNumbers: string[]): Promise<EntityResult[]> {
    // 질문에서 공종명 추출 (한글 2글자 이상 단어)
    const koreanWords = question.match(/[가-힣]{2,}/g) || [];
    // 품셈, mm 등 일반 키워드 제외
    const stopWords = new Set(["품셈", "인력", "인공", "수량", "단위", "장비", "자재", "알려줘", "얼마", "관련"]);
    const workKeywords = koreanWords.filter(w => !stopWords.has(w));

    if (workKeywords.length === 0) return [];

    // 단일 ILIKE 패턴 조합: "%강관용접%200%SCH 40%"
    // Why: supabase-js v2의 .ilike() 체이닝 시 TypeScript 타입 소실 문제 회피
    //      단일 호출로 모든 키워드를 포함하는 엔티티 검색
    const allTokens = [...workKeywords, ...specNumbers];
    const pattern = "%" + allTokens.join("%") + "%";

    // ─── 약칭 확장: TIG → TIG(Tungsten Inert Gas) 등 ───
    const abbrExpansions = expandAbbreviations(question);
    const orClauses = [`name.ilike.${pattern}`, `properties->>'korean_alias'.ilike.${pattern}`];
    for (const alias of abbrExpansions) {
        orClauses.push(`name.ilike.%${alias}%`);
    }
    const { data, error } = await supabase
        .from("graph_entities")
        .select("id, name, type, properties, source_section")
        .in("type", ["WorkType", "Standard"])
        .or(`name.ilike.${pattern},properties->>"korean_alias".ilike.${pattern}`)
        .limit(3);

    if (error || !data) {
        console.error("keywordFallbackSearch error:", error?.message);
        return [];
    }

    // EntityResult 형태로 변환 (similarity는 1.0으로 설정 — 정확 매칭)
    console.log(`[keywordFallback] ${data.length}건 매칭 (약칭확장: ${abbrExpansions.length}개)`);
    return (data as any[]).map((e: any) => ({
        id: e.id,
        name: e.name,
        type: e.type,
        properties: e.properties || {},
        similarity: 1.0, // 키워드 정확 매칭
        source_section: e.source_section,
    }));
}

// ─── Layer 4: chunk 본문 텍스트 키워드 검색 ───
// Why: "장비편성", "인력편성" 등 엔티티 이름에 없지만
//      chunk 본문에만 존재하는 소제목/용어를 검색
//      기존 Layer 1~3에 영향 없이, 조건부로만 실행
export async function chunkTextFallbackSearch(
    question: string
): Promise<EntityResult[]> {
    // 1. 질문에서 한글 키워드 추출 (경량 stopWords — "장비","인력" 등은 보존)
    const koreanWords = question.match(/[가-힣]{2,}/g) || [];
    const contextStopWords = new Set([
        "품셈", "알려줘", "얼마", "관련", "어떻게", "무엇", "확인", "검색",
    ]);
    const filteredWords = koreanWords.filter(w => !contextStopWords.has(w));
    if (filteredWords.length === 0) return [];

    // 2. 복합어 생성: ["장비", "편성"] → "장비편성"
    const compoundPatterns: string[] = [];
    for (let i = 0; i < filteredWords.length - 1; i++) {
        compoundPatterns.push(filteredWords[i] + filteredWords[i + 1]);
    }
    if (filteredWords.length >= 2) {
        compoundPatterns.push(filteredWords.join(''));
    }

    // 가드: 단일 키워드만 남으면 chunk 검색 skip ("장비"만으로 검색 → 수백 건 노이즈)
    if (filteredWords.length < 2 && compoundPatterns.length === 0) {
        console.log(`[chunkTextFallback] 단일 키워드만 → skip`);
        return [];
    }

    // 3. chunk text에서 ILIKE 검색 (복합어 우선 → 원본 키워드 조합 순)
    const searchPatterns = [
        ...compoundPatterns.map(p => `%${p}%`),
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
            console.log(`[chunkTextFallback] pattern="${pattern}" → ${sectionIds.length}개 섹션: ${sectionIds.join(', ')}`);

            // 4. 매칭된 섹션의 WorkType 조회
            const { data: wtData } = await supabase
                .from("graph_entities")
                .select("id, name, type, properties, source_section")
                .eq("type", "WorkType")
                .in("source_section", sectionIds)
                .limit(15);

            if (wtData && wtData.length > 0) {
                console.log(`[chunkTextFallback] WorkType ${wtData.length}건 반환`);
                return (wtData as any[]).map(e => ({
                    id: e.id, name: e.name, type: e.type,
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
                console.log(`[chunkTextFallback] Section ${sectionEntities.length}건 반환`);
                return (sectionEntities as any[]).map(e => ({
                    id: e.id, name: e.name, type: e.type,
                    properties: e.properties || {},
                    similarity: 0.80,
                    source_section: e.source_section,
                }));
            }
        }
    }

    return [];
}

// ─── WorkType 중복 제거 ───
// Why: 같은 표에서 V형/U형 등 소제목이 분리 추출되어
//      "인 력(인)" vs "인력(인)" 같은 미세 차이로 중복 엔티티 존재.
//      검색 결과에서 normalized_name 기준으로 가장 유사도 높은 것만 유지.
export function deduplicateResults<T extends { name: string; similarity?: number }>(results: T[]): T[] {
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

// C-1. 벡터 검색 + 키워드 폴백
export async function searchEntities(embedding: number[], question: string): Promise<EntityResult[]> {
    const { data, error } = await supabase.rpc("search_entities_by_embedding", {
        query_embedding_text: JSON.stringify(embedding),
        match_count: 5,
        match_threshold: 0.4, // 건설 용어 특수성 고려 threshold 완화
    });

    if (error) {
        console.error("searchEntities error:", error.message);
        return [];
    }

    let entities = (data || []) as EntityResult[];

    // ─── 키워드 폴백: 벡터 검색 결과에 질문의 핵심 규격이 없으면 ILIKE 보완 ───
    // Why: 임베딩 모델이 "200mm"와 "(200,"의 의미적 연결을 잘 못하여
    //       "강관용접(250, SCH 140)"이 "강관용접(200, SCH 40)"보다 높은 유사도 반환
    //       → 규격 숫자 기반 키워드 매칭으로 정확한 엔티티를 폴백 검색
    const specNumbers = extractSpecNumbers(question);
    if (specNumbers.length > 0) {
        // 벡터 결과에 질문의 규격 숫자가 포함된 엔티티가 있는지 확인
        const hasExactMatch = entities.some(e =>
            specNumbers.every(num => e.name.includes(num))
        );

        if (!hasExactMatch) {
            console.log(`[키워드 폴백] 벡터 결과에 ${specNumbers.join(',')} 미포함, ILIKE 폴백 실행`);
            const fallbackEntities = await keywordFallbackSearch(question, specNumbers);
            if (fallbackEntities.length > 0) {
                // 폴백 결과를 최상위에 삽입, 기존 벡터 결과에서 중복 제거
                const fallbackIds = new Set(fallbackEntities.map(e => e.id));
                entities = [
                    ...fallbackEntities,
                    ...entities.filter(e => !fallbackIds.has(e.id)),
                ].slice(0, 5);
            }
        }
    }

    // (Codex F4) search_entities_by_embedding은 source_section 미반환
    // → graph_entities에서 id로 직접 조회하여 source_section 획득
    if (entities.length > 0) {
        const ids = entities.map((e) => e.id);
        const { data: fullEntities } = await supabase
            .from("graph_entities")
            .select("id, source_section")
            .in("id", ids);

        if (fullEntities) {
            const sectionMap = new Map(
                (fullEntities as any[]).map((e: any) => [e.id, e.source_section])
            );
            entities.forEach((e) => {
                e.source_section = sectionMap.get(e.id) || undefined;
            });
        }
    }

    return entities;
}

// ─── E-3. 타겟 검색 (3단계 캐스케이드) ───
// Why: 의도 분석 결과를 활용하여 정확도가 높은 순서대로 검색.
//      벡터 검색은 최후 수단으로만 사용.
export async function targetSearch(
    analysis: IntentAnalysis,
    embedding: number[],
    question: string
): Promise<EntityResult[]> {

    const toEntityResults = (data: any[], similarity: number): EntityResult[] =>
        (data as any[]).map((e: any) => ({
            id: e.id, name: e.name, type: e.type,
            properties: e.properties || {},
            similarity,
            source_section: e.source_section,
        }));

    // 1단계: ILIKE 정확 매칭 (work_name + spec, korean_alias 포함)
    if (analysis.work_name && analysis.spec) {
        const pattern = `%${analysis.work_name}%${analysis.spec}%`;
        const { data } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties, source_section")
            .in("type", ["WorkType", "Section"])
            .or(`name.ilike.${pattern},properties->>"korean_alias".ilike.${pattern}`)
            .limit(5);

        if (data && data.length > 0) {
            console.log(`[targetSearch] 1단계 ILIKE 정확 매칭: ${data.length}건`);
            return toEntityResults(data, 1.0);
        }

        // 1단계 실패 → work_name만으로 재시도 (spec이 엔티티명에 없는 경우)
        // Why: "PE관" → "%PE관%" 매칭 실패 대비, 영한 혼합어 완화 패턴도 추가
        const fallbackPattern = `%${analysis.work_name}%`;
        const mixedPatterns = expandMixedTerms([analysis.work_name]);
        const fallbackOrClauses = [
            `name.ilike.${fallbackPattern}`,
            `properties->>"korean_alias".ilike.${fallbackPattern}`,
            ...mixedPatterns.map(p => `name.ilike.${p}`),
        ].join(",");
        const { data: fallback } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties, source_section")
            .in("type", ["WorkType", "Section"])
            .or(fallbackOrClauses)
            .limit(5);

        if (fallback && fallback.length > 0) {
            console.log(`[targetSearch] 1단계 work_name 폴백: ${fallback.length}건`);
            return toEntityResults(fallback, 0.98);
        }

        // 1단계 약칭 확장 폴백: TIG → TIG(Tungsten Inert Gas) 등
        const abbrExpansions = expandAbbreviations(analysis.work_name);
        if (abbrExpansions.length > 0) {
            const abbrOrClauses = abbrExpansions.map(a => `name.ilike.%${a}%`).join(",");
            const { data: abbrData } = await supabase
                .from("graph_entities")
                .select("id, name, type, properties, source_section")
                .in("type", ["WorkType", "Section", "Standard"])
                .or(abbrOrClauses)
                .limit(5);

            if (abbrData && abbrData.length > 0) {
                console.log(`[targetSearch] 1단계 약칭 확장: ${abbrData.length}건`);
                return toEntityResults(abbrData, 0.96);
            }
        }
    }

    // 2단계: 키워드 기반 ILIKE (korean_alias 포함)
    const searchTerms = analysis.keywords.length > 0
        ? analysis.keywords
        : (analysis.work_name ? [analysis.work_name] : []);

    if (searchTerms.length > 0) {
        // ⭐ ILIKE 검색에는 noise가 적은 키워드만 사용
        //    - 한글 키워드: 항상 포함 (PE관, PE드럼, 폴리에틸렌 등)
        //    - 영문 키워드 ≥ 4자: 포함 (HDPE 등)
        //    - 영문 키워드 ≤ 3자: 제외 (PE → Type, Pipe, Speed 등 noise)
        const dedupTerms = [...new Set(searchTerms.filter(t => t.length >= 2))];
        const ilikeTerms = dedupTerms.filter(t => {
            const isAllEnglish = /^[A-Za-z]+$/.test(t);
            return !isAllEnglish || t.length >= 4;  // 영문만이면 4자 이상만
        });

        if (ilikeTerms.length === 0 && dedupTerms.length > 0) {
            // 전부 짧은 영문 → work_name 폴백
            ilikeTerms.push(...dedupTerms);
        }

        // ─── 영한 혼합어 완화 패턴 추가 ───
        // Why: "PE관" → ILIKE '%PE관%' 은 "가스용 폴리에틸렌(PE)관"에 매칭 안 됨
        //      PE와 관 사이에 ')' 괄호가 있기 때문 → '%PE%관%' 패턴 추가
        const mixedExpansions = expandMixedTerms(ilikeTerms);
        const orClauses = [
            ...ilikeTerms.map(t => `name.ilike.%${t}%`),
            ...mixedExpansions.map(p => `name.ilike.${p}`),
        ].join(",");
        const { data } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties, source_section")
            .in("type", ["WorkType", "Section"])
            .or(orClauses)
            .limit(50);

        if (data && data.length > 0) {
            // 관련도 정렬: 매칭 키워드 수 + 원문 질문어 매칭 보너스
            const questionKorean = question.match(/[가-힣]+/g) || [];
            const scored = data.map((e: any) => {
                let score = 0;
                const nameLower = e.name.toLowerCase();
                // 모든 키워드(짧은 영문 포함) 매칭 카운트
                for (const t of dedupTerms) {
                    if (nameLower.includes(t.toLowerCase())) score += 2;
                }
                // 원문 한글어 매칭 보너스 (예: "관" → "PE관", "폴리에틸렌관" 가중)
                for (const k of questionKorean) {
                    if (nameLower.includes(k)) score += 1;
                }
                return { ...e, _score: score };
            });
            scored.sort((a: any, b: any) => b._score - a._score);
            const top = scored.slice(0, 20);
            console.log(`[targetSearch] 2단계 키워드 매칭: ${data.length}건 → 상위 ${top.length}건 (scores: ${top.slice(0, 5).map((s: any) => s._score).join(',')})`);
            return toEntityResults(top, 0.95);
        }

        // 2단계 실패 → work_name 단독 재시도 (keywords에 규격이 포함되어 못 찾는 경우)
        if (analysis.work_name && searchTerms.length > 1) {
            const wnPattern = `%${analysis.work_name}%`;
            const { data: wnData } = await supabase
                .from("graph_entities")
                .select("id, name, type, properties, source_section")
                .in("type", ["WorkType", "Section"])
                .or(`name.ilike.${wnPattern},properties->>"korean_alias".ilike.${wnPattern}`)
                .limit(20);

            if (wnData && wnData.length > 0) {
                console.log(`[targetSearch] 2단계 work_name 폴백: ${wnData.length}건`);
                return toEntityResults(wnData, 0.90);
            }
        }
    }

    // 3단계: 벡터 검색 (타입 필터 적용 — Note/Equipment 제외)
    const { data, error } = await supabase.rpc("search_entities_typed", {
        query_embedding_text: JSON.stringify(embedding),
        match_count: 5,
        match_threshold: 0.4,
        type_filter: ["Section", "WorkType"],
    });

    if (error) {
        console.error("[targetSearch] 벡터 검색 에러:", error.message);
        return searchEntities(embedding, question);
    }

    console.log(`[targetSearch] 3단계 벡터 검색: ${(data || []).length}건`);
    const vectorResults = (data || []) as EntityResult[];

    // 4단계: chunk text fallback (Layer 4)
    // Why: 벡터 결과가 Section만이거나 WorkType 유사도가 낮을 때,
    //      chunk 본문에만 존재하는 용어("장비편성", "인력편성" 등)를 검색
    const hasGoodMatch = vectorResults.some(
        e => e.type === 'WorkType' && e.similarity >= 0.7
    );
    if (!hasGoodMatch) {
        console.log(`[targetSearch] 4단계 chunk text fallback 시도 (WorkType+sim≥0.7 없음)`);
        const chunkResults = await chunkTextFallbackSearch(question);
        if (chunkResults.length > 0) {
            console.log(`[targetSearch] 4단계 chunk text: ${chunkResults.length}건 → 벡터 결과와 병합`);
            const chunkIds = new Set(chunkResults.map(e => e.id));
            return deduplicateResults([
                ...chunkResults,
                ...vectorResults.filter(e => !chunkIds.has(e.id)),
            ]);
        }
    }

    return deduplicateResults(vectorResults);
}

// ─── 검색어 정제 유틸리티 ───
// Why: LLM이 work_name과 중복되는 키워드를 생성할 수 있으므로,
//      work_name에 포함된 키워드를 제거하여 ILIKE 패턴 오염 방지
export function buildSearchTerms(work_name: string | null, keywords: string[]): string[] {
    if (!work_name) return keywords;
    const nameLower = work_name.toLowerCase();
    return keywords.filter(kw => {
        const kwLower = kw.toLowerCase();
        // work_name에 키워드가 포함되어 있으면 제거
        if (nameLower.includes(kwLower)) return false;
        // 키워드에 work_name이 포함되어 있으면 제거
        if (kwLower.includes(nameLower)) return false;
        return true;
    });
}
