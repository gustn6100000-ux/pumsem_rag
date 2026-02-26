// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// RAG 챗봇 Edge Function — rag-chat/index.ts
// Phase 2: 모듈 Import 구조 (리팩토링 완료)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

// ━━━ [A] 모듈 Import ━━━
import {
    supabase,
    RAG_API_KEY,
    getCorsHeaders,
    checkRateLimit,
} from "./config.ts";
import { generateEmbedding } from "./embedding.ts";
import type {
    ChatMessage,
    ChatRequest,
    SessionContext,
    SourceInfo,
    ClarifyOption,
    ChatResponse,
    IntentAnalysis,
    EntityResult,
    RelatedResource,
    IlwiItem,
    ChunkResult,
    AnswerOptions,
} from "./types.ts";
import { targetSearch } from "./search.ts";
import {
    expandGraph,
    searchIlwi,
    retrieveChunks,
    fetchLaborCosts,
} from "./graph.ts";
import {
    analyzeIntent,
    detectCostIntent,
    extractSpec,
    graphClarify,
    normalizeSpec,
    classifyComplexity,
} from "./clarify.ts";
import { generateAnswer, generateReasoningGuide } from "./llm.ts";
import {
    makeAnswerResponse,
    makeClarifyResponse,
} from "./context.ts";
import { buildSelectorPanel } from "./resolve.ts";

// ━━━ [D] 컨텍스트 조합 ━━━

// ─── 플랫 테이블 렌더링 (Phase 4 리팩토링) ───
// Why: 교차표(Matrix) 대신 플랫 4열 테이블로 출력하여 토큰을 절약하고 환각을 방지
function renderFlatTable(
    items: RelatedResource[],
    sectionId: string,
    categoryLabel: string,  // "투입 인력" | "투입 장비" | "사용 자재"
    nameLabel: string,      // "직종" | "장비명" | "자재명"
): string {
    if (items.length === 0) return "";

    const lines: string[] = [];
    lines.push(`**[표 ${sectionId}] ${categoryLabel}**\n`);
    lines.push(`| ${nameLabel} | 수량 | 단위 | 규격 |`);
    lines.push("| --- | ---: | --- | --- |");

    items.forEach((item) => {
        const props = (item.properties || {}) as any;
        let specFallback = "-";
        if (item.related_name.includes('_')) specFallback = item.related_name.split('_')[0];
        const spec = props.source_spec || props.spec || props.per_unit || props.work_type_name || specFallback;
        const itemName = item.related_name.includes('_') ? item.related_name.split('_')[1] : item.related_name;
        const quantity = String(props.quantity ?? "-");
        const unit = String(props.unit ?? (nameLabel === "직종" ? "인" : "-"));

        lines.push(`| ${itemName} | ${quantity} | ${unit} | ${spec} |`);
    });

    lines.push("");
    return lines.join("\n");
}

// ─── tables JSON → Markdown 테이블 변환 ───
// Why: graph_chunks.tables는 JSON이므로 LLM이 이해하려면 Markdown 변환 필요
function tablesToMarkdown(tables: any[]): string {
    return tables.map(t => {
        if (!t.rows || t.rows.length === 0) return "";
        const headers: string[] = t.headers || Object.keys(t.rows[0]);
        const headerRow = "| " + headers.join(" | ") + " |";
        const sepRow = "| " + headers.map(() => "---").join(" | ") + " |";
        const dataRows = t.rows.map((r: any) =>
            "| " + headers.map((h: string) => r[h] ?? "").join(" | ") + " |"
        );
        // 표 하단 주석 (첫 번째 것만, 200자 제한)
        const noteText = (t.notes_in_table && t.notes_in_table.length > 0)
            ? `\n> ${t.notes_in_table[0].substring(0, 300)}`
            : "";
        return [headerRow, sepRow, ...dataRows, noteText].filter(Boolean).join("\n");
    }).filter(Boolean).join("\n\n");
}

