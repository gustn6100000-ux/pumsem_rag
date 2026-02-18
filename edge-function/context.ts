// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// context.ts — LLM 컨텍스트 조합 + 응답 조립 헬퍼
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import type {
    EntityResult, RelatedResource, IlwiItem, ChunkResult,
    ChatResponse, SearchInfo, TokenUsage, SourceInfo,
    ClarifyOption, ClarificationInfo, SelectorPanel, LLMResult,
} from "./types.ts";

// ━━━ 응답 조립 헬퍼 ━━━
// Why: handleChat 내 6곳+ 반복되는 응답 객체 조립을 통합하여 유지보수성 향상
// 응답 구조 변경 시 이 함수만 수정하면 됨

/**
 * 빈 SearchInfo 생성 (entities/relations 없는 간단 응답용)
 */
export function makeEmptySearchInfo(startTime: number): SearchInfo {
    return {
        entities_found: 0,
        relations_expanded: 0,
        ilwi_matched: 0,
        chunks_retrieved: 0,
        latency_ms: Date.now() - startTime,
    };
}

/**
 * "answer" 타입 응답 생성
 * @param answer - LLM 답변 텍스트
 * @param startTime - 처리 시작 시간 (latency 계산용)
 * @param opts - 선택적 응답 데이터 (sources, entities, relations, llmResult 등)
 */
export function makeAnswerResponse(
    answer: string,
    startTime: number,
    opts?: {
        sources?: SourceInfo[];
        entities?: EntityResult[];
        relations?: RelatedResource[][];
        ilwi?: IlwiItem[];
        chunks?: ChunkResult[];
        embeddingTokens?: number;
        llmResult?: LLMResult;
    }
): ChatResponse {
    const searchInfo: SearchInfo = {
        entities_found: opts?.entities?.length || 0,
        relations_expanded: opts?.relations
            ? opts.relations.reduce((sum, r) => sum + r.length, 0)
            : 0,
        ilwi_matched: opts?.ilwi?.length || 0,
        chunks_retrieved: opts?.chunks?.length || 0,
        latency_ms: Date.now() - startTime,
    };

    // token_usage는 llmResult가 있을 때만 포함
    if (opts?.llmResult) {
        const et = opts.embeddingTokens || 0;
        const totalTokens = et + opts.llmResult.inputTokens + opts.llmResult.outputTokens;
        searchInfo.token_usage = {
            embedding_tokens: et,
            llm_input_tokens: opts.llmResult.inputTokens,
            llm_output_tokens: opts.llmResult.outputTokens,
            total_tokens: totalTokens,
            estimated_cost_krw: parseFloat((totalTokens * 0.0002).toFixed(2)),
        };
    }

    return {
        type: "answer",
        answer,
        sources: opts?.sources || [],
        search_info: searchInfo,
    };
}

/**
 * "clarify" 타입 응답 생성
 * @param message - 사용자에게 보여줄 메시지
 * @param startTime - 처리 시작 시간
 * @param clarification - 명확화 옵션 데이터
 * @param opts - 추가 검색 정보 (entities 수 등)
 */
export function makeClarifyResponse(
    message: string,
    startTime: number,
    clarification: {
        options: ClarifyOption[];
        reason: string;
        original_query: string;
        selector?: SelectorPanel;
    },
    opts?: {
        entities?: EntityResult[];
    }
): ChatResponse {
    return {
        type: "clarify",
        answer: message,
        sources: [],
        search_info: {
            entities_found: opts?.entities?.length || 0,
            relations_expanded: 0,
            ilwi_matched: 0,
            chunks_retrieved: 0,
            latency_ms: Date.now() - startTime,
        },
        clarification: clarification as ClarificationInfo,
    };
}

// ─── 매트릭스 패턴 감지 (구경, SCH) ───
// 예: "강관용접(200, SCH 40)" → baseName="강관용접", pipeSize="200", schNo="SCH 40"
const MATRIX_PATTERN = /^(.+)\((\d+),\s*(SCH\s*\d+)\)$/;

// ─── 매트릭스 테이블 렌더링 ───
function renderMatrixTable(
    baseName: string,
    sectionId: string,
    matrix: Map<string, Map<string, number>>,
    allColumns: string[]
): string {
    const lines: string[] = [];

    // 컬럼 헤더 (SCH+직종) 정렬
    const sortedCols = [...allColumns].sort((a, b) => {
        const schA = parseInt(a.match(/SCH\s*(\d+)/)?.[1] || "0");
        const schB = parseInt(b.match(/SCH\s*(\d+)/)?.[1] || "0");
        if (schA !== schB) return schA - schB;
        return a.localeCompare(b);
    });

    lines.push(`**[표 ${sectionId}] ${baseName} 투입 인력 (개소당)**\n`);
    lines.push(`| 구경(mm) | ${sortedCols.join(" | ")} |`);
    lines.push(`| ---: | ${sortedCols.map(() => "---:").join(" | ")} |`);

    // 구경 행 정렬 (숫자순)
    const pipeSizes = [...matrix.keys()].sort((a, b) => parseInt(a) - parseInt(b));
    for (const ps of pipeSizes) {
        const row = matrix.get(ps)!;
        const cells = sortedCols.map(col => {
            const val = row.get(col);
            return val !== undefined ? val.toString() : "-";
        });
        lines.push(`| ${ps} | ${cells.join(" | ")} |`);
    }
    lines.push("");
    return lines.join("\n");
}

