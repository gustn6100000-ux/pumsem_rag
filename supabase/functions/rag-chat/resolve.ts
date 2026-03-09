// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// resolve.ts — 계층 탐색 + 명확화 프레젠테이션
// Why: graphClarify의 656줄 모놀리식 함수를 책임 분리
//   resolveSection : DB 탐색 → ResolveResult 반환
//   presentClarify : ResolveResult → ClarifyResult UI 변환
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import { supabase } from "./config.ts";
import { chunkTextFallbackSearch, expandMixedTerms, expandDomainSynonyms } from "./search.ts";
import type {
    IntentAnalysis, ClarifyOption, ClarifyResult,
    SelectorPanel, SelectorItem, FilterAxis,
} from "./types.ts";

// ─── ResolveContext: 상태 보존 + 의존성 주입 ───
// Why: 파라미터 파편화 방지. 향후 필터 조건 추가 시 함수 서명 변경 불필요
export interface ResolveContext {
    analysis: IntentAnalysis;
    sectionId?: string;
    subSectionName?: string;       // sub_section 드릴다운 상태 보존
    preMatchedSections?: any[];    // searchPipeline 벡터 검색 결과 주입 (DB 이중 쿼리 방지)
}

// ─── ChunkMeta: graph_chunks 메타데이터 ───
export interface ChunkMeta {
    department: string;
    chapter: string;
    title: string;
}

// ─── ResolveResult: resolveSection의 출력 ───
export interface ResolveResult {
    level: 'multi_section' | 'single_section' | 'sub_section' | 'worktype_many' | 'worktype_few' | 'empty';
    sections: any[];
    workTypes: any[];
    subSections?: Map<string, any[]>;   // sub_section 그룹 (drill-down용)
    chunkMeta: Map<string, ChunkMeta>;  // source_section → 부문/장/절
    sectionPath?: string;               // 단일 섹션의 경로 문자열
    sectionName?: string;               // 단일 섹션의 이름
    primarySectionId?: string;          // 주 섹션 ID
    chunkTextResults: any[];            // 전략 4 chunk text 결과
    sectionSourceSections: Set<string>; // 전략 1에서 찾은 source_section 집합
    childSections: any[];               // 하위 절 목록
    subFilter?: string | null;          // sub_section 필터
}

