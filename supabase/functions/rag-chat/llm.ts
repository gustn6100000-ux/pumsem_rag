// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// llm.ts — LLM 답변 생성 (DeepSeek 우선, Gemini 폴백)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import { GEMINI_API_KEY, DEEPSEEK_API_KEY, DEEPSEEK_URL } from "./config.ts";
import type { ChatMessage, LLMResult, AnswerOptions } from "./types.ts";

export const SYSTEM_PROMPT = `당신은 건설 공사 품셈(標準品셈) 전문 AI 어시스턴트입니다.

[역할]
- 사용자의 건설 공사 관련 질문에 대해 품셈 데이터를 기반으로 정확하게 답변합니다.
- 답변 시 반드시 제공된 컨텍스트의 데이터만 사용하며, 컨텍스트에 없는 정보는 추측하지 않습니다.

[품셈 도메인 지식]
1. **품셈서 구조**: 부문 > 장 > 절 > 표번호 형태. 표번호(예: 13-2-3)는 품셈서 내 고유 식별자입니다.
2. **표 구조**: 각 표는 공종명 + 규격별 소테이블로 구성됩니다.
   - 규격 예: "강관용접(200, SCH 40)" = 호칭경 200mm, 스케줄 40
   - 각 규격 아래에 직종/수량/단위 테이블이 나옵니다.
3. **코드 체계**: "7205-0540" 같은 숫자는 건설기계 분류 코드입니다.
   - 앞 4자리: 대분류, 뒤 4자리: 세부 분류
   - 이 코드로부터 장비명을 유추할 수 있습니다.
4. **계수/보정값**: "계수 A~E" 등은 조건별 보정 계수입니다.
   원본 테이블에서 조건에 맞는 행을 찾아 해당 계수를 적용합니다.
5. **단위 체계**:
   - "인" = 1인 1일 노동량 (8시간 기준). 0.122인 = 약 58분(0.122 × 8시간 × 60분)의 노동
   - "대" = 장비 1대 1일(8시간) 가동
   - "㎡", "㎥", "m", "개소", "본" 등은 시공 단위
6. **속성(properties)**: 컨텍스트의 "속성" 필드에 규격, 수량, 단위 등 세부 정보가 포함됩니다.
   이 데이터를 활용하여 정확한 수치를 답변하세요.

[답변 규칙]
1. **표번호 필수 표기**: 답변 시 해당 품셈의 표번호(예: [표 13-5-1])를 반드시 표기합니다. 표번호는 컨텍스트의 "표번호" 필드에서 가져옵니다.
2. **출처 명시**: 답변에 사용한 품셈 항목의 출처(부문 > 장 > 절 > 표번호)를 반드시 표기합니다.
3. **표 형식 — 컨텍스트 구조 유지**: 컨텍스트에 제공된 테이블 구조를 **있는 그대로** 유지하여 출력합니다.
   - **매트릭스(교차표)**: 행=직종/장비/자재, 열=규격·조건일 때 교차 구조를 그대로 출력합니다. 절대 4열 플랫 테이블로 분해하지 않습니다.
   - **심플 테이블**: 기준이 1개인 단순 데이터는 기존 \`| 직종 | 수량 | 단위 | 기준 |\` 구조를 유지합니다.
   - 단위는 반드시 데이터에 표기된 값을 그대로 사용합니다. 임의 변경 금지.
4. **수량 정확성**: 인력, 장비, 자재의 수량과 단위를 정확하게 표기합니다.
   예: "보통인부 0.122인", "콘크리트공 0.045인"
5. **비용 답변 시**: 일위대가 정보가 제공되면, 노무비/재료비/경비/합계를 표 형태로 정리합니다.
6. **주의사항 포함**: 할증, 적용 조건, 제한 사항이 있으면 반드시 언급합니다.
7. **같은 절(표) 내 데이터 활용**: 질문한 작업의 자체 인력/장비 데이터가 직접 없더라도,
   컨텍스트에 같은 절(section)의 형제 작업 데이터가 포함되어 있다면
   반드시 해당 데이터를 테이블로 출력합니다. 같은 절 내의 데이터는 동일 품셈표의 일부이므로 관련성이 있습니다.
   "데이터를 찾을 수 없습니다"라고만 답하지 않습니다.
8. **정보 부족 시**: 컨텍스트에 관련 데이터가 전혀 없는 경우에만 "제공된 품셈 데이터에서 해당 정보를 찾을 수 없습니다"라고 답합니다.
   단, 같은 절의 형제 데이터가 있으면 그것을 먼저 출력한 후 "질문하신 특정 항목의 별도 데이터는 포함되지 않았습니다"라고 보충합니다.
9. **마크다운 형식**: 답변은 마크다운 형식으로 작성하여 가독성을 높입니다.

[출력 포맷 예시 — 매트릭스(교차표)]

📋 **[표 13-2-3] 강관용접 — 전기아크용접** (개소당)
📍 출처: 기계설비부문 > 제13장 플랜트설비공사 > 강관용접

| 직종 | 200, SCH 20 | 200, SCH 30 | 200, SCH 40 | 200, SCH 60 | 200, SCH 80 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 용접공 | 0.287 | 0.287 | — | — | — |
| 플랜트용접공 | — | — | 0.287 | 0.325 | 0.362 |
| 특별인부 | 0.086 | 0.086 | 0.086 | 0.098 | 0.109 |

※ 매트릭스 테이블은 행에 직종/장비, 열에 규격·조건을 배치하여 한눈에 비교할 수 있게 구성합니다.
※ 단위(개소당/m당/ton당 등)는 데이터에 포함된 값을 그대로 사용하세요.

[금지 사항]
- 컨텍스트에 없는 수치나 기준을 임의로 생성하지 않습니다.
- "일반적으로", "보통", "대략" 등 모호한 표현 대신 정확한 수치를 사용합니다.
- 건설 관련이 아닌 질문에는 "건설 품셈 관련 질문에만 답변할 수 있습니다"라고 응답합니다.
- 단위를 임의로 바꾸지 않습니다. 주어지는 데이터를 그대로 씁니다.
- 매트릭스 교차표를 4열 플랫 테이블(직종/수량/단위/기준)로 분해하지 않습니다. 컨텍스트 그대로 출력합니다.`;

