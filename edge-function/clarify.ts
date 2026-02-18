// clarify.ts - Intent analysis + graph-based clarification
import { DEEPSEEK_API_KEY, DEEPSEEK_URL } from "./config.ts";
import type { IntentAnalysis, ChatMessage, SessionContext, ClarifyResult, ClarifyOption } from "./types.ts";
export type { ClarifyResult };
// ━━━ [E] 의도 감지 + 명확화 ━━━

const COST_KEYWORDS = [
    "비용", "단가", "가격", "원", "얼마", "일위대가",
    "재료비", "노무비", "경비", "합계", "산출", "견적",
    "공사비", "원가", "금액",
];

export function detectCostIntent(question: string): boolean {
    return COST_KEYWORDS.some((kw) => question.includes(kw));
}

const SPEC_PATTERNS = [
    /D\d+/i,        // D80, D100
    /\d+mm/i,       // 100mm, 200mm
    /\d+톤/,        // 10톤, 25톤
    /\d+m[³²]?/,    // 0.7m³, 100m²
    /\d+-\d+-\d+/,  // 25-180-12 (레미콘 규격)
];

export function extractSpec(question: string): string | null {
    for (const pattern of SPEC_PATTERNS) {
        const match = question.match(pattern);
        if (match) return match[0];
    }
    return null;
}

// ─── E-1. DeepSeek v3.2 기반 의도 분석 ───
// Why: 규칙 기반 의도 분류의 한계(영문 약어, 동의어, 맥락 이해 불가)를
//      LLM 구조화 출력으로 해결. 비용 ~₩1/호출로 무시 가능.
const INTENT_SYSTEM_PROMPT = `당신은 건설 공사 품셈 검색 시스템의 의도 분석기입니다.
사용자의 질문을 분석하여 반드시 다음 JSON만 반환하십시오.

## ⚠️ 중요: intent는 반드시 아래 5개 중 하나만 사용

{
  "intent": "search" | "clarify_needed" | "followup" | "greeting" | "quantity_input",
  "work_name": "공종명 한글 (예: 강관용접, 잡철물, TIG용접) 또는 null",
  "spec": "규격 (예: 200 SCH 40, D110, 2톤) 또는 null",
  "keywords": ["검색용", "키워드"],
  "ambiguity_reason": "모호한 이유 또는 null"
}

## 의도 판별 기준

### search (바로 검색 가능)
- 공종명이 명확하고, 규격이 특정되어 단일 품셈을 바로 찾을 수 있는 경우
- 예: "강관용접 200mm SCH 40 품셈", "콘크리트 타설 인력", "거푸집 설치"

### clarify_needed (되물어야 함) ← 의심스러우면 이것 선택
- 공종명은 있지만 규격별 세분화가 필요한데 규격 미지정 → clarify_needed
  예: "강관용접 품셈" → 규격 필요
- 공종명이 넓은 범위여서 하위 분류 선택이 필요 → clarify_needed
  예: "잡철물 제작", "용접", "배관" → 구체적 공종/규격 확인 필요
- 수량(2톤, 10m)은 있지만 상세 규격/종류가 불명확 → clarify_needed
  예: "잡철물 2톤 제작" → 잡철물의 종류(규격철물? 현장제작?) 확인 필요
- 약어/영문만 있어 확인 필요 → clarify_needed
  예: "tig" → TIG용접 확인 필요

### followup (이전 대화 후속)
- 이전 대화 맥락의 추가 질문. 예: "SCH 80은?", "장비는?"

### greeting (인사/도움말)
- "안녕", "뭘 할 수 있어?"

### quantity_input (수량 계산)
- 이전에 품셈이 이미 검색된 상태에서 수량만 입력. 예: "10개소", "50m 계산해줘"

## 키워드 추출 규칙
- 영문 약어 → 한글 변환: "tig" → ["TIG", "TIG용접"]
- ⭐ 한글 외래어 → 영문 원어 번역 (필수!): 건설 용어가 한글로 들어오면 영문 원어도 keywords에 반드시 포함
  예: "크러셔" → ["크러셔", "Crusher"], "플랜지" → ["플랜지", "Flange"], "그라인딩" → ["그라인딩", "Grinding"]
  예: "에이치빔" → ["에이치빔", "H-Beam"], "피브이씨" → ["피브이씨", "PVC"], "티그" → ["티그", "TIG"]
  예: "히터" → ["히터", "Heater"], "탱크" → ["탱크", "Tank", "STORAGE TANK"]
  예: "지엔에스에스" → ["지엔에스에스", "GNSS"], "스토리지" → ["스토리지", "STORAGE"]
- ⭐ work_name도 동일하게: 한글 외래어가 공종명이면 영문 원어를 work_name에 사용
  예: "크러셔 운전" → work_name: "Crusher", "플랜지 취부" → work_name: "Flange 취부"
- 규격 정규화: "200mm" → "200", "SCH40" → "SCH 40"
- 불용어 제외: "품셈", "알려줘", "얼마", "인력", "투입", "관련"
- 동의어 확장: "PE관" → ["PE관", "HDPE관"], "HDPE관" → ["HDPE관", "PE관", "PE", "폴리에틸렌"]
- ⭐ 약어/접두어 확장: HDPE는 PE의 하위 종류이므로 반드시 PE도 keywords에 포함
  예: "HDPE관" → keywords: ["HDPE관", "PE관", "PE", "폴리에틸렌"], work_name: "PE"

## 대화 히스토리 활용
- 이전 대화에서 확정된 공종명을 후속 질문에 복원
  예: 이전 "강관용접 200mm SCH 40" → 현재 "SCH 80은?" → work_name: "강관용접", spec: "SCH 80"`;