function buildContext(
    entities: EntityResult[],
    relationsAll: RelatedResource[][],
    ilwiResults: IlwiItem[],
    chunks: ChunkResult[],
    specFilter?: string   // entity 직접 조회 시 두께/호칭경 등 spec 기준 필터
): string {
    const parts: string[] = [];

    // 품셈 검색 결과
    parts.push("## 품셈 검색 결과\n");

    entities.forEach((entity, idx) => {
        const relations = relationsAll[idx] || [];
        const chunk = chunks.find((c) => c.section_id === entity.source_section);

        // 출처 라벨 구성 (Codex F4)
        const sectionLabel = chunk
            ? `${chunk.department} > ${chunk.chapter} > ${chunk.title}`
            : entity.source_section || "출처 미확인";

        // 표번호 명시 (예: [표 13-5-1])
        const sectionId = entity.source_section || "";

        parts.push(
            `### ${idx + 1}. [표 ${sectionId}] ${entity.name} (${entity.type}, 유사도: ${entity.similarity?.toFixed(3)})`
        );
        parts.push(`**표번호**: ${sectionId}`);
        parts.push(`**출처**: ${sectionLabel}\n`);

        // Fix 4: 엔티티 속성 표시 (규격, 수량, 단위 등)
        // Why: LLM이 엔티티의 세부 속성(spec, quantity 등)을 알아야 정확한 답변 가능
        const entityProps = entity.properties || {};
        const propEntries = Object.entries(entityProps)
            .filter(([k]) => !['embedding', 'source_chunk_ids', 'chunk_id'].includes(k))
            .filter(([, v]) => v !== null && v !== undefined && v !== '');
        if (propEntries.length > 0) {
            parts.push(`**속성**: ${propEntries.map(([k, v]) => `${k}=${v}`).join(', ')}\n`);
        }

        // 관계별 그룹화
        const grouped = new Map<string, RelatedResource[]>();
        relations.forEach((r) => {
            const key = r.relation;
            if (!grouped.has(key)) grouped.set(key, []);
            grouped.get(key)!.push(r);
        });

        // ─── 투입 인력 (플랫 렌더링) ───
        const labor = grouped.get("REQUIRES_LABOR") || [];
        if (labor.length > 0) {
            parts.push(renderFlatTable(labor, sectionId, "투입 인력", "직종"));
        }

        // ─── 투입 장비 (플랫 렌더링) ───
        const equipment = grouped.get("REQUIRES_EQUIPMENT") || [];
        if (equipment.length > 0) {
            parts.push(renderFlatTable(equipment, sectionId, "투입 장비", "장비명"));
        }

        // ─── 사용 자재 (플랫 렌더링) ───
        const material = grouped.get("USES_MATERIAL") || [];
        if (material.length > 0) {
            parts.push(renderFlatTable(material, sectionId, "사용 자재", "자재명"));
        }

        // 주의사항 — Note 엔티티의 원문 우선 표시
        // 데이터 구조: note_13-2-3_* → properties.content에 원문 저장 (expandGraph에서 note_content로 매핑)
        //              Back Mirror 등 → properties.spec(조건)/quantity(계수) 저장
        // Why: quantity는 계수(0.3)이지만 원문은 %(30%) 표기 → 변환 필요
        const notes = grouped.get("HAS_NOTE") || [];
        if (notes.length > 0) {
            // 중복 제거: note_content(원문)가 있는 항목과 개별 Note가 겹칠 수 있음
            const seen = new Set<string>();
            parts.push(`**[표 ${sectionId}] 주의사항**\n`);
            notes.forEach((n) => {
                const props = (n.properties || {}) as any;
                const content = props.note_content;  // expandGraph에서 주입된 원문
                const spec = props.spec;
                const quantity = props.quantity;

                if (content) {
                    // 원문 그대로 출력 (note_13-2-3_* 엔티티)
                    const key = content.substring(0, 30);
                    if (!seen.has(key)) {
                        seen.add(key);
                        parts.push(`- ${content}`);
                    }
                } else {
                    // 개별 Note (Back Mirror 등): quantity를 %로 변환
                    const key = n.related_name;
                    if (seen.has(key)) return;
                    seen.add(key);

                    let detail = n.related_name;
                    if (quantity) {
                        const pct = Math.round(Number(quantity) * 100);
                        const action = n.related_name.includes('감') ? '감' : '가산';
                        if (spec) {
                            detail += `(${spec}): ${pct}%까지 ${action}`;
                        } else {
                            detail += `: ${pct}%까지 ${action}`;
                        }
                    } else if (spec) {
                        detail += ` — ${spec}`;
                    }
                    parts.push(`- ${detail}`);
                }
            });
            parts.push("");
        }

        parts.push("---\n");
    });

    // 일위대가 비용 정보
    if (ilwiResults.length > 0) {
        parts.push("## 일위대가 비용 정보\n");
        parts.push("| 항목 | 규격 | 노무비 | 재료비 | 경비 | 합계 |");
        parts.push("| --- | --- | --- | --- | --- | --- |");
        ilwiResults.slice(0, 5).forEach((item) => {
            parts.push(
                `| ${item.name} | ${item.spec || "-"} | ${item.labor_cost?.toLocaleString() ?? "-"} | ${item.material_cost?.toLocaleString() ?? "-"} | ${item.expense_cost?.toLocaleString() ?? "-"} | ${item.total_cost?.toLocaleString() ?? "-"} |`
            );
        });
        parts.push("");
    }

    // 원문 참고
    // specFilter가 있으면 원문 참고 생략: 그래프 관계(REQUIRES_LABOR 등)에서 정확한 수치 제공
    // Why: chunk.text에 전 범위 데이터(두께=3~50)가 포함 → LLM이 그래프 무시하고 원문 기준 전체 출력하는 문제
    if (chunks.length > 0 && !specFilter) {
        parts.push("## 원문 참고 (품셈 원문)\n");
        chunks.forEach((chunk) => {
            parts.push(`> **${chunk.section_id} ${chunk.title}**`);
            parts.push(`> ${chunk.text}`);
            parts.push("");
        });
    } else if (chunks.length > 0 && specFilter) {
        // spec 필터 적용 시: 출처 정보만 간결하게 제공
        parts.push("## 원문 출처\n");
        chunks.forEach((chunk) => {
            parts.push(`> **[표 ${chunk.section_id}] ${chunk.title}** (${chunk.department} > ${chunk.chapter})`);
            parts.push("");
        });
    }

    return parts.join("\n");
}

// ━━━ [G] 파이프라인 함수 ━━━

// ─── answerPipeline: entity → graph 확장 → context → LLM → 응답 ───
// Why: Phase -1(entity_id 직접조회)과 Phase 1b(search 결과 답변)의 중복 로직을 통합
async function answerPipeline(
    entities: EntityResult[],
    question: string,
    history: ChatMessage[],
    startTime: number,
    opts?: {
        skipSiblings?: boolean;   // entity_id 직접조회 시 형제 WT 스킵
        specFilter?: string;      // 두께/규격 필터
        answerOptions?: AnswerOptions;
        analysis?: IntentAnalysis;
    }
): Promise<ChatResponse> {
    const embeddingTokens = Math.ceil(question.length / 2);
    const skipSiblings = opts?.skipSiblings ?? false;
    const specFilter = opts?.specFilter;

    // 💡 [핵심 패치] OOM 방지 및 하위 로직 인덱스 불일치 방지를 위해 상위 10건 확정
    const targetEntities = entities.slice(0, 10);

    // [1] 그래프 확장 (병렬)
    // 💡 [핵심 패치] Caller 레벨에서 source_section 중복 방문 차단 (연쇄 팽창 방지)
    const visitedSections = new Set<string>();
    const relationsPromises = targetEntities.map(async (e) => {
        // source_section 중복 방문 차단
        if (e.source_section && visitedSections.has(e.source_section)) {
            // 동일 section은 skipSectionExpansion=true로 1-hop만 조회
            return expandGraph(e.id, e.type, true);
        }
        if (e.source_section) visitedSections.add(e.source_section);

        return expandGraph(e.id, e.type, skipSiblings);
    });
    const relationsAll = await Promise.all(relationsPromises);

    // [2] 비용 의도 → 일위대가 검색
    let ilwiResults: IlwiItem[] = [];
    if (detectCostIntent(question)) {
        for (const e of targetEntities.filter(e => e.type === "WorkType")) {
            const spec = extractSpec(question);
            const items = await searchIlwi(e.name, spec);
            if (items.length > 0) { ilwiResults.push(...items); break; }
        }
    }

    // [3] 원문 청크 보강
    const chunks = await retrieveChunks(targetEntities, specFilter);
    if (specFilter) console.log(`[answerPipeline] specFilter="${specFilter}" 적용`);

    // [4] 컨텍스트 조합
    let context = buildContext(targetEntities, relationsAll, ilwiResults, chunks, specFilter);

    // [4-1] cost_calculate/report_request 시 노임단가 주입
    const effectiveIntent = opts?.answerOptions?.intent || opts?.analysis?.intent;
    if (effectiveIntent === "cost_calculate" || effectiveIntent === "report_request") {
        const laborNames = relationsAll.flat()
            .filter(r => r.relation === "REQUIRES_LABOR")
            .map(r => r.related_name)
            .filter(Boolean);
        if (laborNames.length > 0) {
            const laborCosts = await fetchLaborCosts(laborNames);
            if (laborCosts.length > 0) {
                context += "\n\n## [2026년 노임단가]\n";
                context += "| 직종 | 노임단가(원/일) |\n|---|---:|\n";
                laborCosts.forEach(lc => {
                    context += `| ${lc.job_name} | ${lc.cost_2026.toLocaleString()} |\n`;
                });
            }
        }
    }

    // [5] LLM 답변 생성
    const llmResult = await generateAnswer(question, context, history, {
        intent: effectiveIntent,
        quantity: opts?.answerOptions?.quantity || opts?.analysis?.quantity || undefined,
    });

    // [6] 응답 조립
    const sourcesWithSection: SourceInfo[] = targetEntities.map(e => {
        const chunk = chunks.find(c => c.section_id === e.source_section);
        return {
            entity_id: e.id,
            entity_name: e.name,
            entity_type: e.type,
            source_section: e.source_section,
            section_label: chunk
                ? `${chunk.department} > ${chunk.chapter} > ${chunk.title}`
                : e.source_section || undefined,
            similarity: e.similarity,
        };
    });

    return makeAnswerResponse(llmResult.answer, startTime, {
        sources: sourcesWithSection,
        entities: targetEntities, relations: relationsAll,
        ilwi: ilwiResults, chunks,
        embeddingTokens, llmResult,
    });
}