// ═══════════════════════════════════════════════════════
// ─── 헬퍼: # 접미사 제거 ───
// ═══════════════════════════════════════════════════════
function displayCode(code: string | null | undefined): string {
    return code ? code.replace(/#.*$/, '') : '';
}

// ─── 범용 동사 목록 (전략 3 독립검색에서 제외) ───
const ACTION_VERBS = new Set([
    "제작", "설치", "시공", "공사", "운반", "보수", "해체", "조립",
    "철거", "가공", "타설", "양생", "포설", "다짐", "절단", "용접",
    "도장", "배관", "배선", "측량", "검사", "인양", "적재",
]);

// ═══════════════════════════════════════════════════════
// resolveSection: DB 탐색 → 계층 판정 → ResolveResult
// ═══════════════════════════════════════════════════════
export async function resolveSection(ctx: ResolveContext): Promise<ResolveResult> {
    const { analysis, sectionId, subSectionName, preMatchedSections } = ctx;
    const { work_name, keywords } = analysis;
    let searchTerms = work_name ? [work_name, ...keywords] : keywords;

    // ─── searchTerms[0] 정규화 ───
    if (searchTerms.length > 0 && searchTerms[0].length > 0) {
        const raw = searchTerms[0];
        const koreanTokens = [...new Set(raw.match(/[가-힣]{2,}/g) || [])];
        if (koreanTokens.length > 0) {
            searchTerms[0] = koreanTokens.join('');
        }
        if (searchTerms[0].length > 15 || !/[가-힣]/.test(searchTerms[0])) {
            const originalQuery = analysis.ambiguity_reason || work_name || '';
            const fallbackTokens = [...new Set(originalQuery.match(/[가-힣]{2,}/g) || [])];
            if (fallbackTokens.length > 0) searchTerms[0] = fallbackTokens.join('');
        }
        console.log(`[resolveSection] searchTerms 정규화: "${raw}" → "${searchTerms[0]}"`);
    }

    if (searchTerms.length === 0) {
        return emptyResult();
    }

    // ═══ sectionId 경로: 해당 섹션 내 탐색 ═══
    if (sectionId) {
        return await resolveBySectionId(sectionId, subSectionName, searchTerms);
    }

    // ═══ 검색 경로: 4전략 실행 ═══
    return await resolveBySearch(analysis, searchTerms, keywords, work_name, preMatchedSections);
}

// ─── sectionId 기반 탐색 (기존 Step 2) ───
async function resolveBySectionId(
    sectionId: string,
    subSectionName?: string,
    searchTerms: string[] = []
): Promise<ResolveResult> {
    // sub_section 필터 추출: "13-2-3:sub=2. TIG용접" → sectionId=13-2-3, subFilter="2. TIG용접"
    let actualSectionId = sectionId;
    let subFilter: string | null = subSectionName || null;
    if (sectionId.includes(':sub=')) {
        const parts = sectionId.split(':sub=');
        actualSectionId = parts[0];
        subFilter = decodeURIComponent(parts[1]);
    }

    console.log(`[resolveSection] sectionId=${actualSectionId}, subFilter=${subFilter}`);

    // graph_chunks 메타데이터 조회
    const { data: chunkData } = await supabase
        .from("graph_chunks")
        .select("section_id, department, chapter, title, text")
        .eq("section_id", actualSectionId)
        .limit(1);

    const chunk = (chunkData as any[])?.[0];
    const sectionPath = chunk
        ? `${chunk.department} > ${chunk.chapter} > ${chunk.title}`
        : actualSectionId;
    const chunkMeta = new Map<string, ChunkMeta>();
    if (chunk) {
        chunkMeta.set(actualSectionId, {
            department: chunk.department || "",
            chapter: chunk.chapter || "",
            title: chunk.title || "",
        });
    }

    // 하위 WorkType 조회
    const { data: exactWTs } = await supabase
        .from("graph_entities")
        .select("id, name, type, source_section, properties")
        .eq("type", "WorkType")
        .eq("source_section", actualSectionId)
        .limit(200);

    let workTypes = (exactWTs || []) as any[];
    console.log(`[resolveSection] exact=${workTypes.length}개 WorkType`);

    // sub_section drill-down 판정
    let subSections: Map<string, any[]> | undefined;
    if (workTypes.length > 0 && !subFilter) {
        const subMap = buildSubSectionMap(workTypes);
        if (subMap.size >= 2) {
            subSections = subMap;
            return {
                level: 'sub_section',
                sections: [],
                workTypes,
                subSections,
                chunkMeta,
                sectionPath,
                sectionName: chunk?.title || actualSectionId,
                primarySectionId: actualSectionId,
                chunkTextResults: [],
                sectionSourceSections: new Set([actualSectionId]),
                childSections: [],
                subFilter: null,
            };
        }
    }

    // sub_section 필터 적용
    if (subFilter && workTypes.length > 0) {
        const beforeCount = workTypes.length;
        workTypes = workTypes.filter((wt: any) => wt.properties?.sub_section === subFilter);
        console.log(`[resolveSection] subFilter="${subFilter}" → ${beforeCount} → ${workTypes.length}개`);
    }

    // WT 0건 → 하위 절(children) 탐색
    let childSections: any[] = [];
    if (workTypes.length === 0 && !subFilter) {
        const baseSectionId = actualSectionId.includes('#') ? actualSectionId.split('#')[0] : actualSectionId;
        const childPrefix = baseSectionId + '-';
        const dept = chunk?.department || '';

        console.log(`[resolveSection] WT 0건 → 하위 절 탐색 (prefix=${childPrefix})`);

        const { data: childChunks } = await supabase
            .from("graph_chunks")
            .select("section_id, title, department")
            .ilike("section_id", `${childPrefix}%`)
            .eq("department", dept);

        const uniqueChildren = new Map<string, any>();
        (childChunks || []).forEach((c: any) => {
            if (!uniqueChildren.has(c.section_id)) {
                uniqueChildren.set(c.section_id, c);
            }
        });
        childSections = Array.from(uniqueChildren.values());

        if (childSections.length > 0) {
            const childSectionIds = childSections.map(c => c.section_id);
            const { data: childWTs } = await supabase
                .from("graph_entities")
                .select("id, name, type, source_section, properties")
                .eq("type", "WorkType")
                .in("source_section", childSectionIds)
                .limit(50);
            workTypes = (childWTs || []) as any[];
        }
    }

    // 이름 정규화 기준 중복 제거 (클린 DB로 인해 삭제됨)

    // WT 0건: Note 수 조회하여 level 판정
    if (workTypes.length === 0 && childSections.length === 0) {
        const { count: noteCount } = await supabase
            .from("graph_entities")
            .select("id", { count: "exact", head: true })
            .eq("type", "Note")
            .eq("source_section", actualSectionId);

        // empty이지만 note가 있을 수 있으므로 workTypes에 noteCount 정보를 meta로 전달
        return {
            level: 'empty',
            sections: [],
            workTypes: [],
            chunkMeta,
            sectionPath,
            sectionName: chunk?.title || actualSectionId,
            primarySectionId: actualSectionId,
            chunkTextResults: [],
            sectionSourceSections: new Set([actualSectionId]),
            childSections: [],
            subFilter,
            // noteCount를 sections 필드로 전달 (임시)
            ...(noteCount ? { sections: [{ _noteCount: noteCount }] } : {}),
        };
    }

    const level = workTypes.length > 3 ? 'worktype_many' : 'worktype_few';

    return {
        level,
        sections: [],
        workTypes,
        chunkMeta,
        sectionPath,
        sectionName: chunk?.title || actualSectionId,
        primarySectionId: actualSectionId,
        chunkTextResults: [],
        sectionSourceSections: new Set([actualSectionId]),
        childSections,
        subFilter,
    };
}

// ─── 검색 전략 기반 탐색 (기존 Step 1) ───
async function resolveBySearch(
    analysis: IntentAnalysis,
    searchTerms: string[],
    keywords: string[],
    work_name: string | null,
    preMatchedSections?: any[]
): Promise<ResolveResult> {
    const sectionSourceSections = new Set<string>();

    // ─── 전략 1: Section 레벨 탐색 ───
    let effectiveSections: any[] = [];

    if (preMatchedSections && preMatchedSections.length > 0) {
        // ✅ preMatchedSections 주입됨 → DB 이중 쿼리 방지
        effectiveSections = preMatchedSections;
        console.log(`[resolveSection] 전략 1: preMatched ${effectiveSections.length}개 사용`);
    } else {
        // 전략 1-A: Section 이름 ILIKE (+ 도메인 동의어)
        const sectionPattern = "%" + searchTerms[0] + "%";
        // 💡 [Track B-1] 도메인 동의어로 Section 검색 확장 (raw work_name 사용)
        const synonymSrc1A = work_name ? [work_name, searchTerms[0]] : [searchTerms[0]];
        const sectionSynonyms = expandDomainSynonyms([...new Set(synonymSrc1A)]);
        const sectionOrClauses = [
            `name.ilike.${sectionPattern}`,
            ...sectionSynonyms.map(s => `name.ilike.%${s}%`),
        ].join(",");
        const { data: sections } = await supabase
            .from("graph_entities")
            .select("id, name, type, source_section, properties")
            .eq("type", "Section")
            .or(sectionOrClauses)
            .limit(10);

        // 전략 1-B: 토큰 분리 ILIKE 폴백
        let tokenFallbackSections: any[] = [];
        if ((!sections || sections.length === 0) && searchTerms[0].length >= 4) {
            let tokens = searchTerms[0].match(/[가-힣]{2,}|[a-zA-Z]+/g) || [];
            if (tokens.length === 1 && tokens[0].length >= 4) {
                const word = tokens[0];
                const halfLen = Math.ceil(word.length / 2);
                tokens = [word.substring(0, halfLen), word.substring(halfLen)];
            }
            if (tokens.length >= 2) {
                let query = supabase.from("graph_entities")
                    .select("id, name, type, source_section, properties")
                    .eq("type", "Section");
                for (const token of tokens) {
                    query = query.ilike("name", `%${token}%`);
                }
                const { data: tokenSections } = await query.limit(10);
                if (tokenSections) tokenFallbackSections = tokenSections;
                console.log(`[resolveSection] 전략 1-B: "${tokens.join('","')}" → ${tokenFallbackSections.length}건`);
            }
        }
        effectiveSections = (sections && sections.length > 0) ? sections : tokenFallbackSections;
    }

    // Section의 source_section으로 하위 WorkType 조회
    let sectionChildWorkTypes: any[] = [];
    if (effectiveSections.length > 0) {
        const sourceSections = effectiveSections.map((s: any) => s.source_section).filter(Boolean);
        sourceSections.forEach((ss: string) => sectionSourceSections.add(ss));
        if (sourceSections.length > 0) {
            const { data: childWTs } = await supabase
                .from("graph_entities")
                .select("id, name, type, source_section, properties")
                .eq("type", "WorkType")
                .in("source_section", sourceSections)
                .limit(200);
            if (childWTs) sectionChildWorkTypes = childWTs;
            console.log(`[resolveSection] Section ${sourceSections.join(",")} 하위 WorkType ${childWTs?.length || 0}개`);
        }
    }

    // 전략 2: WorkType 직접 탐색
    const safeWorkTerms = searchTerms.filter((t: string) => {
        const isAllEng = /^[A-Za-z]+$/.test(t);
        return t.length >= 2 && (!isAllEng || t.length >= 4);
    });
    const wTerms = safeWorkTerms.length > 0 ? safeWorkTerms : searchTerms.filter((t: string) => t.length >= 2);
    // Why: "PE관" → "%PE관%" 매칭 실패 대비, 영한 혼합어 완화 패턴("%PE%관%")도 추가
    const mixedExp = expandMixedTerms(wTerms);
    // 💡 [Track B-1] 도메인 동의어로 WorkType 검색 확장
    // Why: searchTerms[0]이 한글 정규화("PE관"→"관")되어 원본이 사라질 수 있으므로
    //      raw work_name도 동의어 확장 소스에 포함
    const synonymSource = work_name ? [...new Set([work_name, ...wTerms])] : wTerms;
    const domainExp = expandDomainSynonyms(synonymSource);
    console.log(`[resolveSection] 전략 2: wTerms=${JSON.stringify(wTerms)}, synonymSource=${JSON.stringify(synonymSource)}, domainExp=${JSON.stringify(domainExp)}`);
    const workOrClauses = [
        ...wTerms.map((t: string) => `name.ilike.%${t}%`),
        ...mixedExp.map(p => `name.ilike.${p}`),
        ...domainExp.map(s => `name.ilike.%${s}%`),
    ].join(",");
    const { data: workTypes } = await supabase
        .from("graph_entities")
        .select("id, name, type, source_section, properties")
        .eq("type", "WorkType")
        .or(workOrClauses)
        .limit(200);

    // 전략 3: 키워드별 독립 검색 (범용 동사 제외)
    let extraWorkTypes: any[] = [];
    for (const kw of keywords) {
        if (kw.length >= 2 && !ACTION_VERBS.has(kw)) {
            const { data: kwResults } = await supabase
                .from("graph_entities")
                .select("id, name, type, source_section, properties")
                .in("type", ["WorkType", "Section"])
                .or(`name.ilike.%${kw}%,properties->>korean_alias.ilike.%${kw}%`)
                .limit(10);
            if (kwResults) extraWorkTypes = extraWorkTypes.concat(kwResults);
        }
    }

    // 전략 4: chunk 본문 텍스트 검색
    let chunkTextResults: any[] = [];
    const prelimResults = [...effectiveSections, ...sectionChildWorkTypes, ...(workTypes || []), ...extraWorkTypes];
    const kwTokens = keywords.length > 0
        ? keywords
        : (work_name ? work_name.split(/\s+/).filter((w: string) => w.length >= 2) : []);
    const compoundTerms: string[] = [];
    for (let i = 0; i < kwTokens.length - 1; i++) {
        compoundTerms.push(kwTokens[i] + kwTokens[i + 1]);
    }
    if (kwTokens.length >= 2) {
        compoundTerms.push(kwTokens.join(''));
    }
    const compoundMatchFound = compoundTerms.length > 0 && prelimResults.some(
        (r: any) => compoundTerms.some(ct => r.name && r.name.includes(ct))
    );

    if (compoundTerms.length > 0 && !compoundMatchFound) {
        console.log(`[resolveSection] 전략 4: chunk text fallback (복합어 "${compoundTerms.join(',')}" 미매칭)`);
        const chunkQuestion = searchTerms.join(' ');
        const chunkFallback = await chunkTextFallbackSearch(chunkQuestion);
        if (chunkFallback.length > 0) {
            chunkTextResults = chunkFallback.map(e => ({
                id: e.id, name: e.name, type: e.type,
                source_section: e.source_section,
                properties: e.properties,
            }));
        }
    }

    // ─── 결과 병합 + 중복 제거 ───
    const allResults = [...effectiveSections, ...sectionChildWorkTypes, ...(workTypes || []), ...extraWorkTypes, ...chunkTextResults];
    const uniqueResults = Array.from(
        new Map(allResults.map(r => [r.id, r])).values()
    );

    if (uniqueResults.length === 0) {
        return emptyResult();
    }

    // ─── graph_chunks 메타데이터 조회 ───
    const allSourceSections = [...new Set(uniqueResults.map(r => r.source_section).filter(Boolean))];
    const chunkMeta = new Map<string, ChunkMeta>();
    if (allSourceSections.length > 0) {
        const { data: chunks } = await supabase
            .from("graph_chunks")
            .select("section_id, department, chapter, title")
            .in("section_id", allSourceSections);
        if (chunks) {
            for (const c of chunks as any[]) {
                chunkMeta.set(c.section_id, {
                    department: c.department || "",
                    chapter: c.chapter || "",
                    title: c.title || "",
                });
            }
        }
    }

    // ─── 관련성 점수 산출 ───
    // Why: 고아 섹션(chunk 없음, WorkType 없음)이 상위에 노출되면
    //      사용자가 선택해도 "원문 없음"이 나오므로, 대폭 감점(-100)하여 후순위로 밀어냄
    const scoredResults = uniqueResults.map(r => {
        let score = 0;
        const name = r.name || "";
        const nameLC = name.toLowerCase();

        if (r.type === "WorkType" && sectionSourceSections.has(r.source_section)) score += 50;
        if (work_name && nameLC.includes(work_name.toLowerCase())) score += 30;
        for (const kw of keywords) {
            if (nameLC.includes(kw.toLowerCase())) score += 10;
        }
        if (r.type === "Section") score -= 5;

        // 💡 [고아 섹션 감점] chunkMeta에 없는 Section = chunk 없음 → 대폭 감점
        if (r.type === "Section" && r.source_section && !chunkMeta.has(r.source_section)) {
            score -= 100;
            console.log(`[resolveSection] 고아 섹션 감점: ${r.name} (${r.source_section})`);
        }

        return { ...r, _score: score };
    });
    scoredResults.sort((a, b) => b._score - a._score);

    console.log(`[resolveSection] 관련성 상위:`,
        scoredResults.slice(0, 5).map(r => `${r.name}(${r._score})`).join(", "));

    // ─── 계층 판정 ───
    const matchedSections = scoredResults.filter(r => r.type === "Section");
    const matchedWorkTypes = scoredResults.filter(r => r.type === "WorkType");

    // Phase 3-C: chunk text fallback WorkType 우선
    const chunkWorkTypes = chunkTextResults.filter((r: any) => r.type === 'WorkType');
    if (chunkWorkTypes.length > 0) {
        // sub_section drill-down 시도
        const allWTsForDrill = sectionChildWorkTypes.length > 0 ? sectionChildWorkTypes : chunkWorkTypes;
        const drillSectionId = matchedSections[0]?.source_section || chunkWorkTypes[0]?.source_section || '';
        const subMap = buildSubSectionMap(allWTsForDrill);

        const drillSectionName = matchedSections[0]?.name || work_name || searchTerms[0];
        const drillMeta = drillSectionId ? chunkMeta.get(drillSectionId) : null;
        const drillSectionPath = drillMeta
            ? `${drillMeta.department} > ${drillMeta.chapter} > ${drillMeta.title}`
            : drillSectionName;

        if (subMap.size >= 2) {
            return {
                level: 'sub_section',
                sections: matchedSections,
                workTypes: allWTsForDrill,
                subSections: subMap,
                chunkMeta,
                sectionPath: drillSectionPath,
                sectionName: drillSectionName,
                primarySectionId: drillSectionId,
                chunkTextResults,
                sectionSourceSections,
                childSections: [],
            };
        }

        // sub_section 없으면 chunk WorkType을 그대로 반환
        return {
            level: 'worktype_few',
            sections: matchedSections,
            workTypes: chunkWorkTypes,
            chunkMeta,
            sectionPath: drillSectionPath,
            sectionName: drillSectionName,
            primarySectionId: drillSectionId,
            chunkTextResults,
            sectionSourceSections,
            childSections: [],
        };
    }

    // 복수 섹션 판정 (💡 [Track B-1] WorkType의 source_section도 고려)
    // 💡 [고아 섹션 필터] chunkMeta에 없는(=chunk가 0건) 섹션 ID를 제외
    const sectionOnlyIds = [...new Set(matchedSections.map(s => s.source_section).filter(Boolean))];
    const workTypeOnlyIds = [...new Set(matchedWorkTypes.map(w => w.source_section).filter(Boolean))];
    const allUniqueSectionIds = [...new Set([...sectionOnlyIds, ...workTypeOnlyIds])]
        .filter(sid => chunkMeta.has(sid));  // 고아 섹션 제거
    if (allUniqueSectionIds.length > 1) {
        // 실데이터가 있는 섹션의 Section/WorkType만 전달
        const validSectionSet = new Set(allUniqueSectionIds);
        return {
            level: 'multi_section',
            sections: matchedSections.filter(s => validSectionSet.has(s.source_section)),
            workTypes: matchedWorkTypes.filter(w => validSectionSet.has(w.source_section)),
            chunkMeta,
            chunkTextResults,
            sectionSourceSections,
            childSections: [],
        };
    }

    // 단일 섹션 + WorkType 많음
    if (matchedWorkTypes.length > 3) {
        const sectionNameA = matchedSections[0]?.name || work_name || searchTerms[0];
        const sectionMetaA = matchedSections[0] ? chunkMeta.get(matchedSections[0].source_section) : null;
        const fullSectionPathA = sectionMetaA
            ? `${sectionMetaA.department} > ${sectionMetaA.chapter} > ${sectionMetaA.title}`
            : sectionNameA;
        const primarySectionIdA = matchedSections[0]?.source_section || matchedWorkTypes[0]?.source_section || '';

        // sub_section drill-down 시도
        const subMap = buildSubSectionMap(matchedWorkTypes);
        if (subMap.size >= 2) {
            return {
                level: 'sub_section',
                sections: matchedSections,
                workTypes: matchedWorkTypes,
                subSections: subMap,
                chunkMeta,
                sectionPath: fullSectionPathA,
                sectionName: sectionNameA,
                primarySectionId: primarySectionIdA,
                chunkTextResults,
                sectionSourceSections,
                childSections: [],
            };
        }

        return {
            level: 'worktype_many',
            sections: matchedSections,
            workTypes: matchedWorkTypes,
            chunkMeta,
            sectionPath: fullSectionPathA,
            sectionName: sectionNameA,
            primarySectionId: primarySectionIdA,
            chunkTextResults,
            sectionSourceSections,
            childSections: [],
        };
    }

    // Section 1개 + WorkType 소수
    if (matchedSections.length === 1 && matchedWorkTypes.length > 0) {
        const section = matchedSections[0];
        const meta = chunkMeta.get(section.source_section);
        const sectionPath = meta
            ? `${meta.department} > ${meta.chapter} > ${meta.title}`
            : section.name;

        return {
            level: 'worktype_few',
            sections: matchedSections,
            workTypes: matchedWorkTypes,
            chunkMeta,
            sectionPath,
            sectionName: section.name,
            primarySectionId: section.source_section || matchedWorkTypes[0]?.source_section || '',
            chunkTextResults,
            sectionSourceSections,
            childSections: [],
        };
    }

    // 소수 결과 (Section + WorkType 혼합)
    return {
        level: 'worktype_few',
        sections: matchedSections,
        workTypes: scoredResults, // 전체 scored 결과
        chunkMeta,
        chunkTextResults,
        sectionSourceSections,
        childSections: [],
    };
}

// ═══════════════════════════════════════════════════════
// presentClarify: ResolveResult → ClarifyResult (UI 변환)
// ═══════════════════════════════════════════════════════
export function presentClarify(
    resolved: ResolveResult,
    searchTerms: string[],
    workName: string | null
): ClarifyResult {
    const { level, sections, workTypes, subSections, chunkMeta,
        sectionPath, sectionName, primarySectionId,
        childSections, subFilter } = resolved;

    // ─── label 생성 헬퍼 ───
    const makeLabel = (r: any): string => {
        const meta = chunkMeta.get(r.source_section);
        if (meta && meta.department) {
            const dept = meta.department.replace(/부문$/, "");
            const secTag = r.source_section ? ` (${displayCode(r.source_section)})` : "";
            return `[${dept}${secTag}] ${r.name}`;
        }
        const sectionTag = r.source_section ? `[${displayCode(r.source_section)}]` : "";
        return `${sectionTag} ${r.name}`;
    };

    // ─── empty ───
    if (level === 'empty') {
        const noteCount = sections[0]?._noteCount || 0;
        const options: ClarifyOption[] = [{
            label: `📋 ${sectionName || primarySectionId} 전체 내용 보기`,
            query: `${sectionName || primarySectionId} 전체 품셈`,
            section_id: primarySectionId,
            option_type: "full_view",
        }];

        const message = noteCount > 0
            ? `**${sectionPath}** 품셈은 개별 작업이 분류되어 있지 않고, **기준 및 주의사항 ${noteCount}건**을 포함하고 있습니다.\n아래 "전체 내용 보기"를 통해 확인해 주세요.`
            : `**${sectionPath}** 품셈의 상세 작업이 개별 등록되어 있지 않습니다.\n아래 "전체 내용 보기" 버튼으로 해당 절의 품셈 데이터를 확인해 주세요.`;

        return { message, options };
    }

    // ─── sub_section drill-down ───
    if (level === 'sub_section' && subSections) {
        const options: ClarifyOption[] = [];
        const prefix = sectionName || workName || searchTerms[0];

        options.push({
            label: `📋 ${sectionName || primarySectionId} 전체 내용 보기`,
            query: `${prefix} 전체 품셈`,
            section_id: primarySectionId,
            option_type: "full_view",
        });

        // sub_section별 옵션 (sub_section_no 순 정렬)
        const sorted = [...subSections.entries()].sort((a, b) => {
            const noA = a[1][0]?.properties?.sub_section_no || 99;
            const noB = b[1][0]?.properties?.sub_section_no || 99;
            return Number(noA) - Number(noB);
        });

        for (const [subName, subWTs] of sorted) {
            options.push({
                label: `📂 ${subName} (${subWTs.length}건)`,
                query: `${prefix} ${subName} 품셈`,
                section_id: `${primarySectionId}:sub=${encodeURIComponent(subName)}`,
                option_type: "section" as any,
            });
        }

        return {
            message: `**${sectionPath}** 품셈에는 ${subSections.size}개 분류(총 ${workTypes.length}개 작업)가 있습니다.\n분류를 선택해 주세요.`,
            options,
        };
    }

    // ─── multi_section ───
    if (level === 'multi_section') {
        // 💡 [Track B-1] Section 엔티티 + WorkType의 source_section 병합
        const sectionSrcSet = new Set(sections.map(s => s.source_section).filter(Boolean));
        // 💡 [고아 섹션 필터] chunkMeta에 없는 Section은 옵션에서 제외
        const validSections = sections.filter(s => s.source_section && chunkMeta.has(s.source_section));
        const options: ClarifyOption[] = validSections.slice(0, 10).map(s => {
            const meta = chunkMeta.get(s.source_section);
            const secTag = s.source_section ? ` (${displayCode(s.source_section)})` : "";
            const label = meta
                ? `${meta.department} > ${meta.chapter} > ${meta.title}${secTag}`
                : `[${displayCode(s.source_section)}] ${s.name}`;
            return {
                label,
                query: `${s.name} 품셈`,
                source_section: s.source_section,
                section_id: s.source_section,
                option_type: 'section' as const,
            };
        });

        // WorkType의 source_section 중 Section에 없는 것들도 option으로 추가
        const wtBySrc = new Map<string, any>();
        for (const wt of workTypes) {
            if (wt.source_section && !sectionSrcSet.has(wt.source_section) && !wtBySrc.has(wt.source_section)) {
                wtBySrc.set(wt.source_section, wt);
            }
        }
        for (const [srcSec, wt] of wtBySrc) {
            // 💡 [고아 섹션 필터] chunkMeta에 없는 WorkType 소스 섹션도 제외
            if (!chunkMeta.has(srcSec)) continue;
            const meta = chunkMeta.get(srcSec);
            const secTag = ` (${displayCode(srcSec)})`;
            const label = meta
                ? `${meta.department} > ${meta.chapter} > ${meta.title}${secTag}`
                : `[${displayCode(srcSec)}] ${wt.name}`;
            options.push({
                label,
                query: `${meta?.title || wt.name} 품셈`,
                source_section: srcSec,
                section_id: srcSec,
                option_type: 'section' as const,
            });
        }

        const allUniqueIds = [...new Set([...sections.map(s => s.source_section), ...workTypes.map(w => w.source_section)].filter(Boolean))]
            .filter(sid => chunkMeta.has(sid));  // 고아 섹션 제외
        const selector = buildSelectorPanel(options, searchTerms[0]);
        return {
            message: `"${searchTerms.join(" ")}" 관련 품셈이 **${allUniqueIds.length}개 분야**에 있습니다.\n어떤 분야의 품셈을 찾으시나요?`,
            options,
            selector,
        };
    }

    // ─── sectionId 경로: worktype_many / worktype_few ───
    if (primarySectionId && childSections.length >= 0) {
        const options: ClarifyOption[] = [];

        // "전체 내용 보기" 옵션
        if (primarySectionId) {
            options.push({
                label: `📋 ${sectionName || primarySectionId}${subFilter ? ` > ${subFilter}` : ''} 전체 내용 보기`,
                query: `${sectionName || primarySectionId} 전체 품셈`,
                section_id: primarySectionId,
                option_type: "full_view",
            });
        }

        if (childSections.length > 0 && workTypes.length > 10) {
            // 하위 절 단위 옵션
            for (const child of childSections) {
                options.push({
                    label: `📂 ${child.title}`,
                    query: `${child.title} 품셈`,
                    section_id: child.section_id,
                    option_type: "section" as any,
                });
            }
        } else {
            // 개별 WorkType 옵션
            for (const wt of workTypes) {
                if (options.find(o => o.entity_id === wt.id)) continue;
                options.push({
                    label: (level === 'worktype_many' || !sections.length) ? makeLabel(wt) : wt.name,
                    query: `${wt.name} 품셈`,
                    entity_id: wt.id,
                    source_section: wt.source_section,
                    option_type: (wt.type === 'Section' ? 'section' : 'worktype') as 'section' | 'worktype',
                    ...(wt.type === 'Section' ? { section_id: wt.source_section } : {}),
                });
            }
        }

        // 메시지 분기
        let message: string;
        if (subFilter) {
            message = `**${sectionPath} > ${subFilter}** 품셈은 ${workTypes.length}개 작업으로 분류되어 있습니다.\n어떤 작업의 품셈을 찾으시나요?`;
        } else if (level === 'worktype_many') {
            message = `**${sectionPath || sectionName}** 품셈은 ${workTypes.length}개 작업으로 분류되어 있습니다.\n어떤 작업의 품셈을 찾으시나요?`;
        } else if (sections.length === 1 && workTypes.length > 0) {
            message = `**${sectionPath || sectionName}** 하위 ${workTypes.length}개 작업이 있습니다.\n어떤 작업의 품셈을 찾으시나요?`;
        } else if (workTypes.length > 0) {
            message = `다음 중 찾으시는 항목이 있나요?`;
        } else {
            message = `"${searchTerms.join(" ")}"와 관련된 품셈 항목을 찾지 못했습니다.\n정확한 공종명을 입력해 주세요.`;
        }

        const selector = buildSelectorPanel(options, workName || searchTerms[0]);
        return {
            message,
            options,
            ...(selector ? { selector } : {}),
        };
    }

    // ─── 최종 폴백 ───
    return {
        message: `"${searchTerms.join(" ")}"와 관련된 품셈 항목을 찾지 못했습니다.\n정확한 공종명을 입력해 주세요.`,
        options: [],
    };
}

// ═══════════════════════════════════════════════════════
// 유틸리티 함수
// ═══════════════════════════════════════════════════════

function emptyResult(): ResolveResult {
    return {
        level: 'empty',
        sections: [],
        workTypes: [],
        chunkMeta: new Map(),
        chunkTextResults: [],
        sectionSourceSections: new Set(),
        childSections: [],
    };
}

// sub_section별 그룹 생성
function buildSubSectionMap(workTypes: any[]): Map<string, any[]> {
    const subMap = new Map<string, any[]>();
    for (const wt of workTypes) {
        const sub = wt.properties?.sub_section || null;
        if (sub) {
            if (!subMap.has(sub)) subMap.set(sub, []);
            subMap.get(sub)!.push(wt);
        }
    }
    return subMap;
}


// ─── Selector Panel 관련 함수 (clarify.ts에서 이동) ───

function parseWorkTypeName(name: string): Record<string, string> {
    // 1. 강관 (옥외 용접식) 형식을 식별 (ComplexTablePipeline 용)
    const mComplex = name.match(/^([^()]+)\s*\(([^)\s]+)\s+([^)\s]+)\)$/);
    if (mComplex) {
        return {
            '재질': mComplex[1].trim(),
            '배관장소': mComplex[2].trim(),
            '접합방식': mComplex[3].trim()
        };
    }

    // 2. (XX, SCH YY) 형식 식별
    const m = name.match(/\((\d+),\s*SCH\s*([\d~]+)\)$/);
    if (m) return { diameter: m[1], sch: m[2] };

    // 3. (A, B) 형식
    const m2 = name.match(/\(([^,]+),\s*(.+)\)$/);
    if (m2) return { spec1: m2[1].trim(), spec2: m2[2].trim() };

    // 4. (A) 단일 형식
    const m3 = name.match(/\(([^)]+)\)$/);
    if (m3) {
        const val = m3[1].trim();
        // Ignore section IDs like 1-2-4 or 1-2
        if (!/^(\d+-)+\d+(#\d+)?$/.test(val)) {
            return { spec1: val };
        }
    }

    // 5. 언더스코어(_) 로 구분된 서브타입
    const parts = name.split('_');
    if (parts.length >= 2) return { subtype: parts.slice(1).join('_') };

    return {};
}