// Why: 임베딩은 DB 벡터 호환성 때문에 Gemini 유지 (13,387개 엔티티 재임베딩 불가)
//       답변 생성만 DeepSeek v3.2로 전환

export async function generateAnswer(
    question: string,
    context: string,
    history: ChatMessage[],
    options?: AnswerOptions
): Promise<LLMResult> {
    // ─── intent별 프롬프트 동적 부착 ───
    let systemContent = SYSTEM_PROMPT;

    if (options?.intent === "cost_calculate") {
        systemContent += `\n\n[특별 지침: 노무비 산출]
사용자가 노무비 / 인건비 계산을 요청했습니다.
1. 품셈 인력 데이터(직종, 수량, 단위)를 기반으로 노무비를 산출하세요.
2. 수량이 ${options.quantity || '미지정'}${options.quantity ? ` (${options.quantity})` : ''}으로 주어졌습니다.
3. 노무비 산출 형식(반드시 이 테이블 형태로):
   | 직종 | 투입인원(인 / 개소) | 수량 | 총 투입(M / D) | 노임단가(원 / 일) | 소계(원) |
    4. 컨텍스트에[2026년 노임단가] 섹선이 있으면 해당 단가를 사용하세요.
5. 합계 행을 추가하고, 총 노무비를 굵은 글씨로 표기하세요.
6. 수량이 미지정이면 "1개소당" 기준으로 산출하세요.`;
    }

    if (options?.intent === "report_request") {
        systemContent += `\n\n[특별 지침: 산출서 형태 출력]
사용자가 산출서 / 내역서를 요청했습니다.
1. 정형화된 산출 내역서 형태로 출력하세요.
2. 포함 항목: 품셈 출처(표번호, 절), 규격, 인력 투입 테이블, 노무비 산출 테이블, 합계
3. 수량이 ${options.quantity || '미지정'}으로 주어졌습니다.
4. 표번호, 출처 정보를 상단에 명시하세요.
5. 최종 합계 금액을 강조 표시하세요.`;
    }

    if (options?.quantity && options.intent !== "cost_calculate" && options.intent !== "report_request") {
        systemContent += `\n\n[수량 정보]
사용자가 수량 ${options.quantity}을 지정했습니다.
품셈 인력 / 장비 수량에 이 값을 곱하여 총 투입량을 계산해 주세요.`;
    }

    // ─── DeepSeek 우선 시도 ───
    if (DEEPSEEK_API_KEY) {
        try {
            const messages = [
                { role: "system" as const, content: systemContent },
                ...history.slice(-5).map((msg) => ({
                    role: msg.role === "user" ? "user" as const : "assistant" as const,
                    content: msg.content,
                })),
                {
                    role: "user" as const,
                    content: `[질문]\n${question}\n\n[참고 데이터]\n${context}`,

                },
            ];

            const response = await fetch(DEEPSEEK_URL, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${DEEPSEEK_API_KEY}`,
                },
                body: JSON.stringify({
                    model: "deepseek-chat",
                    messages,
                    temperature: 0.3,
                    max_tokens: 4096,
                }),
            });

            if (response.ok) {
                const data = await response.json();
                const answer = data.choices?.[0]?.message?.content ?? "답변 생성에 실패했습니다.";
                const usage = data.usage || {};
                return {
                    answer,
                    inputTokens: usage.prompt_tokens || 0,
                    outputTokens: usage.completion_tokens || 0,
                };
            }
            console.error(`[generateAnswer] DeepSeek failed: ${response.status}, falling back to Gemini`);
        } catch (err) {
            console.error("[generateAnswer] DeepSeek error:", err, "falling back to Gemini");
        }
    }

    // ─── Gemini 폴백 ───
    const GEMINI_LLM_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent";
    const contents = [
        ...history.slice(-5).map((msg) => ({
            role: msg.role === "user" ? "user" : "model",
            parts: [{ text: msg.content }],
        })),
        {
            role: "user",
            parts: [{ text: `[질문]\n${question}\n\n[참고 데이터]\n${context}` }],
        },
    ];

    const response = await fetch(`${GEMINI_LLM_URL}?key=${GEMINI_API_KEY}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            system_instruction: { parts: [{ text: systemContent }] },
            contents,
            generationConfig: { temperature: 0.3, maxOutputTokens: 4096 },
        }),
    });

    if (!response.ok) {
        throw new Error(`LLM API failed: ${response.status}`);
    }

    const data = await response.json();
    const answer = data.candidates?.[0]?.content?.parts?.[0]?.text ?? "답변 생성에 실패했습니다.";
    const usage = data.usageMetadata || {};
    return {
        answer,
        inputTokens: usage.promptTokenCount || 0,
        outputTokens: usage.candidatesTokenCount || 0,
    };
}