// ─── fullViewPipeline: section 전체 원문 → WorkType 탐색 → context → LLM ───
// Why: full_view 4단계 폴백(직접→cross-ref→하위절→Section) 로직을 handleChat에서 분리
async function fullViewPipeline(
    sectionId: string,
    question: string,
    history: ChatMessage[],
    startTime: number
): Promise<ChatResponse> {
    const embeddingTokens = Math.ceil(question.length / 2);

    // ── sub_section 파싱: "13-2-4:sub=1. 전기아크용접(V형)" → base + sub 키워드
    const decodedSectionId = decodeURIComponent(sectionId);
    const subMatch = decodedSectionId.match(/^(.+?):sub=(.+)$/);
    const baseSectionId = subMatch ? subMatch[1] : decodedSectionId;
    const subKeyword = subMatch ? subMatch[2].replace(/^\d+\.\s*/, '') : null;

    console.log(`[fullViewPipeline] base=${baseSectionId}, sub=${subKeyword || 'none'} 전체 원문 조회`);

    // [1] 전체 chunk 로딩
    const { data: chunkData } = await supabase
        .from("graph_chunks")
        .select("id, section_id, title, department, chapter, section, text, tables")
        .eq("section_id", baseSectionId)
        .limit(20);

    let allChunks = (chunkData || []) as any[];

    // [1-1] sub_section 필터
    if (subKeyword && allChunks.length > 1) {
        const filtered = allChunks.filter(c =>
            (c.text && c.text.includes(subKeyword)) ||
            (c.tables && JSON.stringify(c.tables).includes(subKeyword))
        );
        if (filtered.length > 0) {
            console.log(`[fullViewPipeline] sub "${subKeyword}" 필터: ${allChunks.length}건 → ${filtered.length}건`);
            allChunks = filtered;
        }
    }

    // [2] chunk 병합 (text + tables → 하나의 메타 chunk)
    const chunk = allChunks[0] ? { ...allChunks[0] } : null;
    if (chunk && allChunks.length >= 1) {
        chunk.text = allChunks
            .map(c => {
                let t = c.text || "";
                if (c.tables && Array.isArray(c.tables) && c.tables.length > 0) {
                    t += (t ? "\n" : "") + tablesToMarkdown(c.tables);
                }
                return t;
            })
            .filter(t => t.length > 0)
            .join("\n\n");
        console.log(`[fullViewPipeline] ${allChunks.length}건 chunk 병합, text_len=${chunk.text.length}`);
    }

    if (!chunk) {
        console.warn(`[fullViewPipeline] section_id=${baseSectionId} 원문 없음 → 안내`);
        return makeAnswerResponse(
            `해당 절(${baseSectionId})의 원문 데이터를 찾을 수 없습니다.\n다른 작업을 선택하거나, 다시 검색해 주세요.`,
            startTime
        );
    }

    // [3] WorkType 4단계 폴백 탐색
    let wtEntities: EntityResult[] = [];
    let relationsAll: any[][] = [];

    // 3-1: 직접 매칭
    const { data: sectionWTData } = await supabase
        .from("graph_entities")
        .select("id, name, type, properties, source_section")
        .eq("type", "WorkType")
        .eq("source_section", baseSectionId)
        .limit(20);

    const sectionWTs = (sectionWTData || []) as any[];
    console.log(`[fullViewPipeline] WorkType ${sectionWTs.length}건 (baseSectionId=${baseSectionId})`);

    if (sectionWTs.length > 0) {
        wtEntities = sectionWTs.map(wt => ({
            id: wt.id, name: wt.name, type: wt.type,
            properties: wt.properties || {},
            source_section: wt.source_section,
            similarity: 1.0,
        }));
        const rp = wtEntities.map(e => expandGraph(e.id, e.type));
        relationsAll = await Promise.all(rp);
    } else {
        // 3-2: cross-reference (동일 title의 다른 section)
        console.log(`[fullViewPipeline] baseSectionId=${baseSectionId} WorkType 0건 → cross-reference 탐색`);
        const { data: siblings } = await supabase
            .from("graph_chunks")
            .select("section_id")
            .eq("title", chunk.title)
            // 💡 [핵심 패치] 도메인 격리: 동일 부문(department)과 장(chapter)이 일치할 때만 병합
            .eq("department", chunk.department)
            .eq("chapter", chunk.chapter);
        const siblingIds = [...new Set(
            (siblings || []).map((s: any) => s.section_id).filter((sid: string) => sid !== baseSectionId)
        )];

        if (siblingIds.length > 0) {
            const { data: siblingWTs } = await supabase
                .from("graph_entities")
                .select("id, name, type, properties, source_section")
                .eq("type", "WorkType")
                .in("source_section", siblingIds)
                .limit(30);

            if (siblingWTs && siblingWTs.length > 0) {
                console.log(`[fullViewPipeline] cross-ref에서 ${siblingWTs.length}건 WorkType 발견`);
                wtEntities = (siblingWTs as any[]).map(wt => ({
                    id: wt.id, name: wt.name, type: wt.type,
                    properties: wt.properties || {},
                    source_section: wt.source_section,
                    similarity: 0.95,
                }));
                const rp = wtEntities.map(e => expandGraph(e.id, e.type));
                relationsAll = await Promise.all(rp);
            }
        }

        if (wtEntities.length === 0) {
            // 3-3: 하위 절(children) WorkType 탐색
            const childBaseSectionId = baseSectionId.includes('#') ? baseSectionId.split('#')[0] : baseSectionId;
            const childPrefix = childBaseSectionId + '-';
            console.log(`[fullViewPipeline] cross-ref 실패 → 하위 절 탐색 (prefix=${childPrefix})`);

            const { data: childWTs } = await supabase
                .from("graph_entities")
                .select("id, name, type, properties, source_section")
                .eq("type", "WorkType")
                .ilike("source_section", `${childPrefix}%`)
                .limit(50);

            if (childWTs && childWTs.length > 0) {
                console.log(`[fullViewPipeline] 하위 절에서 ${childWTs.length}건 WorkType 발견`);
                wtEntities = (childWTs as any[]).map(wt => ({
                    id: wt.id, name: wt.name, type: wt.type,
                    properties: wt.properties || {},
                    source_section: wt.source_section,
                    similarity: 0.98,
                }));
                const rp = wtEntities.map(e => expandGraph(e.id, e.type));
                relationsAll = await Promise.all(rp);

                // 하위 절 chunk 텍스트 보강
                const childSectionIds = [...new Set(childWTs.map((w: any) => w.source_section))];
                const { data: childChunks } = await supabase
                    .from("graph_chunks")
                    .select("id, section_id, title, department, chapter, section, text")
                    .in("section_id", childSectionIds)
                    .limit(10);

                if (childChunks && childChunks.length > 0) {
                    const childTexts = (childChunks as any[])
                        .filter(c => c.text && c.text.length > 10)
                        .map(c => `### ${c.section_id} ${c.title}\n${c.text}`)
                        .join('\n\n');
                    if (childTexts) chunk.text = (chunk.text || '') + '\n\n' + childTexts;
                }
            } else {
                // 3-4: Section 자체 확장 (최후 수단)
                const { data: sectionEntity } = await supabase
                    .from("graph_entities")
                    .select("id, name, type, properties, source_section")
                    .eq("type", "Section")
                    .eq("source_section", baseSectionId)
                    .limit(1);

                if (sectionEntity && sectionEntity.length > 0) {
                    const se = sectionEntity[0] as any;
                    wtEntities = [{
                        id: se.id, name: se.name, type: se.type,
                        properties: se.properties || {},
                        source_section: se.source_section,
                        similarity: 1.0,
                    }];
                    const sectionRels = await expandGraph(se.id, "Section");
                    relationsAll = [sectionRels];
                }
            }
        }
    }

    // [4] 원문 + 그래프 관계 컨텍스트 → LLM → 응답
    const context = [
        `## 품셈 원문: ${chunk.title}`,
        `**출처**: ${chunk.department} > ${chunk.chapter} > ${chunk.title}`,
        `**표번호**: ${chunk.section_id}`,
        `\n${chunk.text}`,
        `\n---\n`,
        buildContext(wtEntities, relationsAll, [], [chunk as ChunkResult]),
    ].join("\n");

    const llmResult = await generateAnswer(question, context, history);

    return makeAnswerResponse(llmResult.answer, startTime, {
        sources: [{
            entity_name: chunk.title,
            entity_type: "Section",
            source_section: chunk.section_id,
            section_label: `${chunk.department} > ${chunk.chapter} > ${chunk.title}`,
            similarity: 1.0,
        }],
        entities: wtEntities, relations: relationsAll,
        chunks: [chunk as any],
        embeddingTokens, llmResult,
    });
}

