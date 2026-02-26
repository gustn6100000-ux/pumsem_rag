// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// types.ts — 공통 타입/인터페이스 정의
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

export interface ChatMessage {
    role: "user" | "assistant";
    content: string;
}

export interface ChatRequest {
    question: string;
    history?: ChatMessage[];
    entity_id?: string;   // 칩 선택 시 직접 entity 조회용
    section_id?: string;  // Step 2 트리거용 section_id (I-6 수정)
    session_context?: SessionContext;  // 세션 상태 (이전 턴 품셈/수량 정보)
}

// 세션 상태 (프론트엔드 → 서버 전달)
// Why: "아까 건", "그거 말고" 같은 맥락 참조 질문 해석에 필요
export interface SessionContext {
    last_entity_id: string | null;       // 마지막 확정 품셈 entity ID (예: "W-0788")
    last_work_name: string | null;       // "강관용접(200, SCH 40)"
    last_spec: string | null;            // "200 SCH 40"
    last_quantity: number | null;        // 50
    last_section_id: string | null;      // "13-2-3"
}

export interface SourceInfo {
    entity_id?: string;
    entity_name: string;
    entity_type: string;
    source_section?: string;
    section_label?: string;
    similarity?: number;
}

export interface TokenUsage {
    embedding_tokens: number;
    llm_input_tokens: number;
    llm_output_tokens: number;
    total_tokens: number;
    estimated_cost_krw: number;
}

export interface SearchInfo {
    entities_found: number;
    relations_expanded: number;
    ilwi_matched: number;
    chunks_retrieved: number;
    latency_ms: number;
    telemetry?: {
        embedding_ms: number;
        rpc_ms: number;
        llm_ms: number;
    };
    token_usage?: TokenUsage;
}

// 명확화 선택 옵션
export interface ClarifyOption {
    label: string;           // 표시 텍스트: "200mm SCH 40"
    query: string;           // 클릭 시 전송할 질문
    entity_id?: string;      // 직접 검색용 ID (옵션)
    source_section?: string; // 출처 절번호
    section_id?: string;     // Step 2 트리거용 section_id (graph_chunks 키)
    option_type?: 'section' | 'worktype' | 'full_view';  // 옵션 유형
}

export interface ClarificationInfo {
    options: ClarifyOption[];
    reason: string;
    original_query: string;
    selector?: SelectorPanel;  // 6개 초과 시 드롭다운+체크박스 패널
}

// ─── Selector Panel 타입 (명확화 UI 개선) ───

export interface FilterAxis {
    key: string;       // "diameter" | "sch"
    label: string;     // "호칭경(mm)" | "SCH"
    values: string[];  // ["15","20","25",...]
}

export interface SelectorItem {
    label: string;
    query: string;
    entity_id?: string;
    source_section?: string;
    option_type?: string;  // 'worktype' | 'section'
    specs: Record<string, string>;  // { diameter: "200", sch: "40" }
}

export interface SelectorPanel {
    title: string;              // "강관용접 — 규격 선택"
    filters: FilterAxis[];      // 필터 축 (호칭경, SCH 등)
    items: SelectorItem[];      // 전체 항목 (필터링 전)
    original_query: string;
}

export interface ChatResponse {
    type: "answer" | "clarify";     // 응답 유형
    answer: string;
    sources: SourceInfo[];
    search_info: SearchInfo;
    clarification?: ClarificationInfo; // 명확화 정보 (type=clarify 시)
}

// 의도 분석 결과
export interface IntentAnalysis {
    intent: "search" | "clarify_needed" | "followup" | "greeting" | "quantity_input" | "cost_calculate" | "modify_request" | "report_request" | "complex_estimate";
    work_name: string | null;
    spec: string | null;
    keywords: string[];
    ambiguity_reason: string | null;
    modify_type?: "quantity" | "work_change" | "exclude_labor" | null;  // modify_request 세부 유형
    quantity?: number | null;  // quantity_input/modify_request 시 수량 값
    complexity?: "simple" | "complex"; // 쿼리 복잡도 (라우팅 용)
}

// 검색 결과 엔티티
export interface EntityResult {
    id: string;
    name: string;
    type: string;
    properties: Record<string, unknown>;
    similarity: number;
    source_section?: string; // 추가 조회로 채움
}

// 그래프 관계
export interface RelatedResource {
    direction: string;
    relation: string;
    related_id: string;
    related_name: string;
    related_type: string;
    properties: Record<string, unknown>;
}

// 일위대가 항목
export interface IlwiItem {
    id: number;
    name: string;
    spec: string;
    labor_cost: number;
    material_cost: number;
    expense_cost: number;
    total_cost: number;
}

// 품셈 표 데이터 (graph_chunks.tables JSON 구조)
export interface TableData {
    table_id: string;
    type: string;
    headers: string[];
    rows: Record<string, any>[];
    raw_row_count: number;
    parsed_row_count: number;
    notes_in_table?: string[];
}

// 원문 청크
export interface ChunkResult {
    id: string;
    section_id: string;
    title: string;
    department: string;
    chapter: string;
    section: string;
    text: string; // DB 컬럼명: text (not content)
    tables?: TableData[];   // 품셈 표 데이터
    notes?: any[];          // 주의사항
}

// 명확화 결과
export interface ClarifyResult {
    message: string;
    options: ClarifyOption[];
    selector?: SelectorPanel;  // 6개 초과 시 Selector Panel 데이터
}

// LLM 응답 결과
export interface LLMResult {
    answer: string;
    inputTokens: number;
    outputTokens: number;
}

// ─── 답변 생성 옵션 (intent→답변 연결) ───
export interface AnswerOptions {
    intent?: string;       // "cost_calculate" | "report_request" | "modify_request" 등
    quantity?: number;     // 사용자 지정 수량 (50m, 10개소 등)
    modifyType?: string;   // "quantity" | "work_change"
}

// ─── 노임단가 항목 ───
export interface LaborCostEntry {
    job_name: string;
    cost_2026: number;
}