export function buildContext(
    entities: EntityResult[],
    relationsAll: RelatedResource[][],
    ilwiResults: IlwiItem[],
    chunks: ChunkResult[]
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

        // ─── Phase 1: RAW_TABLE 원문 폴백 출력 ───
        // Why: TIG용접 등 그래프에 규격별 인력 미저장 WT는 unit_costs 원문 직접 표시
        const rawTables = grouped.get("RAW_TABLE") || [];
        if (rawTables.length > 0) {
            const seen = new Set<string>();
            rawTables.forEach((rt) => {
                const rawContent = (rt.properties as any)?.raw_content;
                const wtName = (rt.properties as any)?.work_type_name || rt.related_name;
                if (rawContent && !seen.has(wtName)) {
                    seen.add(wtName);
                    parts.push(`**[표 ${sectionId}] ${wtName} 원문 품셈표**\n`);
                    // 원문 테이블 5000자 제한
                    parts.push(rawContent.substring(0, 5000));
                    parts.push("");
                }
            });
        }

        // ─── Phase 2: 매트릭스 패턴 감지 및 통합 테이블 ───
        const labor = grouped.get("REQUIRES_LABOR") || [];
        if (labor.length > 0) {
            const hasWorkType = labor.some(l => (l.properties as any)?.work_type_name);

            if (hasWorkType) {
                // work_type_name에서 매트릭스 패턴 감지
                // 매트릭스: Map<baseName, Map<pipeSize, Map<colKey, quantity>>>
                const matrixData = new Map<string, Map<string, Map<string, number>>>();
                const matrixColumns = new Map<string, Set<string>>();
                const nonMatrixLabor = new Map<string, RelatedResource[]>();

                labor.forEach((l) => {
                    const wt = (l.properties as any)?.work_type_name || "기타";
                    const match = wt.match(MATRIX_PATTERN);

                    if (match) {
                        const [, baseName, pipeSize, schNo] = match;
                        const props = (l.properties || {}) as any;
                        const colKey = `${schNo} ${l.related_name}`;

                        if (!matrixData.has(baseName)) {
                            matrixData.set(baseName, new Map());
                            matrixColumns.set(baseName, new Set());
                        }
                        const baseMatrix = matrixData.get(baseName)!;
                        if (!baseMatrix.has(pipeSize)) baseMatrix.set(pipeSize, new Map());
                        baseMatrix.get(pipeSize)!.set(colKey, props.quantity ?? 0);
                        matrixColumns.get(baseName)!.add(colKey);
                    } else {
                        // 비매트릭스 패턴 → 기존 개별 테이블
                        if (!nonMatrixLabor.has(wt)) nonMatrixLabor.set(wt, []);
                        nonMatrixLabor.get(wt)!.push(l);
                    }
                });

                // 매트릭스 테이블 출력
                for (const [baseName, matrix] of matrixData) {
                    const cols = [...(matrixColumns.get(baseName) || [])];
                    if (matrix.size >= 3 && cols.length >= 2) {
                        // 충분한 데이터 → 매트릭스 출력
                        parts.push(renderMatrixTable(baseName, sectionId, matrix, cols));
                    } else {
                        // 데이터 부족 → 기존 개별 출력으로 폴백
                        for (const [ps, row] of matrix) {
                            const wtName = `${baseName}(${ps})`;
                            parts.push(`**${wtName}**`);
                            parts.push("| 직종 | 수량 | 단위 |");
                            parts.push("| --- | ---: | --- |");
                            for (const [colKey, qty] of row) {
                                const laborName = colKey.replace(/SCH\s*\d+\s*/, "");
                                parts.push(`| ${laborName} | ${qty} | 인 |`);
                            }
                            parts.push("");
                        }
                    }
                }

                // 비매트릭스 패턴 개별 테이블 출력
                if (nonMatrixLabor.size > 0) {
                    parts.push(`**[표 ${sectionId}] 투입 인력**\n`);
                    for (const [workName, laborItems] of nonMatrixLabor) {
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

        // 주의사항 — Note 엔티티의 실제 내용 표시
        const notes = grouped.get("HAS_NOTE") || [];
        if (notes.length > 0) {
            parts.push(`**[표 ${sectionId}] 주의사항**\n`);
            notes.forEach((n) => {
                const noteContent = (n.properties as any)?.note_content;
                parts.push(`- ${noteContent || n.related_name}`);
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
    if (chunks.length > 0) {
        parts.push("## 원문 참고 (품셈 원문)\n");
        chunks.forEach((chunk) => {
            parts.push(`> **${chunk.section_id} ${chunk.title}**`);
            parts.push(`> ${chunk.text}`);
            parts.push("");
        });
    }

    return parts.join("\n");
}