// ─── searchPipeline: embedding → targetSearch → Section-Only 분기 → answer ───
// Why: 검색 + 결과 평가 + 답변/clarify 분기를 handleChat에서 분리
async function searchPipeline(
    analysis: IntentAnalysis,
    question: string,
    history: ChatMessage[],
    startTime: number,
    answerOptions?: AnswerOptions
): Promise<ChatResponse> {
    const embeddingTokens = Math.ceil(question.length / 2);

    // [1] 질문 임베딩
    const embedding = await generateEmbedding(question);

    // [1-1] 💡 [Track B-1 최적화] 동의어 재료 즉시 추출 (targetSearch 대기 불필요)
    // Why: domainExp는 analysis(LLM 분석 결과)에서만 산출. targetSearch 결과 의존 없음.
    //      따라서 targetSearch와 동의어 WorkType 쿼리를 Promise.all로 병렬 실행하여
    //      순차 I/O 대기시간(+1.4s)을 targetSearch의 대기시간에 완전히 가려(Shadowing) 제거.
    const { expandDomainSynonyms } = await import("./search.ts");
    const domainTerms = analysis.work_name
        ? [analysis.work_name, ...(analysis.keywords || [])]
        : analysis.keywords || [];
    const domainExp = expandDomainSynonyms(domainTerms);
    const synOrClauses = domainExp.length > 0
        ? domainExp.map(s => `name.ilike.%${s}%`).join(",")
        : null;

    // [1-2] 💡 메인 검색 + 동의어 서브 검색을 Promise.all로 병렬 출발
    const [entities, synWTsResponse] = await Promise.all([
        targetSearch(analysis, embedding, question),
        synOrClauses
            ? supabase
                .from("graph_entities")
                .select("id, name, type, source_section, properties")
                .eq("type", "WorkType")
                .or(synOrClauses)
                .limit(50)
            : Promise.resolve({ data: [] as any[], error: null }),
    ]);
    const synonymWorkTypes = synWTsResponse.data || [];
    if (synonymWorkTypes.length > 0) {
        console.log(`[searchPipeline] 도메인 동의어 WorkType: ${synonymWorkTypes.length}건 (${domainExp.join(",")})`);
    }

    // [2] Section만 매칭 → clarify 분기
    const sectionOnly = entities.length > 0 && entities.every(e => e.type === "Section");
    if (sectionOnly) {
        const sectionSourceIds = [...new Set(entities.map(e => e.source_section).filter(Boolean))] as string[];

        // Section source_section + 동의어 WorkType source_section 병합
        const synSectionIds = [...new Set(synonymWorkTypes.map(w => w.source_section).filter(Boolean))] as string[];
        const allSectionIds = [...new Set([...sectionSourceIds, ...synSectionIds])];

        if (allSectionIds.length > 1) {
            // 복수 분야: 섹션 선택 칩 직접 생성
            console.log(`[searchPipeline] Section ${sectionSourceIds.length}개 + 동의어 ${synSectionIds.length}개 = 총 ${allSectionIds.length}개 분야 → 섹션 선택`);
            const { data: chunkMetas } = await supabase
                .from("graph_chunks")
                .select("section_id, department, chapter, title")
                .in("section_id", allSectionIds);

            const metaMap = new Map<string, any>();
            for (const cm of (chunkMetas || [])) {
                if (!metaMap.has(cm.section_id)) metaMap.set(cm.section_id, cm);
            }

            // Section 엔티티 기반 옵션
            const options: ClarifyOption[] = entities.slice(0, 10).map(s => {
                const meta = metaMap.get(s.source_section || "");
                const label = meta
                    ? `${meta.department} > ${meta.chapter} > ${meta.title}`
                    : `[${s.source_section || ""}] ${s.name}`;
                return {
                    label,
                    query: `${s.name} 품셈`,
                    source_section: s.source_section,
                    section_id: s.source_section,
                    option_type: 'section' as const,
                };
            });

            // 동의어 WorkType의 source_section 중 Section에 없는 것 추가
            const existingSrcSet = new Set(sectionSourceIds);
            const addedSynSrcSet = new Set<string>();
            for (const wt of synonymWorkTypes) {
                if (wt.source_section && !existingSrcSet.has(wt.source_section) && !addedSynSrcSet.has(wt.source_section)) {
                    addedSynSrcSet.add(wt.source_section);
                    const meta = metaMap.get(wt.source_section);
                    const label = meta
                        ? `${meta.department} > ${meta.chapter} > ${meta.title}`
                        : `[${wt.source_section}] ${wt.name}`;
                    options.push({
                        label,
                        query: `${meta?.title || wt.name} 품셈`,
                        source_section: wt.source_section,
                        section_id: wt.source_section,
                        option_type: 'section' as const,
                    });
                }
            }

            return makeClarifyResponse(
                `"${question}" 관련 품셈이 **${allSectionIds.length}개 분야**에 있습니다.\n어떤 분야의 품셈을 찾으시나요?`,
                startTime,
                {
                    options,
                    reason: `'${entities[0].name}' 관련 품셈이 여러 분야에 존재하여 선택이 필요합니다.`,
                    original_query: question,
                },
                { entities }
            );
        }

        // 단일 섹션: 하위 WorkType 확인
        const singleSectionId = sectionSourceIds[0];
        const { data: childWorkTypes } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties, source_section")
            .eq("type", "WorkType")
            .eq("source_section", singleSectionId)
            .limit(200);

        if (childWorkTypes && childWorkTypes.length > 3) {
            console.log(`[searchPipeline] Section 1개 + WorkType ${childWorkTypes.length}개 → Step 2`);
            const clarifyResult = await graphClarify(
                { ...analysis, intent: "clarify_needed" as const, work_name: analysis.work_name || entities[0].name },
                singleSectionId
            );
            return makeClarifyResponse(clarifyResult.message, startTime, {
                options: clarifyResult.options,
                reason: `'${entities[0].name}' 하위에 ${childWorkTypes.length}개 작업이 있어 선택이 필요합니다.`,
                original_query: question,
                selector: clarifyResult.selector,
            }, { entities });
        }
        // WT ≤ 3 → answerPipeline으로 진행
    }

    // [3] 검색 결과 없음
    if (entities.length === 0) {
        const llmResult = await generateAnswer(
            question,
            "제공된 품셈 데이터베이스에서 관련 정보를 찾지 못했습니다.",
            history
        );
        return makeAnswerResponse(llmResult.answer, startTime, {
            embeddingTokens, llmResult,
        });
    }

    // [4] WorkType 매칭 → answerPipeline
    return answerPipeline(entities, question, history, startTime, {
        answerOptions, analysis,
    });
}