function extractFilterAxes(items: SelectorItem[]): FilterAxis[] {
    const axisMap = new Map<string, Set<string>>();
    for (const item of items) {
        for (const [key, val] of Object.entries(item.specs)) {
            if (!axisMap.has(key)) axisMap.set(key, new Set());
            axisMap.get(key)!.add(val);
        }
    }

    function extractNumber(s: string): number {
        const m = s.match(/[\d.]+/);
        return m ? parseFloat(m[0]) : NaN;
    }

    function normalizeValues(values: Set<string>): { normalized: string[]; unit: string } {
        const arr = [...values];
        const unitMatch = arr[0]?.match(/[a-zA-Z/²]+$/);
        const detectedUnit = unitMatch ? unitMatch[0] : '';
        const allSameUnit = detectedUnit && arr.every(v => {
            const m = v.match(/[a-zA-Z/²]+$/);
            return m && m[0] === detectedUnit;
        });
        const hasUnit = arr.some(v => /[a-zA-Z/²]+$/.test(v));
        const noUnit = arr.some(v => /^\d+\.?\d*$/.test(v));

        if (hasUnit && noUnit && detectedUnit) {
            const fixed = arr.map(v => /^\d+\.?\d*$/.test(v) ? `${v}${detectedUnit}` : v);
            const sorted = fixed.sort((a, b) => {
                const na = extractNumber(a), nb = extractNumber(b);
                return (!isNaN(na) && !isNaN(nb)) ? na - nb : a.localeCompare(b, 'ko');
            });
            return { normalized: sorted, unit: detectedUnit };
        }
        const sorted = arr.sort((a, b) => {
            const na = extractNumber(a), nb = extractNumber(b);
            return (!isNaN(na) && !isNaN(nb)) ? na - nb : a.localeCompare(b, 'ko');
        });
        return { normalized: sorted, unit: allSameUnit ? detectedUnit : '' };
    }

    function inferAxisLabel(key: string, values: Set<string>): string {
        const fixed: Record<string, string> = { diameter: '호칭경(mm)', sch: 'SCH', subtype: '유형' };
        if (fixed[key]) return fixed[key];
        const sample = [...values].find(v => v.length > 0) || '';
        if (/^\d+\s*mm$/i.test(sample)) return '구경(mm)';
        if (/kg\/cm[²2]?$/i.test(sample)) return '압력(kg/cm²)';
        if (/^\d+\s*R?T$/i.test(sample)) return '용량(RT)';
        if (/^\d+\s*HP$/i.test(sample)) return '마력(HP)';
        if (/^\d+\s*kW$/i.test(sample)) return '출력(kW)';
        if (/^SCH/i.test(sample)) return 'SCH';
        if (/^\d+$/.test(sample)) return '호칭경';
        return key === 'spec1' ? '규격1' : key === 'spec2' ? '규격2' : key;
    }

    const axes: FilterAxis[] = [];
    for (const [key, vals] of axisMap) {
        if (vals.size > 1) {
            const { normalized } = normalizeValues(vals);
            axes.push({ key, label: inferAxisLabel(key, vals), values: normalized });
        }
    }
    return axes;
}