// ─── 규칙 기반 의도 분석 (DeepSeek 폴백) ───
// Why: API 키 미설정 또는 API 장애 시에도 기본적인 키워드 추출 보장

// 한글 외래어 → 영문 원어 번역 딕셔너리
// Why: DeepSeek이 처리하는 것이 정석이지만, API 장애 시에도
//      "크러셔" → "Crusher", "플랜지" → "Flange" 변환 보장
export const KO_EN_DICT: Record<string, string[]> = {
    "크러셔": ["Crusher"], "크라셔": ["Crusher"],
    "플랜지": ["Flange"], "플렌지": ["Flange"],
    "그라인딩": ["Grinding"], "그라인더": ["Grinding"],
    "에이치빔": ["H-Beam"], "히터": ["Heater"],
    "피브이씨": ["PVC"], "스토리지": ["STORAGE"],
    "탱크": ["Tank", "STORAGE TANK"],
    "티그": ["TIG", "TIG용접"], "미그": ["MIG"],
    "지엔에스에스": ["GNSS"], "인버터": ["Inverter"],
    "컨베이어": ["Conveyor"], "호퍼": ["Hopper"],
    "콤프레서": ["Compressor"], "컴프레셔": ["Compressor"],
    "펌프": ["Pump"], "밸브": ["Valve"],
    "보일러": ["Boiler"], "덕트": ["Duct"],
    "에이치디피이": ["HDPE", "PE"], "피이": ["PE"],
    "트랜스": ["Transformer"], "케이블": ["Cable"],
    "브레이커": ["Breaker"], "불도저": ["Bulldozer"],
    "로더": ["Loader"], "덤프": ["Dump"],
    "롤러": ["Roller"], "크레인": ["Crane"],
    "백호우": ["Backhoe"], "그래더": ["Grader"],
    "스크레이퍼": ["Scraper"], "페이버": ["Paver"],
    "피니셔": ["Finisher"], "스프레더": ["Spreader"],
    "바이브레이터": ["Vibrator"], "해머": ["Hammer"],
    "앵커": ["Anchor"], "와이어": ["Wire"],
    "배럴": ["Barrel"], "실링": ["Sealing"],
    "코킹": ["Caulking"], "프라이머": ["Primer"],
};