// ━━━ [H] 메인 핸들러 (라우터) ━━━

// ─── 특수 테이블 전용 감지기 및 파이프라인 (Phase 1.5) ───
interface ComplexTableQuery {
    section_code: string;       // '13-1-1'
    material?: string;          // '배관용 탄소강관'
    spec_mm?: number;           // 200
    pipe_location?: string;     // '옥내' | '옥외'
    joint_type?: string;        // '용접식' | '나사식'
    quantity_value?: number;    // 10 (m)
}

const COMPLEX_TABLE_TRIGGERS: Record<string, {
    section_code: string;
    materials: string[];
}> = {
    "플랜트 배관": {
        section_code: "13-1-1",
        materials: ["탄소강관", "합금강", "스텐레스", "스테인리스", "알루미늄",
            "동관", "황동", "KSD3507", "A335", "Type304", "Monel", "백관", "흑관"]
    },
    "밸브 설치": {
        section_code: "13-3-1",
        materials: ["밸브", "플랜지"]
    },
    "플랜지 설치": {
        section_code: "13-3-1",
        materials: ["밸브", "플랜지"]
    }
};

function detectComplexTable(question: string): ComplexTableQuery | null {
    for (const [trigger, config] of Object.entries(COMPLEX_TABLE_TRIGGERS)) {
        const triggerWords = trigger.split(" ");
        const allTriggerMatch = triggerWords.every(w => question.includes(w));
        if (!allTriggerMatch) continue;

        const matchedMaterial = config.materials.find(m => question.includes(m));

        const specMatch = question.match(/(\d{2,4})\s*(mm|A|a|㎜)/);
        const spec_mm = specMatch ? parseInt(specMatch[1]) : undefined;

        const pipe_location = question.includes("옥외") ? "옥외" : (question.includes("옥내") ? "옥내" : undefined);
        const joint_type = question.includes("나사") ? "나사식" : (question.includes("용접") ? "용접식" : undefined);

        const qtyMatch = question.match(/(\d+(?:\.\d+)?)\s*(m|미터|M|ton|톤)\b/);
        const quantity_value = qtyMatch ? parseFloat(qtyMatch[1]) : undefined;

        return {
            section_code: config.section_code,
            material: matchedMaterial,
            spec_mm,
            pipe_location,
            joint_type,
            quantity_value,
        };
    }
    return null;
}

function findBestCostMatch(
    jobName: string,
    costMap: Map<string, number>
): { name: string; cost: number } | null {
    if (costMap.has(jobName)) return { name: jobName, cost: costMap.get(jobName)! };
    const normalized = jobName.replace(/\s+/g, '');
    for (const [key, cost] of costMap) {
        if (key.replace(/\s+/g, '') === normalized) return { name: key, cost };
    }
    let bestMatch: { name: string; cost: number } | null = null;
    for (const [key, cost] of costMap) {
        const keyNorm = key.replace(/\s+/g, '');
        if (keyNorm.includes(normalized) || normalized.includes(keyNorm)) {
            if (!bestMatch || key.length < bestMatch.name.length) {
                bestMatch = { name: key, cost };
            }
        }
    }
    return bestMatch;
}