export function buildSelectorPanel(
    options: ClarifyOption[],
    workName: string,
    forceSelector: boolean = false
): SelectorPanel | undefined {
    if (!forceSelector && options.length <= 6) return undefined;

    const selectorItems: SelectorItem[] = options
        .filter(o => (o.option_type === 'worktype' || o.option_type === 'section') && (o.entity_id || o.section_id))
        .map(o => ({
            label: o.label,
            query: o.query,
            entity_id: o.entity_id || o.section_id,
            source_section: o.source_section,
            option_type: o.option_type,
            specs: parseWorkTypeName(o.label),
        }));

    if (!forceSelector && selectorItems.length < 6) return undefined;

    // ─── 유효한 Base Name(공통 공종명) 추출 ───
    const counts = new Map<string, number>();
    let maxBaseName = workName;
    let maxCount = 0;

    for (const item of selectorItems) {
        // "플랜트 배관 설치(동, SCH 80)" -> "플랜트 배관 설치" 추출
        let rawName = item.label;
        // 접두어 "[기계부문 > ...]" 제거
        if (rawName.includes('] ')) {
            rawName = rawName.substring(rawName.indexOf('] ') + 2).trim();
        }
        // 괄호 규격 부분 이전 텍스트 추출
        const match = rawName.match(/^([^()]+)\s*\(/);
        const baseName = match ? match[1].trim() : rawName;

        const c = (counts.get(baseName) || 0) + 1;
        counts.set(baseName, c);
        if (c > maxCount) {
            maxCount = c;
            maxBaseName = baseName;
        }
    }

    // 그룹핑 타당성 검사: 검색 결과가 완전히 제각각인 하위 항목들이 섞여 있다면 (예: 포괄적 키워드 검색)
    // 공통 규격으로 묶는 Selector Panel을 띄우는 것이 부적절하므로 일반 칩스(options)로 폴백합니다.
    if (!forceSelector && maxCount < 4 && maxCount < selectorItems.length * 0.4) {
        console.log(`[buildSelectorPanel] 공통 BaseName 부족(${maxCount}/${selectorItems.length}). Selector Panel 취소.`);
        return undefined;
    }

    selectorItems.sort((a, b) => {
        const numA = parseInt((a.label.match(/\d+/) || ['0'])[0], 10);
        const numB = parseInt((b.label.match(/\d+/) || ['0'])[0], 10);
        if (numA !== numB) return numA - numB;
        return a.label.localeCompare(b.label, 'ko');
    });

    const filters = extractFilterAxes(selectorItems);

    // 필터 축이 하나도 추출되지 않았다면 일반 칩스(options)로 폴백
    if (!forceSelector && filters.length === 0) return undefined;

    return {
        title: `${maxBaseName} — 규격 선택`,
        filters,
        items: selectorItems,
        original_query: workName,
    };
}