export function ruleBasedIntent(question: string): IntentAnalysis {
    // 인사 감지
    if (/^(안녕|반가|도움|뭘\s*할|할\s*수|help)/i.test(question)) {
        return { intent: "greeting", work_name: null, spec: null, keywords: [], ambiguity_reason: null };
    }

    // 불용어 제거 후 한글 키워드 추출
    const stopWords = new Set(["품셈", "인력", "인공", "수량", "단위", "장비", "자재", "알려줘", "얼마", "관련", "제작", "설치", "시공", "공사"]);
    const koreanWords = question.match(/[가-힣]{2,}/g) || [];
    const workKeywords = koreanWords.filter(w => !stopWords.has(w));

    // ⭐ 한글 외래어 → 영문 번역 (폴백 보장)
    const translatedKeywords: string[] = [];
    for (const kw of workKeywords) {
        if (KO_EN_DICT[kw]) {
            translatedKeywords.push(...KO_EN_DICT[kw]);
        }
    }
    const allKeywords = [...workKeywords, ...translatedKeywords];

    // 영문 키워드도 질문에서 직접 추출 (Crusher, Flange 등)
    const englishWords = question.match(/[A-Za-z][-A-Za-z]{1,}/g) || [];
    const engStopWords = new Set(["SCH", "mm", "ton", "help"]);
    const engKeywords = englishWords.filter(w => !engStopWords.has(w) && w.length >= 2);
    allKeywords.push(...engKeywords);

    // ⭐ 영문 약어 확장 (HDPE→PE, PVC→PE 등)
    const ENG_EXPAND: Record<string, string[]> = {
        "HDPE": ["PE", "폴리에틸렌"], "hdpe": ["PE", "폴리에틸렌"],
        "PVC": ["PVC관"], "pvc": ["PVC관"],
    };
    for (const ek of engKeywords) {
        const upper = ek.toUpperCase();
        if (ENG_EXPAND[upper]) allKeywords.push(...ENG_EXPAND[upper]);
    }

    // 규격 추출 (2t, 200mm, SCH 40, D110 등)
    let spec: string | null = null;
    const specMatch = question.match(/(\d+)\s*(t|ton|mm|A|㎜)/i);
    if (specMatch) spec = `${specMatch[1]}${specMatch[2]}`;
    const schMatch = question.match(/SCH\s*(\d+)/i);
    if (schMatch) spec = (spec ? spec + " " : "") + `SCH ${schMatch[1]}`;

    // 공종명 = 첫 번째 의미 있는 키워드 (영문 번역 우선)
    const work_name = translatedKeywords.length > 0
        ? translatedKeywords[0]  // 영문 원어 우선 (DB 엔티티명과 매칭)
        : (workKeywords.length > 0 ? workKeywords[0] : null);

    // 수량 감지 (10개소, 2t, 50m 등)
    const qtyMatch = question.match(/(\d+)\s*(개소|개|m|㎡|㎥|t|ton|본)/i);
    if (qtyMatch && work_name) {
        return {
            intent: "search",
            work_name,
            spec,
            keywords: allKeywords,
            ambiguity_reason: null,
        };
    }

    console.log(`[ruleBasedIntent] work_name=${work_name}, spec=${spec}, keywords=${allKeywords.join(",")}, translated=${translatedKeywords.join(",")}`);
    return {
        intent: work_name ? "search" : "greeting",
        work_name,
        spec,
        keywords: allKeywords,
        ambiguity_reason: null,
    };
}