async function complexTablePipeline(
    query: ComplexTableQuery,
    question: string,
    history: ChatMessage[],
    startTime: number
): Promise<ChatResponse> {
    console.log(`[complexTablePipeline] section=${query.section_code}, ` +
        `material=${query.material}, spec=${query.spec_mm}, ` +
        `location=${query.pipe_location}, joint=${query.joint_type}`);

    let dbQuery = supabase
        .from("complex_table_specs")
        .select("*")
        .eq("section_code", query.section_code);

    if (query.material) dbQuery = dbQuery.ilike("material", `%${query.material}%`);
    if (query.pipe_location) dbQuery = dbQuery.eq("pipe_location", query.pipe_location);
    if (query.joint_type) dbQuery = dbQuery.eq("joint_type", query.joint_type);

    const { data: specs, error } = await dbQuery;

    let filteredSpecs: any[] = specs || [];
    if (query.spec_mm) {
        filteredSpecs = filteredSpecs.filter((s: any) => s.spec_mm === query.spec_mm);
    }

    if (query.material && filteredSpecs.length > 0) {
        const uniqueMaterials = [...new Set(filteredSpecs.map(s => s.material))];
        let bestMaterial = uniqueMaterials[0];
        for (const mat of uniqueMaterials) {
            const matPrefix = mat.split('(')[0];
            if (question.replace(/\s+/g, '').includes(matPrefix.replace(/\s+/g, ''))) {
                bestMaterial = mat;
                break;
            }
        }
        filteredSpecs = filteredSpecs.filter((s: any) => s.material === bestMaterial);
    }

    if (filteredSpecs.length === 0) {
        console.warn("[complexTablePipeline] 전용 DB에 데이터 없음 → 일반 search 폴백/안내");
        // Fallback to normal semantic search if missing
        const analysis = await analyzeIntent(question, history);
        return searchPipeline(analysis, question, history, startTime);
    }

    // Step 1.5: 다중 조합(재질, 배관장소, 접합방식)일 경우 사용자에게 Clarification 요청
    const uniqueCombos = [...new Set(filteredSpecs.map(s => `${s.material}||${s.pipe_location}||${s.joint_type}`))];
    if (uniqueCombos.length > 1) {
        const options: ClarifyOption[] = uniqueCombos.slice(0, 15).map(combo => {
            const [mat, loc, jnt] = combo.split('||');
            return {
                label: `${mat} (${loc} ${jnt})`, // 간결하게 표시
                query: `플랜트 배관 설치 ${mat} ${loc} ${jnt}`,
                option_type: 'section',
                section_id: query.section_code
            };
        });

        // forceSelector=true 로 체크박스 UI 강제 활성화
        const selector = buildSelectorPanel(options, `[${query.section_code}] 배관 설치`, true);

        return makeClarifyResponse(
            `"${question}"에 해당하는 품셈 기준이 여러 개 발견되었습니다. 단일 기준을 선택해 주세요.`,
            startTime,
            {
                options,
                reason: "재질, 배관구분, 접합방식이 명확하지 않아 선택이 필요합니다.",
                original_query: question,
                ...(selector ? { selector } : {})
            }
        );
    }

    // 단일 조합 확정
    const exactMat = filteredSpecs[0].material;
    const exactLoc = filteredSpecs[0].pipe_location;
    const exactJnt = filteredSpecs[0].joint_type;

    // Step 2: 2026 노임단가 사전연산
    const jobNames = [...new Set(filteredSpecs.map((s: any) => s.job_name as string))];
    const laborCosts = await fetchLaborCosts(jobNames);
    const costMap = new Map(laborCosts.map(lc => [lc.job_name, lc.cost_2026]));

    const quantityMultiplier = query.quantity_value || 1;
    const quantityUnit = filteredSpecs[0]?.quantity_unit || "인/100m";
    const unitLabel = quantityUnit === "인/100m" ? "100m" : quantityUnit.replace("인/", "");

    let context = `## 📋 [${query.section_code}] ${filteredSpecs[0]?.section_name}\n\n`;
    context += `**재질**: ${exactMat} | **배관구분**: ${exactLoc} | **접합방식**: ${exactJnt}\n\n`;

    const uniqueSpecs = [...new Set(filteredSpecs.map((s: any) => s.spec_mm))].sort((a, b) => a - b);
    const hasMultipleSpecs = uniqueSpecs.length > 1;

    context += `## [2026년 노임단가 기반 산출 결과 (백엔드 계산 완료)]\n\n`;

    let totalCost = 0;
    if (hasMultipleSpecs) {
        // [매트릭스 렌더링]: 구경(mm)이 컬럼이 되는 테이블
        const specHeaders = uniqueSpecs.map(s => `${s}mm`).join(" | ");
        const specSep = uniqueSpecs.map(() => "---:").join(" | ");

        context += `| 직종 | 노임단가(원/일) | ${specHeaders} |\n`;
        context += `|---|---:|${specSep}|\n`;

        for (const job of jobNames) {
            const matched = findBestCostMatch(job, costMap);
            const unitCost = matched?.cost ?? 0;

            const rowValues = uniqueSpecs.map(spec => {
                const item = filteredSpecs.find((s: any) => s.job_name === job && s.spec_mm === spec);
                return item ? item.quantity : "-";
            });

            context += `| ${job} | ${unitCost.toLocaleString()} | ` + rowValues.join(" | ") + ` |\n`;
        }

        if (quantityMultiplier !== 1) {
            context += `\n> 💡 **참고**: 수량(${quantityMultiplier}${unitLabel.replace("100m", "m")})을 전체 노임비로 계산하시려면, 특정 구경(mm) 하나를 이어서 다시 질문해 주세요.\n`;
        }
    } else {
        // [플랫 테이블 렌더링]: 단일 구경의 세부 조건과 합산된 노무비 (기존 로직)
        const specInfo = filteredSpecs[0];
        context += `**구경**: ${specInfo.spec_mm}mm | **외경**: ${specInfo.outer_dia_mm}mm | **두께**: ${specInfo.thickness_mm}mm | **단위중량**: ${specInfo.unit_weight}kg/m\n\n`;

        context += `| 직종 | 품(${unitLabel}당) | 노임단가(원/일) | `;
        if (quantityMultiplier > 1) {
            const displayUnit = unitLabel === "100m" ? "m" : unitLabel;
            context += `${quantityMultiplier}${displayUnit} 환산 금액(원) | `;
        }
        context += `비고 |\n|---|---:|---:|`;
        if (quantityMultiplier > 1) context += `---:|`;
        context += `---|\n`;

        for (const spec of filteredSpecs) {
            const matched = findBestCostMatch(spec.job_name, costMap);
            const unitCost = matched?.cost ?? 0;
            const qtyPer100m = parseFloat(spec.quantity);

            const actualQty = quantityUnit === "인/100m"
                ? qtyPer100m * (quantityMultiplier / 100)
                : qtyPer100m * quantityMultiplier;
            const amount = Math.round(actualQty * unitCost);
            totalCost += amount;

            context += `| ${spec.job_name} | ${spec.quantity} | ${unitCost.toLocaleString()} | `;
            if (quantityMultiplier > 1) {
                context += `${amount.toLocaleString()} | `;
            }
            context += `${query.section_code} |\n`;
        }

        if (quantityMultiplier > 1) {
            const toolCost = Math.round(totalCost * 0.03);
            context += `| 공구손료 (3%) | - | - | ${toolCost.toLocaleString()} | 인력품의 3% |\n`;
            totalCost += toolCost;
            context += `| **합계** | | | **${totalCost.toLocaleString()}** | |\n`;
        }
    }

    context += `\n> ⚠️ 위 금액은 **전용 정형화 DB에서 정확히 조회**되어 백엔드에서 계산한 확정값입니다.\n`;
    context += `> LLM은 이 숫자를 절대 수정하지 말고 그대로 출력하세요.\n`;

    // Step 3: LLM 포장
    const llmResult = await generateAnswer(question, context, history, {
        intent: "cost_calculate",
        quantity: query.quantity_value,
    });

    const sources: SourceInfo[] = [{
        entity_name: `${filteredSpecs[0]?.section_name} (${filteredSpecs[0]?.material})`,
        entity_type: "ComplexTable" as any,
        source_section: query.section_code,
        section_label: `${filteredSpecs[0]?.section_name}`,
        similarity: 1.0
    }];

    return makeAnswerResponse(llmResult.answer, startTime, {
        sources,
        embeddingTokens: 0,
        llmResult,
    });
}

