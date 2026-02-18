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
} from "./clarify.ts";
import { generateAnswer } from "./llm.ts";
import {
    makeAnswerResponse,
    makeClarifyResponse,
} from "./context.ts";

// ━━━ [D] 컨텍스트 조합 ━━━

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

        // ─── 투입 인력: 자세×직종 매트릭스 또는 규격별 그룹화 ───
        const labor = grouped.get("REQUIRES_LABOR") || [];
        if (labor.length > 0) {
            const hasWorkType = labor.some(l => (l.properties as any)?.work_type_name);
            // 매트릭스 가능 여부: related_name에 '_' 포함 시 (예: "하향_용접공")
            const hasMatrix = labor.some(l => l.related_name.includes('_'));

            if (hasMatrix && !hasWorkType) {
                // ─── 매트릭스 출력: 자세×직종 가로 테이블 ───
                // Why: "하향_용접공" → 자세="하향", 직종="용접공" 분리 → 가독성 개선
                const positionMap = new Map<string, Map<string, string>>();
                const allJobs = new Set<string>();
                labor.forEach((l) => {
                    const [position, job] = l.related_name.includes('_')
                        ? l.related_name.split('_', 2)
                        : [l.related_name, '수량'];
                    const props = (l.properties || {}) as any;
                    allJobs.add(job);
                    if (!positionMap.has(position)) positionMap.set(position, new Map());
                    positionMap.get(position)!.set(job, String(props.quantity ?? "-"));
                });
                const jobList = [...allJobs];
                const unit = (labor[0]?.properties as any)?.unit || "인";

                parts.push(`**[표 ${sectionId}] 투입 인력**\n`);
                parts.push("| 자세 | " + jobList.map(j => `${j}(${unit})`).join(" | ") + " |");
                parts.push("| --- | " + jobList.map(() => "---:").join(" | ") + " |");
                for (const [position, jobs] of positionMap) {
                    parts.push("| " + position + " | " + jobList.map(j => jobs.get(j) ?? "-").join(" | ") + " |");
                }
                parts.push("");
            } else if (hasWorkType) {
                // 규격(work_type_name)별로 그룹화 → 원본 품셈 테이블 형태
                const byWorkType = new Map<string, RelatedResource[]>();
                labor.forEach((l) => {
                    const wt = (l.properties as any)?.work_type_name || "기타";
                    if (!byWorkType.has(wt)) byWorkType.set(wt, []);
                    byWorkType.get(wt)!.push(l);
                });

                parts.push(`**[표 ${sectionId}] 투입 인력**\n`);
                // 규격(work_type_name)을 숫자 기준 정렬: 15→20→90→100→125→200
                const sortedWorkTypes = [...byWorkType.entries()].sort(([a], [b]) => {
                    const numA = parseInt((a.match(/\d+/) || ['0'])[0], 10);
                    const numB = parseInt((b.match(/\d+/) || ['0'])[0], 10);
                    if (numA !== numB) return numA - numB;
                    // 같은 숫자면 두 번째 숫자(SCH 등) 기준
                    const numA2 = parseInt((a.match(/\d+.*?(\d+)/)?.[1] || '0'), 10);
                    const numB2 = parseInt((b.match(/\d+.*?(\d+)/)?.[1] || '0'), 10);
                    return numA2 - numB2;
                });
                for (const [workName, laborItems] of sortedWorkTypes) {
                    parts.push(`**${workName}**`);
                    parts.push("| 직종 | 수량 | 단위 |");
                    parts.push("| --- | ---: | --- |");
                    laborItems.forEach((l) => {
                        const props = (l.properties || {}) as any;
                        parts.push(
                            `| ${l.related_name} | ${props.quantity ?? "-"} | ${props.unit ?? "인"} |`
                        );
                    });
                    parts.push("");
                }
            } else {
                parts.push(`**[표 ${sectionId}] 투입 인력**\n`);
                parts.push("| 직종 | 수량 | 단위 |");
                parts.push("| --- | ---: | --- |");
                labor.forEach((l) => {
                    const props = (l.properties || {}) as any;
                    parts.push(
                        `| ${l.related_name} | ${props.quantity ?? "-"} | ${props.unit ?? "인"} |`
                    );
                });
                parts.push("");
            }
        }

        // 투입 장비
        const equipment = grouped.get("REQUIRES_EQUIPMENT") || [];
        if (equipment.length > 0) {
            parts.push(`**[표 ${sectionId}] 투입 장비**\n`);
            parts.push("| 장비명 | 수량 | 단위 |");
            parts.push("| --- | ---: | --- |");
            equipment.forEach((eq) => {
                const props = (eq.properties || {}) as any;
                parts.push(
                    `| ${eq.related_name} | ${props.quantity ?? "-"} | ${props.unit ?? "-"} |`
                );
            });
            parts.push("");
        }

        // 사용 자재
        const material = grouped.get("USES_MATERIAL") || [];
        if (material.length > 0) {
            parts.push(`**[표 ${sectionId}] 사용 자재**\n`);
            parts.push("| 자재명 | 수량 | 단위 |");
            parts.push("| --- | ---: | --- |");
            material.forEach((m) => {
                const props = (m.properties || {}) as any;
                parts.push(
                    `| ${m.related_name} | ${props.quantity ?? "-"} | ${props.unit ?? "-"} |`
                );
            });
            parts.push("");
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

// ━━━ [G] 메인 핸들러 ━━━

async function handleChat(
    question: string,
    history: ChatMessage[],
    entityId?: string,
    sectionId?: string,
    sessionContext?: SessionContext,
    answerOptions?: AnswerOptions
): Promise<ChatResponse> {
    const startTime = Date.now();
    const embeddingTokens = Math.ceil(question.length / 2);

    // ═══ Phase -1: entity_id 직접 조회 (칩 선택 시) ═══
    // 복수 entity_id 지원: 쉼표 구분 (예: "W-1061,W-1062,W-1063")
    if (entityId) {
        const entityIds = entityId.split(',').map(s => s.trim()).filter(Boolean);
        console.log(`[handleChat] entity_ids=[${entityIds.join(',')}] → 직접 조회 (clarify 스킵)`);
        const { data: directEntities } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties, source_section")
            .in("id", entityIds);

        if (directEntities && directEntities.length > 0) {
            const entities: EntityResult[] = directEntities.map((de: any) => ({
                id: de.id,
                name: de.name,
                type: de.type,
                properties: de.properties || {},
                source_section: de.source_section,
                similarity: 1.0,
            }));

            // Phase -1: entity_id 직접 전달 → 선택된 entity의 관계만 조회 (section 전체 확장 스킵)
            // Why: 사용자가 셀렉터에서 특정 규격을 선택했으므로, 형제 WorkType 불필요
            const relationsPromises = entities.map((e) => expandGraph(e.id, e.type, true));
            const relationsAll = await Promise.all(relationsPromises);

            let ilwiResults: IlwiItem[] = [];
            if (detectCostIntent(question)) {
                const workTypeEntities = entities.filter((e) => e.type === "WorkType");
                for (const e of workTypeEntities) {
                    const spec = extractSpec(question);
                    const items = await searchIlwi(e.name, spec);
                    if (items.length > 0) { ilwiResults.push(...items); break; }
                }
            }

            // spec 숫자 추출 → chunk tables 필터링
            // Why: "4 두께 인 력(인)" → "4" 추출 → 두께=4 행만 context에 포함
            const firstSpec = entities[0]?.properties?.spec as string || "";
            const specNum = firstSpec.match(/^(\d+)/)?.[1];
            const chunks = await retrieveChunks(entities, specNum);
            if (specNum) console.log(`[handleChat] specFilter="${specNum}" 적용`);
            const context = buildContext(entities, relationsAll, ilwiResults, chunks, specNum);
            const llmResult = await generateAnswer(question, context, history, answerOptions);
            const totalTokens = embeddingTokens + llmResult.inputTokens + llmResult.outputTokens;

            const sourcesWithSection: SourceInfo[] = entities.map((e) => {
                const chunk = chunks.find((c) => c.section_id === e.source_section);
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
                entities, relations: relationsAll,
                ilwi: ilwiResults, chunks,
                embeddingTokens, llmResult,
            });
        }
    }

    // ═══ Phase -0.5: section_id → 섹션 내 하목 선택 또는 전체 보기 ═══
    // Why: Step 1에서 섹션을 선택한 후 Step 2로 드릴다운
    if (sectionId) {
        console.log(`[handleChat] section_id=${sectionId} → 섹션 내 탐색`);

        // full_view: 섹션 전체 원문을 컨텍스트로 답변 생성
        const isFullView = question.includes("전체") || question.includes("목록");
        if (isFullView) {
            console.log(`[handleChat] full_view: ${sectionId} 전체 원문 조회`);
            // ─── 전체 chunk 로딩 (기존 .limit(1) → 전체) ───
            // Why: 강관용접 등은 11개 chunk에 tables 분산 저장 → 전체 필요
            const { data: chunkData } = await supabase
                .from("graph_chunks")
                .select("id, section_id, title, department, chapter, section, text, tables")
                .eq("section_id", sectionId)
                .limit(20);

            let allChunks = (chunkData || []) as any[];

            // sub_section 필터: sectionId에 ":sub=" 포함 시 관련 chunk만 선별
            // Why: "13-2-3:sub=2. TIG용접" → TIG 관련 chunk만 선택하여 context 크기 관리
            const subMatch = sectionId.match(/:sub=(.+)$/);
            const subKeyword = subMatch ? subMatch[1].replace(/^\d+\.\s*/, '') : null;
            if (subKeyword && allChunks.length > 1) {
                const filtered = allChunks.filter(c =>
                    (c.text && c.text.includes(subKeyword)) ||
                    (c.tables && JSON.stringify(c.tables).includes(subKeyword))
                );
                if (filtered.length > 0) {
                    console.log(`[handleChat] sub_section "${subKeyword}" 필터: ${allChunks.length}건 → ${filtered.length}건`);
                    allChunks = filtered;
                }
            }

            // 전체 chunk의 text + tables → 하나의 메타 chunk로 병합
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
                console.log(`[handleChat] full_view: ${allChunks.length}건 chunk 병합, text_len=${chunk.text.length}`);
            }
            if (chunk) {
                // 해당 섹션의 모든 WorkType 관계 가져오기 — 정확 매칭 (I-1 수정)
                // Why: base 보완(# 앞)은 다른 절(9-1-2=토목 vs 9-1-2#3=기계설비)과 충돌
                const { data: sectionWTData } = await supabase
                    .from("graph_entities")
                    .select("id, name, type, properties, source_section")
                    .eq("type", "WorkType")
                    .eq("source_section", sectionId)
                    .limit(20);

                const sectionWTs = (sectionWTData || []) as any[];
                console.log(`[handleChat] full_view: WorkType ${sectionWTs.length}건 (sectionId=${sectionId})`);

                let wtEntities: EntityResult[] = [];
                let relationsAll: any[][] = [];

                if (sectionWTs.length > 0) {
                    // WorkType 있으면 → 각 WT의 관계 확장
                    wtEntities = sectionWTs.map(wt => ({
                        id: wt.id, name: wt.name, type: wt.type,
                        properties: wt.properties || {},
                        source_section: wt.source_section,
                        similarity: 1.0,
                    }));
                    const relationsPromises = wtEntities.map(e => expandGraph(e.id, e.type));
                    relationsAll = await Promise.all(relationsPromises);
                } else {
                    // WorkType 없으면 → 동일 title의 다른 section에서 cross-reference
                    // Why: 품셈서에서 "잡철물 제작 및 설치" 같은 표는 건축/기계설비 등
                    //      여러 부문에 동일 내용으로 중복 수록됨. 한 쪽에만 WorkType이
                    //      등록된 경우, 다른 쪽에서 차용하여 실제 품셈 데이터 제공
                    console.log(`[handleChat] full_view: sectionId=${sectionId} WorkType 0건 → cross-reference 탐색`);

                    const { data: siblingWTs } = await supabase
                        .from("graph_entities")
                        .select("id, name, type, properties, source_section")
                        .eq("type", "WorkType")
                        .in("source_section",
                            // 동일 title의 다른 section_id 목록 조회 (서브쿼리 대체)
                            await (async () => {
                                const { data: siblings } = await supabase
                                    .from("graph_chunks")
                                    .select("section_id")
                                    .eq("title", chunk.title);
                                return [...new Set(
                                    (siblings || [])
                                        .map((s: any) => s.section_id)
                                        .filter((sid: string) => sid !== sectionId)
                                )];
                            })()
                        )
                        .limit(30);

                    if (siblingWTs && siblingWTs.length > 0) {
                        console.log(`[handleChat] full_view: cross-ref에서 ${siblingWTs.length}건 WorkType 발견`);
                        wtEntities = (siblingWTs as any[]).map(wt => ({
                            id: wt.id, name: wt.name, type: wt.type,
                            properties: wt.properties || {},
                            source_section: wt.source_section,
                            similarity: 0.95,  // cross-ref이므로 약간 낮은 유사도
                        }));
                        const relationsPromises = wtEntities.map(e => expandGraph(e.id, e.type));
                        relationsAll = await Promise.all(relationsPromises);
                    } else {
                        // Fix B0-fv: cross-ref 실패 → 하위 절(children) WorkType 탐색
                        // Why: "2-12 공통장비" 같은 상위 절은 본인 WT 0건, cross-ref도 없지만
                        //      하위 절 "2-12-1 건설용리프트", "2-12-2 마스트" 등에 데이터 존재
                        const baseSectionId = sectionId.includes('#') ? sectionId.split('#')[0] : sectionId;
                        const childPrefix = baseSectionId + '-';
                        console.log(`[handleChat] full_view: cross-ref 실패 → 하위 절 탐색 (prefix=${childPrefix})`);

                        const { data: childWTs } = await supabase
                            .from("graph_entities")
                            .select("id, name, type, properties, source_section")
                            .eq("type", "WorkType")
                            .ilike("source_section", `${childPrefix}%`)
                            .limit(50);

                        if (childWTs && childWTs.length > 0) {
                            console.log(`[handleChat] full_view: 하위 절에서 ${childWTs.length}건 WorkType 발견`);
                            wtEntities = (childWTs as any[]).map(wt => ({
                                id: wt.id, name: wt.name, type: wt.type,
                                properties: wt.properties || {},
                                source_section: wt.source_section,
                                similarity: 0.98,
                            }));
                            const relationsPromises = wtEntities.map(e => expandGraph(e.id, e.type));
                            relationsAll = await Promise.all(relationsPromises);

                            // 하위 절 chunk 텍스트도 포함 (원문 보강)
                            const childSectionIds = [...new Set(childWTs.map((w: any) => w.source_section))];
                            const { data: childChunks } = await supabase
                                .from("graph_chunks")
                                .select("id, section_id, title, department, chapter, section, text")
                                .in("section_id", childSectionIds)
                                .limit(10);

                            if (childChunks && childChunks.length > 0) {
                                // 하위 절 원문을 chunk.text에 병합
                                const childTexts = (childChunks as any[])
                                    .filter(c => c.text && c.text.length > 10)
                                    .map(c => `### ${c.section_id} ${c.title}\n${c.text}`)
                                    .join('\n\n');
                                if (childTexts) {
                                    chunk.text = (chunk.text || '') + '\n\n' + childTexts;
                                }
                            }
                        } else {
                            // 하위 절도 없으면 → Section 자체 확장 (최선의 노력)
                            const { data: sectionEntity } = await supabase
                                .from("graph_entities")
                                .select("id, name, type, properties, source_section")
                                .eq("type", "Section")
                                .eq("source_section", sectionId)
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

                // 원문 청크 + 그래프 관계 컨텍스트 조합
                const context = [
                    `## 품셈 원문: ${chunk.title}`,
                    `**출처**: ${chunk.department} > ${chunk.chapter} > ${chunk.title}`,
                    `**표번호**: ${chunk.section_id}`,
                    `\n${chunk.text}`,
                    `\n---\n`,
                    buildContext(wtEntities, relationsAll, [], [chunk as ChunkResult]),
                ].join("\n");

                const llmResult = await generateAnswer(question, context, history);
                const totalTokens = embeddingTokens + llmResult.inputTokens + llmResult.outputTokens;

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
            } else {
                // I-8: chunk 미발견 시 명시적 안내 (full_view 요청인데 원문 없음)
                console.warn(`[handleChat] full_view: section_id=${sectionId} 원문 없음 → 안내`);
                return makeAnswerResponse(
                    `해당 절(${sectionId})의 원문 데이터를 찾을 수 없습니다.\n다른 작업을 선택하거나, 다시 검색해 주세요.`,
                    startTime
                );
            }
        }

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

    // ═══ Phase 0: 의도 분석 (DeepSeek v3.2) ═══
    const analysis = await analyzeIntent(question, history, sessionContext);
    // Phase 3: 규격 정규화 (인치→mm, 파이→mm, SCH 띄어쓰기)
    analysis.spec = normalizeSpec(analysis.spec);

    // ─── 인사/도움말 의도 ───
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

    // ─── 비용 산출 의도 (cost_calculate) ───
    // Why: 이전 턴에서 확정된 품셈에 대해 노무비 계산 요청
    if (analysis.intent === "cost_calculate") {
        const targetEntityId = sessionContext?.last_entity_id;
        if (!targetEntityId) {
            return makeAnswerResponse(
                "노무비를 계산하려면 먼저 품셈을 검색해 주세요.\n\n" +
                "예시: \"강관용접 200mm SCH 40\" 또는 \"TIG용접 품셈\"",
                startTime
            );
        }
        // entity_id가 있으면 → 직접 조회 흐름으로 전환
        console.log(`[handleChat] cost_calculate: entity=${targetEntityId} → 직접 조회 전환`);
        return handleChat(question, history, targetEntityId, undefined, sessionContext, {
            intent: "cost_calculate",
            quantity: analysis.quantity || sessionContext?.last_quantity || undefined,
        });
    }

    // ─── 변경 요청 의도 (modify_request) ───
    // Why: 수량 변경, 공종 변경, 직종 제외 등 이전 결과 기반 수정
    if (analysis.intent === "modify_request") {
        if (analysis.modify_type === "quantity" && sessionContext?.last_entity_id) {
            // 수량만 변경 → 이전 entity로 재조회
            console.log(`[handleChat] modify_request(quantity=${analysis.quantity}): entity=${sessionContext.last_entity_id}`);
            return handleChat(question, history, sessionContext.last_entity_id, undefined, sessionContext, {
                intent: "cost_calculate",
                quantity: analysis.quantity || undefined,
                modifyType: "quantity",
            });
        }
        if (analysis.modify_type === "work_change" && analysis.work_name) {
            // 공종 변경 → 새 work_name으로 search 전환 (이전 spec 유지)
            console.log(`[handleChat] modify_request(work_change): ${analysis.work_name}, spec=${sessionContext?.last_spec}`);
            const modifiedAnalysis: IntentAnalysis = {
                ...analysis,
                intent: analysis.spec || sessionContext?.last_spec ? "search" : "clarify_needed",
                spec: analysis.spec || sessionContext?.last_spec || null,
            };
            // search/clarify 흐름으로 진행 (아래 분기에서 처리)
            Object.assign(analysis, modifiedAnalysis);
        }
        // exclude_labor 또는 미분류 modify_type → 안내 메시지
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

    // ─── 산출서 요청 의도 (report_request) ───
    // Why: 이전 턴의 계산 결과를 정형화된 산출 내역서로 출력
    if (analysis.intent === "report_request") {
        const targetEntityId = sessionContext?.last_entity_id;
        if (!targetEntityId) {
            return makeAnswerResponse(
                "산출서를 만들려면 먼저 품셈을 검색해 주세요.\n\n" +
                "예시: \"강관용접 200mm SCH 40\"",
                startTime
            );
        }
        // entity_id가 있으면 → 직접 조회 흐름으로 전환 (LLM이 산출서 형태로 출력)
        console.log(`[handleChat] report_request: entity=${targetEntityId} → 직접 조회 전환`);
        return handleChat(question, history, targetEntityId, undefined, sessionContext, {
            intent: "report_request",
            quantity: sessionContext?.last_quantity || undefined,
        });
    }

    // ─── 명확화 필요 의도 → 그래프 탐색 후 선택 칩 제시 ───
    if (analysis.intent === "clarify_needed") {
        const clarifyResult = await graphClarify(analysis);

        return makeClarifyResponse(clarifyResult.message, startTime, {
            options: clarifyResult.options,
            reason: analysis.ambiguity_reason || "질문의 범위가 넓어 구체적인 확인이 필요합니다",
            original_query: question,
            selector: clarifyResult.selector,
        });
    }

    // ═══ Phase 1: 검색 (search, followup, quantity_input) ═══

    // [1] 질문 임베딩
    const embedding = await generateEmbedding(question);

    // [2] 의도 분석 결과 기반 타겟 검색
    const entities = await targetSearch(analysis, embedding, question);

    // [2-1] 검색 결과가 Section만 있으면 → Phase 3 방식으로 처리 (I-3 수정)
    // Why: 기존 graphClarify(analysis) 재호출은 sectionId 없이 검색을 반복하여 비효율적
    //       + 복수 섹션 시 section_id 누락 가능
    const sectionOnly = entities.length > 0 && entities.every(e => e.type === "Section");
    if (sectionOnly) {
        const sectionSourceIds = [...new Set(entities.map(e => e.source_section).filter(Boolean))] as string[];

        if (sectionSourceIds.length > 1) {
            // ═══ 복수 섹션: 섹션 선택 칩 직접 생성 (graphClarify 재호출 없음) ═══
            console.log(`[handleChat] Section ${sectionSourceIds.length}개 분야 → 섹션 선택`);

            const { data: chunkMetas } = await supabase
                .from("graph_chunks")
                .select("section_id, department, chapter, title")
                .in("section_id", sectionSourceIds);

            const metaMap = new Map<string, any>();
            for (const cm of (chunkMetas || [])) {
                if (!metaMap.has(cm.section_id)) metaMap.set(cm.section_id, cm);
            }

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

            return makeClarifyResponse(
                `"${question}" 관련 품셈이 **${sectionSourceIds.length}개 분야**에 있습니다.\n어떤 분야의 품셈을 찾으시나요?`,
                startTime,
                {
                    options,
                    reason: `'${entities[0].name}' 관련 품셈이 여러 분야에 존재하여 선택이 필요합니다.`,
                    original_query: question,
                },
                { entities }
            );
        }

        // ═══ 단일 섹션: 하위 WorkType 확인 ═══
        const singleSectionId = sectionSourceIds[0];
        const { data: childWorkTypes } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties, source_section")
            .eq("type", "WorkType")
            .eq("source_section", singleSectionId)  // I-1 적용: eq 정확 매칭
            .limit(200);

        if (childWorkTypes && childWorkTypes.length > 3) {
            // WT > 3 → Step 2: sectionId 전달하여 하목 선택
            console.log(`[handleChat] Section 1개 + WorkType ${childWorkTypes.length}개 → Step 2`);
            const clarifyResult = await graphClarify(
                { ...analysis, intent: "clarify_needed" as const, work_name: analysis.work_name || entities[0].name },
                singleSectionId  // sectionId 전달 (기존: 미전달)
            );
            return makeClarifyResponse(clarifyResult.message, startTime, {
                options: clarifyResult.options,
                reason: `'${entities[0].name}' 하위에 ${childWorkTypes.length}개 작업이 있어 선택이 필요합니다.`,
                original_query: question,
                selector: clarifyResult.selector,
            }, { entities });
        }
        // WT ≤ 3 → 기존 흐름 계속 (Phase 2로 진행하여 답변 생성)
    }

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

    // [3] 그래프 확장 (병렬)
    const relationsPromises = entities.map((e) => expandGraph(e.id, e.type));
    const relationsAll = await Promise.all(relationsPromises);

    // [4] 비용 의도 → 일위대가
    let ilwiResults: IlwiItem[] = [];
    if (detectCostIntent(question)) {
        const workTypeEntities = entities.filter((e) => e.type === "WorkType");
        for (const e of workTypeEntities) {
            const spec = extractSpec(question);
            const items = await searchIlwi(e.name, spec);
            if (items.length > 0) {
                ilwiResults.push(...items);
                break; // 첫 매칭 사용
            }
        }
    }

    // [5] 원문 청크 보강
    const chunks = await retrieveChunks(entities);

    // [6] 컨텍스트 → LLM 답변
    let context = buildContext(entities, relationsAll, ilwiResults, chunks);

    // cost_calculate 또는 report_request 시 노임단가 context에 주입
    const effectiveIntent = answerOptions?.intent || analysis.intent;
    if (effectiveIntent === "cost_calculate" || effectiveIntent === "report_request") {
        // relations에서 직종명 추출
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

    const llmResult = await generateAnswer(question, context, history, {
        intent: effectiveIntent,
        quantity: answerOptions?.quantity || analysis.quantity || undefined,
    });

    // [7] 응답 조립
    const sourcesWithSection: SourceInfo[] = entities.map((e) => {
        const chunk = chunks.find((c) => c.section_id === e.source_section);
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
        entities, relations: relationsAll,
        ilwi: ilwiResults, chunks,
        embeddingTokens, llmResult,
    });
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
