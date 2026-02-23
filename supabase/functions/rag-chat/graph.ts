// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// graph.ts — 그래프 확장 (1-hop 관계 + 계층) + 일위대가 + 원문 청크
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import { supabase } from "./config.ts";
import type { EntityResult, RelatedResource, IlwiItem, ChunkResult } from "./types.ts";

// C-2. 그래프 확장 (1-hop 관계 + 계층)
// skipSectionExpansion: true → 동일 section의 형제 WorkType 확장 생략
//   Phase -1에서 entity_id가 직접 전달된 경우 사용
//   Why: 사용자가 특정 규격(50 SCH20 등)을 선택했을 때,
//        같은 section의 모든 WorkType(15,20,25,...,600)을 확장하면
//        수백 개 관계가 context에 포함되어 LLM이 혼동함
export async function expandGraph(
    entityId: string,
    entityType: string,
    skipSectionExpansion: boolean = false
): Promise<RelatedResource[]> {
    // 1-hop 관계 조회
    const { data: relations, error: relErr } = await supabase.rpc(
        "get_related_resources_v2",
        { p_entity_id: entityId }
    );

    if (relErr) {
        console.error("expandGraph error:", relErr.message);
        return [];
    }

    let allRelations = (relations || []) as RelatedResource[];

    // ─── 동일 Section의 WorkType 확장 함수 (재사용) ───
    // Why: Section, WorkType, Note, Standard 등 어떤 엔티티에서든
    //      source_section이 같으면 해당 절의 전체 자원을 탐색해야 함
    async function expandSectionWorkTypes(sourceSection: string): Promise<void> {
        const { data: workTypes } = await supabase
            .from("graph_entities")
            .select("id, name, type, properties")
            .eq("source_section", sourceSection)
            .eq("type", "WorkType")
            .limit(30);

        if (!workTypes || workTypes.length === 0) return;

        // 이미 allRelations에 포함된 WorkType ID 수집 (중복 방지)
        const existingIds = new Set(
            allRelations
                .filter(r => r.related_type === "WorkType")
                .map(r => r.related_id)
        );

        // 각 WorkType의 관계를 병렬 조회
        const workRelPromises = (workTypes as any[]).map(async (wt: any) => {
            const { data: wtRels } = await supabase.rpc(
                "get_related_resources_v2",
                { p_entity_id: wt.id }
            );
            return { workType: wt, relations: (wtRels || []) as RelatedResource[] };
        });

        const workRelResults = await Promise.all(workRelPromises);

        for (const { workType, relations: wtRels } of workRelResults) {
            // WorkType 자체를 가상 관계로 추가 (중복 제외)
            if (!existingIds.has(workType.id)) {
                allRelations.push({
                    direction: "outbound",
                    relation: "CONTAINS_WORK",
                    related_id: workType.id,
                    related_name: workType.name,
                    related_type: "WorkType",
                    properties: workType.properties || {},
                });
            }
            // WorkType의 REQUIRES_LABOR, REQUIRES_EQUIPMENT, USES_MATERIAL 관계 추가
            // Why: 각 관계에 work_type_name을 주입하여, 출력 시 어떤 규격(15mm, SCH 20 등)의 인력인지 표시
            // Phase 5: sub_section 속성이 있으면 [sub_section] 접두사 추가 → context.ts에서 그룹별 출력
            const subSection = (workType.properties as any)?.sub_section;
            const displayWtName = subSection ? `[${subSection}] ${workType.name}` : workType.name;
            const relevantRels = wtRels.filter((r: RelatedResource) =>
                ["REQUIRES_LABOR", "REQUIRES_EQUIPMENT", "USES_MATERIAL", "HAS_NOTE"].includes(r.relation)
            );
            relevantRels.forEach(r => {
                (r.properties as any).work_type_name = displayWtName;
            });
            allRelations = allRelations.concat(relevantRels);

            // Fix A3: REQUIRES_LABOR 관계 없지만 properties에 quantity/unit이 있는 WT
            // Why: 986개 WT 중 285개가 이 패턴 (LLM이 관계 대신 properties에 직접 저장)
            //      → 가상 REQUIRES_LABOR 관계를 생성하여 인력 테이블에 포함
            const hasLaborRel = relevantRels.some(r => r.relation === "REQUIRES_LABOR");
            const wtProps = workType.properties || {} as any;
            if (!hasLaborRel && wtProps.quantity && wtProps.unit) {
                allRelations.push({
                    direction: "outbound",
                    relation: "REQUIRES_LABOR",
                    related_id: workType.id + "_prop",
                    related_name: wtProps.unit?.includes("인") ? workType.name : workType.name,
                    related_type: "Labor",
                    properties: {
                        quantity: wtProps.quantity,
                        unit: wtProps.unit,
                        work_type_name: displayWtName,
                        source: "properties",
                    },
                });
            }

            // Phase 1: Labor 관계도 없고 properties에도 quantity 없는 WT → unit_costs 원문 폴백
            // Why: TIG용접 등 그래프에 규격별 인력 미저장 WT는 unit_costs 원문 테이블을 직접 활용
            if (!hasLaborRel && !(wtProps.quantity && wtProps.unit)) {
                const { data: rawData } = await supabase
                    .from("unit_costs")
                    .select("content, name")
                    .ilike("content", `%${workType.name}%`)
                    .limit(1);

                if (rawData && rawData.length > 0) {
                    allRelations.push({
                        direction: "outbound",
                        relation: "RAW_TABLE",
                        related_id: workType.id + "_raw",
                        related_name: (rawData[0] as any).name || workType.name,
                        related_type: "RawTable",
                        properties: {
                            raw_content: (rawData[0] as any).content,
                            work_type_name: workType.name,
                            source: "unit_costs_fallback",
                        },
                    });
                    console.log(`[expandGraph] RAW_TABLE fallback for ${workType.name}`);
                }
            }
        }
    }

    // ─── Section 타입: 계층 + WorkType 확장 ───
    if (entityType === "Section") {
        const { data: hierarchy, error: hierErr } = await supabase.rpc(
            "get_entity_hierarchy",
            { p_entity_id: entityId }
        );

        if (!hierErr && hierarchy) {
            allRelations = allRelations.concat(hierarchy as RelatedResource[]);
        }

        // source_section 기준으로 WorkType 2-hop 확장 (skipSectionExpansion이면 생략)
        if (!skipSectionExpansion) {
            const { data: sectionEntities } = await supabase
                .from("graph_entities")
                .select("source_section")
                .eq("id", entityId)
                .limit(1);

            const sectionEntity = (sectionEntities as any[])?.[0];
            if (sectionEntity?.source_section) {
                await expandSectionWorkTypes(sectionEntity.source_section);
            }
        }
    }

    // ─── WorkType/Note/Standard 타입: 형제 WorkType 확장 ───
    // Why: "TIG용접"(WorkType)을 검색하면 W-0634만 나오고,
    //      같은 section(13-2-3)의 강관용접(W-0792~) → REQUIRES_LABOR가 누락됨.
    //      Note/Standard도 마찬가지 — 해당 section 전체 자원을 보여줘야 함.
    // skipSectionExpansion이면 생략: 사용자가 이미 특정 entity를 선택한 상태
    if (!skipSectionExpansion && ["WorkType", "Note", "Standard"].includes(entityType)) {
        // 현재 엔티티의 source_section 조회
        const { data: selfEntities } = await supabase
            .from("graph_entities")
            .select("source_section")
            .eq("id", entityId)
            .limit(1);

        const selfEntity = (selfEntities as any[])?.[0];
        if (selfEntity?.source_section) {
            await expandSectionWorkTypes(selfEntity.source_section);
        }
    }

    // ─── HAS_NOTE 관계의 실제 내용 보강 ───
    // get_related_resources는 대상 엔티티의 properties를 반환하지 않으므로
    // Note 엔티티의 properties.content를 별도 조회하여 관계에 주입
    const noteRelations = allRelations.filter(r => r.relation === "HAS_NOTE");
    if (noteRelations.length > 0) {
        const noteIds = [...new Set(noteRelations.map(r => r.related_id))];
        const { data: noteEntities } = await supabase
            .from("graph_entities")
            .select("id, properties")
            .in("id", noteIds);

        if (noteEntities) {
            const noteContentMap = new Map(
                (noteEntities as any[]).map((e: any) => [
                    e.id,
                    e.properties?.content || null
                ])
            );
            noteRelations.forEach(r => {
                const content = noteContentMap.get(r.related_id);
                if (content) {
                    (r.properties as any).note_content = content;
                }
            });
        }
    }

    return allRelations;
}