async function handleChat(
    question: string,
    history: ChatMessage[],
    entityId?: string,
    sectionId?: string,
    sessionContext?: SessionContext,
    answerOptions?: AnswerOptions
): Promise<ChatResponse> {
    const startTime = Date.now();

    // ═══ Route 0.5: 특수 복합 테이블 전용 라우터 (Phase 1.5) ═══
    const complexTableMatch = detectComplexTable(question);
    if (complexTableMatch) {
        console.log(`[handleChat] 🎯 Route 0.5: 특수 테이블 감지 → ${complexTableMatch.section_code}`);
        return complexTablePipeline(complexTableMatch, question, history, startTime);
    }

    // ═══ Route 1: entity_id 직접 조회 (칩 선택 시) ═══
    if (entityId) {
        const entityIds = entityId.split(',').map(s => s.trim()).filter(Boolean);
        console.log(`[handleChat] entity_ids=[${entityIds.join(',')}] → answerPipeline`);
        const { data: directEntities } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties, source_section")
            .in("id", entityIds);

        if (directEntities && directEntities.length > 0) {
            const entities: EntityResult[] = directEntities.map((de: any) => ({
                id: de.id, name: de.name, type: de.type,
                properties: de.properties || {},
                source_section: de.source_section,
                similarity: 1.0,
            }));
            const firstSpec = entities[0]?.properties?.spec as string || "";
            const specNum = firstSpec.match(/^(\d+)/)?.[1];
            return answerPipeline(entities, question, history, startTime, {
                skipSiblings: true,
                specFilter: specNum,
                answerOptions,
            });
        }
    }

    // ═══ Route 2: section_id → full_view or Step 2 clarify ═══
    if (sectionId) {
        console.log(`[handleChat] section_id=${sectionId} → 섹션 내 탐색`);
        const isSubSection = sectionId.includes(":sub=");
        const isFullView = isSubSection || question.includes("전체") || question.includes("목록");

        if (isFullView) return fullViewPipeline(sectionId, question, history, startTime);

        // Step 2: 해당 섹션 내 하목 선택 옵션 제시
        const clarifyAnalysis: IntentAnalysis = {
            intent: "clarify_needed",
            work_name: question.replace(/품셈|전체|\s/g, "") || null,
            spec: null,
            keywords: [],
            ambiguity_reason: "섹션 내 하목 선택이 필요합니다.",
        };
        const clarifyResult = await graphClarify(clarifyAnalysis, sectionId);
        return makeClarifyResponse(clarifyResult.message, startTime, {
            options: clarifyResult.options,
            reason: "섹션 내 하위 작업을 선택해 주세요.",
            original_query: question,
            selector: clarifyResult.selector,
        });
    }

    // ═══ Route 3: 의도 분석 (DeepSeek v3.2) ═══
    const analysis = await analyzeIntent(question, history, sessionContext);
    analysis.complexity = classifyComplexity(question, analysis);
    analysis.spec = normalizeSpec(analysis.spec);

    // ─── 인사/도움말 ───
    if (analysis.intent === "greeting") {
        return makeAnswerResponse(
            "안녕하세요! 건설 공사 표준품셈 AI 어시스턴트입니다. 🏗️\n\n" +
            "다음과 같은 질문이 가능합니다:\n" +
            "- **품셈 검색**: \"강관용접 200mm SCH 40 품셈\"\n" +
            "- **인력 투입량**: \"콘크리트 타설 인력\"\n" +
            "- **비용 산출**: \"거푸집 설치 일위대가\"\n\n" +
            "공종명과 규격을 함께 입력하면 더 정확한 결과를 얻을 수 있습니다.",
            startTime
        );
    }

    // ─── 비용 산출 (cost_calculate) ───
    if (analysis.intent === "cost_calculate") {
        const targetEntityId = sessionContext?.last_entity_id;
        if (!targetEntityId) {
            return makeAnswerResponse(
                "노무비를 계산하려면 먼저 품셈을 검색해 주세요.\n\n" +
                "예시: \"강관용접 200mm SCH 40\" 또는 \"TIG용접 품셈\"",
                startTime
            );
        }
        console.log(`[handleChat] cost_calculate: entity=${targetEntityId} → 재귀 호출`);
        return handleChat(question, history, targetEntityId, undefined, sessionContext, {
            intent: "cost_calculate",
            quantity: analysis.quantity || sessionContext?.last_quantity || undefined,
        });
    }

    // ─── 변경 요청 (modify_request) ───
    if (analysis.intent === "modify_request") {
        if (analysis.modify_type === "quantity" && sessionContext?.last_entity_id) {
            console.log(`[handleChat] modify_request(quantity=${analysis.quantity}): entity=${sessionContext.last_entity_id}`);
            return handleChat(question, history, sessionContext.last_entity_id, undefined, sessionContext, {
                intent: "cost_calculate",
                quantity: analysis.quantity || undefined,
                modifyType: "quantity",
            });
        }
        if (analysis.modify_type === "work_change" && analysis.work_name) {
            console.log(`[handleChat] modify_request(work_change): ${analysis.work_name}, spec=${sessionContext?.last_spec}`);
            const modifiedAnalysis: IntentAnalysis = {
                ...analysis,
                intent: analysis.spec || sessionContext?.last_spec ? "search" : "clarify_needed",
                spec: analysis.spec || sessionContext?.last_spec || null,
            };
            Object.assign(analysis, modifiedAnalysis);
        }
        if (analysis.modify_type === "exclude_labor" || (!analysis.modify_type && sessionContext?.last_entity_id)) {
            return makeAnswerResponse(
                "직종 제외/수정 기능은 아직 준비 중입니다. 현재는 수량 변경과 공종 변경만 지원합니다.\n\n" +
                "예시: \"50m로 바꿔서 다시\" 또는 \"TIG로 바꿔줘\"",
                startTime
            );
        }
        if (!sessionContext?.last_entity_id && !analysis.work_name) {
            return makeAnswerResponse(
                "변경할 이전 검색 결과가 없습니다. 먼저 품셈을 검색해 주세요.",
                startTime
            );
        }
    }

    // ─── 산출서 요청 (report_request) ───
    if (analysis.intent === "report_request") {
        const targetEntityId = sessionContext?.last_entity_id;
        if (!targetEntityId) {
            return makeAnswerResponse(
                "산출서를 만들려면 먼저 품셈을 검색해 주세요.\n\n" +
                "예시: \"강관용접 200mm SCH 40\"",
                startTime
            );
        }
        console.log(`[handleChat] report_request: entity=${targetEntityId} → 재귀 호출`);
        return handleChat(question, history, targetEntityId, undefined, sessionContext, {
            intent: "report_request",
            quantity: sessionContext?.last_quantity || undefined,
        });
    }

    // ─── 명확화 필요 → graphClarify ───
    if (analysis.intent === "clarify_needed") {
        const clarifyResult = await graphClarify(analysis);
        return makeClarifyResponse(clarifyResult.message, startTime, {
            options: clarifyResult.options,
            reason: analysis.ambiguity_reason || "질문의 범위가 넓어 구체적인 확인이 필요합니다",
            original_query: question,
            selector: clarifyResult.selector,
        });
    }

    // ═══ Route 3.5: 복합 질의 듀얼 모델 라우팅 (Phase 2) ═══
    if (analysis.complexity === "complex" && (analysis.intent === "search" || analysis.intent === "complex_estimate")) {
        console.log(`[handleChat] 🎯 Route 3.5 (Complex) triggered. Calling Reasoner...`);
        const guide = await generateReasoningGuide(question, history);
        if (guide && guide.search_tasks && guide.search_tasks.length > 0) {
            console.log(`[handleChat] Reasoner Guide:`, JSON.stringify(guide));

            // 멀티 태스크 마스터플랜을 컨텍스트에 추가 주입하여 LLM 답변 시 참고하게 함 (현재는 fall-through 하여 searchPipeline으로 진입)
            const masterPlanContext = `\n\n[AI 분해 마스터플랜]\n분석된 검색 대상: ${guide.search_tasks.map(t => `"${t}"`).join(', ')}\n필요 계산: ${guide.calculations.join(', ')}\n추가 조정: ${guide.adjustments.join(', ')}\n`;

            analysis.ambiguity_reason = (analysis.ambiguity_reason || "") + masterPlanContext;

            // 키워드도 확장하여 첫 targetSearch의 회수율 높임 (임시 조치)
            const addedKeywords = guide.search_tasks.flatMap(t => t.split(/\s+/)).filter(w => w.length >= 2);
            analysis.keywords = [...new Set([...analysis.keywords, ...addedKeywords])];
        }
    }

    // ═══ Route 4: search → searchPipeline ═══
    return searchPipeline(analysis, question, history, startTime, answerOptions);
}