export async function analyzeIntent(
    question: string,
    history: ChatMessage[],
    sessionContext?: SessionContext
): Promise<IntentAnalysis> {
    // DeepSeek API 키가 없으면 규칙 기반 폴백
    if (!DEEPSEEK_API_KEY) {
        console.warn("[analyzeIntent] DEEPSEEK_API_KEY 미설정 → 규칙 기반 폴백");
        return ruleBasedIntent(question);
    }

    try {
        // 세션 컨텍스트가 있으면 시스템 프롬프트에 부착
        let systemContent = INTENT_SYSTEM_PROMPT;
        if (sessionContext?.last_entity_id) {
            systemContent += `\n\n## 현재 세션 상태\n` +
                `last_entity_id: ${sessionContext.last_entity_id}\n` +
                `last_work_name: ${sessionContext.last_work_name || '없음'}\n` +
                `last_spec: ${sessionContext.last_spec || '없음'}\n` +
                `last_quantity: ${sessionContext.last_quantity || '없음'}\n` +
                `last_section_id: ${sessionContext.last_section_id || '없음'}`;
        }

        const response = await fetch(DEEPSEEK_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${DEEPSEEK_API_KEY}`,
            },
            body: JSON.stringify({
                model: "deepseek-chat",
                messages: [
                    { role: "system", content: systemContent },
                    // 최근 3턴만 전달 (토큰 절약)
                    ...history.slice(-3).map(h => ({
                        role: h.role === "user" ? "user" as const : "assistant" as const,
                        content: h.content,
                    })),
                    { role: "user" as const, content: question },
                ],
                response_format: { type: "json_object" },
                temperature: 0.1,
                max_tokens: 300,
            }),
        });

        if (!response.ok) {
            console.error(`[analyzeIntent] DeepSeek API failed: ${response.status} → 규칙 기반 폴백`);
            return ruleBasedIntent(question);
        }

        const data = await response.json();
        const content = data.choices?.[0]?.message?.content ?? "{}";
        const parsed = JSON.parse(content) as IntentAnalysis;

        // 안전성 보장: intent가 유효하지 않으면 폴백
        const validIntents = ["search", "clarify_needed", "followup", "greeting", "quantity_input", "cost_calculate", "modify_request", "report_request"];
        if (!validIntents.includes(parsed.intent)) {
            parsed.intent = parsed.ambiguity_reason ? "clarify_needed" : "search";
        }
        if (parsed.intent === "search" && parsed.ambiguity_reason) {
            parsed.intent = "clarify_needed";
        }
        parsed.keywords = parsed.keywords || [];

        console.log(`[analyzeIntent] intent=${parsed.intent}, work_name=${parsed.work_name}, spec=${parsed.spec}, keywords=${parsed.keywords.join(",")}${parsed.modify_type ? `, modify_type=${parsed.modify_type}` : ''}${parsed.quantity != null ? `, quantity=${parsed.quantity}` : ''}`);
        return parsed;
    } catch (err) {
        console.error("[analyzeIntent] error:", err, "→ 규칙 기반 폴백");
        return ruleBasedIntent(question);
    }
}

// ─── E-2. 그래프 기반 명확화 (graphClarify) ───
// Why: 모호한 질문에 대해 그래프의 Section→WorkType 계층을 탐색하여
//      실제 존재하는 후보만 제시.
// Phase 3: 2단계 drill-down
//   sectionId 없음 → Step 1: 섹션(분야) 선택
//   sectionId 있음 → Step 2: 해당 섹션 내 하목 선택
// ClarifyResult is imported from types.ts

// ═══ graphClarify: resolve.ts의 resolveSection + presentClarify를 호출하는 thin wrapper ═══
// Why: 기존 graphClarify의 656줄 모놀리식 로직을 resolve.ts로 분리.
//      이 함수는 기존 caller(index.ts, handleChat)와의 호환성을 유지하는 역할.
import { resolveSection, presentClarify } from "./resolve.ts";
import type { ResolveContext } from "./resolve.ts";

export async function graphClarify(analysis: IntentAnalysis, sectionId?: string): Promise<ClarifyResult> {
    const { work_name, keywords } = analysis;
    const searchTerms = work_name ? [work_name, ...keywords] : keywords;

    // searchTerms 비어있으면 안내 반환
    if (searchTerms.length === 0 && !sectionId) {
        return {
            message: "검색하고 싶은 품셈 항목을 좀 더 구체적으로 알려주세요.\n예: \"강관용접 200mm SCH 40\", \"콘크리트 타설\", \"거푸집 설치\"",
            options: [
                { label: "강관용접", query: "강관용접 품셈" },
                { label: "콘크리트 타설", query: "콘크리트 타설 품셈" },
                { label: "거푸집 설치", query: "거푸집 설치 품셈" },
            ],
        };
    }

    // sub_section 상태 파싱: sectionId에 ":sub=" 인코딩이 있으면 분리
    let actualSectionId = sectionId;
    let subSectionName: string | undefined;
    if (sectionId && sectionId.includes(':sub=')) {
        const parts = sectionId.split(':sub=');
        actualSectionId = parts[0];
        subSectionName = decodeURIComponent(parts[1]);
    }

    const ctx: ResolveContext = {
        analysis,
        sectionId: actualSectionId,
        subSectionName,
    };

    const resolved = await resolveSection(ctx);
    return presentClarify(resolved, searchTerms, work_name);
}

// ━━━ [E-3] 규격 정규화 ━━━
// Why: 사용자가 입력하는 규격 표기가 다양함 (인치, 파이, SCH 붙여쓰기 등)
//      DB의 표준 표기(mm, SCH 띄어쓰기)로 통일하여 검색 정확도 향상
export function normalizeSpec(spec: string | null): string | null {
    if (!spec) return spec;
    let s = spec;

    // 인치 → mm 변환 (1인치 = 25.4mm, 반올림)
    const inchMap: Record<string, string> = {
        '1/2': '15', '3/4': '20', '1': '25', '1-1/4': '32', '1-1/2': '40',
        '2': '50', '2-1/2': '65', '3': '80', '4': '100', '5': '125',
        '6': '150', '8': '200', '10': '250', '12': '300', '14': '350',
        '16': '400', '18': '450', '20': '500', '24': '600',
    };

    // "8인치" → "200mm"
    const inchMatch = s.match(/^(\d+(?:-\d+\/\d+|\d*\/\d+)?)\s*(?:인치|inch|"|″)/i);
    if (inchMatch) {
        const mmVal = inchMap[inchMatch[1]];
        if (mmVal) {
            s = s.replace(inchMatch[0], `${mmVal}mm`);
        }
    }

    // "파이200" → "200mm" (파이 = 직경 표기)
    s = s.replace(/파이\s*(\d+)/g, '$1mm');
    // "Φ200" or "ø200" → "200mm"
    s = s.replace(/[Φφø]\s*(\d+)/g, '$1mm');

    // "SCH40" → "SCH 40" (띄어쓰기 정규화)
    s = s.replace(/SCH\s*(\d+)/gi, 'SCH $1');

    // "200A" → "200mm" (A = mm in KS 표기)
    s = s.replace(/(\d+)\s*A\b/g, '$1mm');

    return s;
}