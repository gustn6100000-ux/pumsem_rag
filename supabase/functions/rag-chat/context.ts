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
        telemetry?: { embedding_ms: number; rpc_ms: number; llm_ms: number; };
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
        ...(opts?.telemetry && { telemetry: opts.telemetry }),
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