// ━━━ 서버 진입점 ━━━

Deno.serve(async (req: Request) => {
    const corsHeaders = getCorsHeaders(req);

    // OPTIONS preflight
    if (req.method === "OPTIONS") {
        return new Response(null, { status: 204, headers: corsHeaders });
    }

    // POST만 허용
    if (req.method !== "POST") {
        return new Response(
            JSON.stringify({ error: "method_not_allowed" }),
            { status: 405, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
    }

    // (Codex F1) API Key 검증
    if (RAG_API_KEY) {
        const clientKey = req.headers.get("x-api-key") || "";
        if (clientKey !== RAG_API_KEY) {
            return new Response(
                JSON.stringify({ error: "unauthorized" }),
                { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } }
            );
        }
    }

    // (Codex F1) Rate Limiting
    const clientIp =
        req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
        req.headers.get("cf-connecting-ip") ||
        "unknown";
    if (!checkRateLimit(clientIp)) {
        return new Response(
            JSON.stringify({ error: "rate_limited" }),
            { status: 429, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
    }

    // Body 크기 제한 (10KB)
    const contentLength = parseInt(req.headers.get("content-length") || "0", 10);
    if (contentLength > 10_240) {
        return new Response(
            JSON.stringify({ error: "payload_too_large" }),
            { status: 413, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
    }

    try {
        const body = (await req.json()) as ChatRequest;

        // 입력 검증
        if (!body.question || body.question.trim().length === 0) {
            return new Response(
                JSON.stringify({ error: "question_required" }),
                { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
            );
        }

        // (Codex F5) 500자 초과 시 truncate (에러가 아닌 자동 절삭)
        const question = body.question.trim().substring(0, 500);
        const history = (body.history || []).slice(-5);

        // RAG 파이프라인 실행
        const entityId = body.entity_id || undefined;
        const sectionId = body.section_id || undefined;
        const sessionContext = body.session_context || undefined;
        const result = await handleChat(question, history, entityId, sectionId, sessionContext);

        return new Response(JSON.stringify(result), {
            status: 200,
            headers: { ...corsHeaders, "Content-Type": "application/json" },
        });
    } catch (err) {
        // 에러 종류별 분기
        const errorMsg = err instanceof Error ? err.message : String(err);
        console.error("rag-chat error:", errorMsg);

        // Gemini API 에러 → 502
        if (errorMsg.includes("Embedding API failed")) {
            return new Response(
                JSON.stringify({ error: "embedding_failed" }),
                { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
            );
        }
        if (errorMsg.includes("LLM API failed")) {
            // (Codex 권장) LLM 실패 시 구조 응답 폴백
            return new Response(
                JSON.stringify({
                    error: "llm_failed",
                    message: "LLM 답변 생성에 실패했습니다. 검색 결과만 반환합니다.",
                }),
                { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
            );
        }

        // 기타 서버 에러
        return new Response(
            JSON.stringify({ error: "internal_error" }),
            { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
    }
});