// C-3. 일위대가 검색
export async function searchIlwi(
    name: string,
    spec: string | null
): Promise<IlwiItem[]> {
    const { data, error } = await supabase.rpc("search_ilwi", {
        search_name: name,
        search_spec: spec,
    });

    if (error) {
        console.error("searchIlwi error:", error.message);
        return [];
    }

    return (data || []) as IlwiItem[];
}

// C-4. 원문 청크 보강
// specFilter: entity 선택 시 해당 spec(두께 등)에 해당하는 tables 행만 포함
// Why: 강판 전기아크용접(두께=4) 선택 시 전 범위(3~50) 데이터가 context에 범람하는 문제 방지
export async function retrieveChunks(
    entities: EntityResult[],
    specFilter?: string   // 예: "4" (두께=4mm만 필터링)
): Promise<ChunkResult[]> {
    // entity.source_section → graph_chunks.section_id 매칭 (Codex F3)
    const sectionIds = entities
        .map((e) => e.source_section)
        .filter((s): s is string => !!s);

    if (sectionIds.length === 0) return [];

    // 중복 제거
    const uniqueSectionIds = [...new Set(sectionIds)];

    const { data, error } = await supabase
        .from("graph_chunks")
        .select("id, section_id, title, department, chapter, section, text, tables")
        .in("section_id", uniqueSectionIds)
        .limit(15); // 강관용접 등 11개 chunk 커버

    if (error) {
        console.error("retrieveChunks error:", error.message);
        return [];
    }

    const rawChunks = (data || []) as any[];

    // section_id별 그룹화 → 동일 섹션의 여러 chunk를 하나로 병합
    // Why: 강관용접(13-2-3) 등은 11개 chunk에 tables 분산 → 하나로 합쳐야 LLM이 전체 표 확인 가능
    const sectionMap = new Map<string, any>();
    for (const chunk of rawChunks) {
        const sid = chunk.section_id;
        if (!sectionMap.has(sid)) {
            sectionMap.set(sid, { ...chunk, _allTables: [] });
        }
        const merged = sectionMap.get(sid)!;
        // tables 수집
        if (chunk.tables && Array.isArray(chunk.tables)) {
            merged._allTables.push(...chunk.tables);
        }
        // text 병합 (빈 text 제외)
        if (chunk.text && chunk.text.length > 0 && chunk.id !== merged.id) {
            merged.text = (merged.text || "") + "\n" + chunk.text;
        }
    }

    // tables → Markdown 변환 후 text에 추가
    return Array.from(sectionMap.values()).map((chunk) => {
        let fullText = chunk.text || "";
        if (chunk._allTables && chunk._allTables.length > 0) {
            const tablesMarkdown = chunk._allTables.map((t: any) => {
                if (!t.rows || t.rows.length === 0) return "";
                const headers: string[] = t.headers || Object.keys(t.rows[0]);
                let rows = t.rows;

                // specFilter 적용: 첫 번째 header(spec 기준 컬럼)의 값으로 행 필터링
                // Why: "두께=4" 선택 시 두께=4 행만 남기고 나머지(3,5,6...) 제거
                if (specFilter && headers.length > 0) {
                    const specKey = headers[0]; // 예: "구분 자세 및 직종 두께(mm)"
                    const filtered = rows.filter((r: any) => {
                        const val = String(r[specKey] ?? "");
                        return val === specFilter;
                    });
                    if (filtered.length > 0) {
                        rows = filtered;
                        console.log(`[retrieveChunks] specFilter="${specFilter}": ${t.rows.length}행 → ${rows.length}행`);
                    }
                    // 필터 결과가 0건이면 원본 유지 (fallback)
                }

                const headerRow = "| " + headers.join(" | ") + " |";
                const sepRow = "| " + headers.map(() => "---").join(" | ") + " |";
                const dataRows = rows.map((r: any) =>
                    "| " + headers.map((h: string) => r[h] ?? "").join(" | ") + " |"
                );
                return [headerRow, sepRow, ...dataRows].join("\n");
            }).filter(Boolean).join("\n\n");
            fullText += "\n" + tablesMarkdown;
        }
        delete chunk._allTables;
        return {
            ...chunk,
            text: fullText.substring(0, 6000), // 확장: 2000→6000 (다수 표 포함)
        } as ChunkResult;
    });
}

// ─── 노임단가 조회 (labor_costs 테이블) ───
// Why: cost_calculate intent 시 실제 노무비 산출을 위해 직종별 단가 필요
import type { LaborCostEntry } from "./types.ts";

export async function fetchLaborCosts(jobNames: string[]): Promise<LaborCostEntry[]> {
    if (jobNames.length === 0) return [];
    const patterns = jobNames.map(name => name.replace(/\s+/g, '%'));
    const { data, error } = await supabase
        .from("labor_costs")
        .select("job_name, cost_2026")
        .or(patterns.map(p => `job_name.ilike.%${p}%`).join(','));

    if (error || !data) {
        console.error("[fetchLaborCosts] error:", error);
        return [];
    }
    console.log(`[fetchLaborCosts] 조회 ${jobNames.length}개 직종 → ${data.length}건 매칭`);
    return data as LaborCostEntry[];
}

