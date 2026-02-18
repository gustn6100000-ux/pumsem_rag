// clarify.ts - Intent analysis + graph-based clarification
import { supabase, DEEPSEEK_API_KEY, DEEPSEEK_URL } from "./config.ts";
import { chunkTextFallbackSearch } from "./search.ts";
import type { IntentAnalysis, ChatMessage, SessionContext, ClarifyResult, ClarifyOption, SelectorPanel, SelectorItem, FilterAxis } from "./types.ts";
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
- 동의어 확장: "PE관" → ["PE관", "HDPE관"]

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

// ─── Selector Panel 헬퍼: WorkType/Section 이름에서 규격 파싱 ───
function parseWorkTypeName(name: string): Record<string, string> {
    // 패턴1: 강관용접(200, SCH 40) → {diameter, sch}
    const m = name.match(/\((\d+),\s*SCH\s*([\d~]+)\)$/);
    if (m) return { diameter: m[1], sch: m[2] };

    // 패턴2: 이름(A, B) 일반 2값 → {spec1, spec2}
    const m2 = name.match(/\(([^,]+),\s*(.+)\)$/);
    if (m2) return { spec1: m2[1].trim(), spec2: m2[2].trim() };

    // 패턴3: 이름(단일값) → {spec1}
    const m3 = name.match(/\(([^)]+)\)$/);
    if (m3) return { spec1: m3[1].trim() };

    // 패턴4: 이름_서브타입 → {subtype}
    const parts = name.split('_');
    if (parts.length >= 2) return { subtype: parts.slice(1).join('_') };

    return {};
}

function extractFilterAxes(items: SelectorItem[]): FilterAxis[] {
    // 모든 spec key를 동적으로 집계
    const axisMap = new Map<string, Set<string>>();
    for (const item of items) {
        for (const [key, val] of Object.entries(item.specs)) {
            if (!axisMap.has(key)) axisMap.set(key, new Set());
            axisMap.get(key)!.add(val);
        }
    }

    // ─── 숫자 추출 헬퍼: "125mm" → 125, "50" → 50, "SCH 40" → 40 ───
    function extractNumber(s: string): number {
        const m = s.match(/[\d.]+/);
        return m ? parseFloat(m[0]) : NaN;
    }

    // ─── 단위 정규화: 모든 값에서 공통 단위를 추출하여 label에 통합 ───
    function normalizeValues(values: Set<string>): { normalized: string[]; unit: string } {
        const arr = [...values];
        // 단위 패턴 감지: "125mm", "300mm" → unit="mm"
        const unitMatch = arr[0]?.match(/[a-zA-Z/²]+$/);
        const detectedUnit = unitMatch ? unitMatch[0] : '';

        // 모든 값이 동일 단위를 갖는지 확인
        const allSameUnit = detectedUnit && arr.every(v => {
            const m = v.match(/[a-zA-Z/²]+$/);
            return m && m[0] === detectedUnit;
        });

        // 일부만 단위 있는 경우 → 단위 없는 값에 단위 추가 (일관성)
        // 예: "100" + "125mm" → 모두 "100mm", "125mm"
        const hasUnit = arr.some(v => /[a-zA-Z/²]+$/.test(v));
        const noUnit = arr.some(v => /^\d+\.?\d*$/.test(v));

        if (hasUnit && noUnit && detectedUnit) {
            // 혼재 케이스: 단위 없는 값에 단위 추가 후 정규화
            const fixed = arr.map(v => /^\d+\.?\d*$/.test(v) ? `${v}${detectedUnit}` : v);
            // 숫자 추출 후 정렬
            const sorted = fixed.sort((a, b) => {
                const na = extractNumber(a), nb = extractNumber(b);
                return (!isNaN(na) && !isNaN(nb)) ? na - nb : a.localeCompare(b, 'ko');
            });
            return { normalized: sorted, unit: detectedUnit };
        }

        if (allSameUnit) {
            // 모두 같은 단위 → 숫자순 정렬
            const sorted = arr.sort((a, b) => {
                const na = extractNumber(a), nb = extractNumber(b);
                return (!isNaN(na) && !isNaN(nb)) ? na - nb : a.localeCompare(b, 'ko');
            });
            return { normalized: sorted, unit: detectedUnit };
        }

        // 단위 없거나 혼합 불가 → 기존 정렬
        const sorted = arr.sort((a, b) => {
            const na = extractNumber(a), nb = extractNumber(b);
            return (!isNaN(na) && !isNaN(nb)) ? na - nb : a.localeCompare(b, 'ko');
        });
        return { normalized: sorted, unit: '' };
    }

    // 값 패턴 기반 필터 라벨 자동 추론
    function inferAxisLabel(key: string, values: Set<string>): string {
        // 고정 키 우선
        const fixed: Record<string, string> = { diameter: '호칭경(mm)', sch: 'SCH', subtype: '유형' };
        if (fixed[key]) return fixed[key];
        // spec1/spec2는 값 샘플로 추론
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

// ─── Selector Panel 생성: options > 6이면 체크박스 Panel 생성 ───
function buildSelectorPanel(
    options: ClarifyOption[],
    workName: string
): SelectorPanel | undefined {
    if (options.length <= 6) return undefined;

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

    if (selectorItems.length < 6) return undefined;

    // 숫자 기준 자연 정렬 (15, 20, 90, 100, 125, 150, 200)
    // Why: 문자열 정렬 시 "15" < "150" < "20" → 숫자 추출 후 비교
    selectorItems.sort((a, b) => {
        const numA = parseInt((a.label.match(/\d+/) || ['0'])[0], 10);
        const numB = parseInt((b.label.match(/\d+/) || ['0'])[0], 10);
        if (numA !== numB) return numA - numB;
        return a.label.localeCompare(b.label, 'ko');
    });

    const filters = extractFilterAxes(selectorItems);
    // 필터 없어도 체크박스 Panel은 생성 (잡철물 등 규격패턴 없는 항목용)

    return {
        title: `${workName} — 규격 선택`,
        filters,
        items: selectorItems,
        original_query: workName,
    };
}

// ━━━ sub_section drill-down 공통 함수 ━━━
// Why: Step 2와 케이스 A에서 동일 패턴 반복 → 함수 추출로 중복 제거
// sub_section이 2개 이상이면 ClarifyResult 반환, 아니면 null
function subSectionDrillDown(
    workTypes: any[],
    sectionPath: string,
    sectionId: string,
    sectionName: string,
    queryPrefix?: string   // query 조합 시 사용할 prefix (기본: sectionName)
): ClarifyResult | null {
    // sub_section별 분포 확인
    const subMap = new Map<string, any[]>();
    for (const wt of workTypes) {
        const sub = wt.properties?.sub_section || null;
        if (sub) {
            if (!subMap.has(sub)) subMap.set(sub, []);
            subMap.get(sub)!.push(wt);
        }
    }

    // distinct sub_section이 2개 미만 → drill-down 불필요
    if (subMap.size < 2) return null;

    console.log(`[graphClarify] sub_section drill-down: ${subMap.size}개 sub_section 발견`);

    const options: ClarifyOption[] = [];
    const prefix = queryPrefix || sectionName;

    // "전체 내용 보기" 옵션
    options.push({
        label: `📋 ${sectionName} 전체 내용 보기`,
        query: `${prefix} 전체 품셈`,
        section_id: sectionId,
        option_type: "full_view",
    });

    // sub_section별 옵션 (sub_section_no 순 정렬)
    const sorted = [...subMap.entries()].sort((a, b) => {
        const noA = a[1][0]?.properties?.sub_section_no || 99;
        const noB = b[1][0]?.properties?.sub_section_no || 99;
        return Number(noA) - Number(noB);
    });

    for (const [subName, subWTs] of sorted) {
        options.push({
            label: `📂 ${subName} (${subWTs.length}건)`,
            query: `${prefix} ${subName} 품셈`,
            section_id: `${sectionId}:sub=${encodeURIComponent(subName)}`,
            option_type: "section" as any,
        });
    }

    return {
        message: `**${sectionPath}** 품셈에는 ${subMap.size}개 분류(총 ${workTypes.length}개 작업)가 있습니다.\n분류를 선택해 주세요.`,
        options,
    };
}

export async function graphClarify(analysis: IntentAnalysis, sectionId?: string): Promise<ClarifyResult> {
    const { work_name, keywords } = analysis;
    let searchTerms = work_name ? [work_name, ...keywords] : keywords;

    // ─── searchTerms[0] 정규화 ───
    // Why: DeepSeek가 "강판용접 강판용접 Plate Welding" 같은 비정상 work_name을 반환하는 경우
    //      한글 토큰만 추출하여 중복 제거 후 정규화
    if (searchTerms.length > 0 && searchTerms[0].length > 0) {
        const raw = searchTerms[0];
        // 한글+영문 토큰 추출 후 중복 제거
        const koreanTokens = [...new Set(raw.match(/[가-힣]{2,}/g) || [])];
        if (koreanTokens.length > 0) {
            // 중복 단어 제거 후 재결합 (예: "강판용접 강판용접" → "강판용접")
            searchTerms[0] = koreanTokens.join('');
        }
        // 여전히 비정상적으로 길거나 한글이 없으면 → original_query에서 추출
        if (searchTerms[0].length > 15 || !/[가-힣]/.test(searchTerms[0])) {
            const originalQuery = analysis.ambiguity_reason || work_name || '';
            const fallbackTokens = [...new Set(originalQuery.match(/[가-힣]{2,}/g) || [])];
            if (fallbackTokens.length > 0) searchTerms[0] = fallbackTokens.join('');
        }
        console.log(`[graphClarify] searchTerms 정규화: "${raw}" → "${searchTerms[0]}"`);
    }

    // # 접미사 제거 (DB 내부: 3-2-2#5, 사용자 표시: 3-2-2)
    const displayCode = (code: string | null | undefined): string =>
        code ? code.replace(/#.*$/, '') : '';

    if (searchTerms.length === 0) {
        return {
            message: "검색하고 싶은 품셈 항목을 좀 더 구체적으로 알려주세요.\n예: \"강관용접 200mm SCH 40\", \"콘크리트 타설\", \"거푸집 설치\"",
            options: [
                { label: "강관용접", query: "강관용접 품셈" },
                { label: "콘크리트 타설", query: "콘크리트 타설 품셈" },
                { label: "거푸집 설치", query: "거푸집 설치 품셈" },
            ],
        };
    }

    // ═══ Step 2: sectionId가 있으면 → 해당 섹션 내 하목 선택 ═══
    // Why: 사용자가 Step 1에서 섹션(분야)을 선택한 후, 해당 섹션의 하위 WorkType을 보여줌
    if (sectionId) {
        // ─── sub_section 필터 추출 ───
        // Why: sectionId에 ":sub=N" 접미사가 있으면 → 특정 sub_section만 표시
        //      예: "13-2-3:sub=2. TIG용접" → sectionId=13-2-3, subFilter="2. TIG용접"
        let actualSectionId = sectionId;
        let subFilter: string | null = null;
        if (sectionId.includes(':sub=')) {
            const parts = sectionId.split(':sub=');
            actualSectionId = parts[0];
            subFilter = decodeURIComponent(parts[1]);
        }

        console.log(`[graphClarify] Step 2: sectionId=${actualSectionId}, subFilter=${subFilter}`);

        // graph_chunks에서 해당 섹션 메타데이터 조회
        const { data: chunkData } = await supabase
            .from("graph_chunks")
            .select("section_id, department, chapter, title, text")
            .eq("section_id", actualSectionId)
            .limit(1);

        const chunk = (chunkData as any[])?.[0];
        const sectionPath = chunk
            ? `${chunk.department} > ${chunk.chapter} > ${chunk.title}`
            : actualSectionId;

        // 해당 섹션의 하위 WorkType 조회 — 정확 매칭 (I-1 수정)
        const { data: exactWTs } = await supabase
            .from("graph_entities")
            .select("id, name, type, source_section, properties")
            .eq("type", "WorkType")
            .eq("source_section", actualSectionId)
            .limit(200);

        let workTypes = (exactWTs || []) as any[];
        console.log(`[graphClarify] Step 2: exact=${workTypes.length}개 WorkType (sectionId=${actualSectionId})`);

        // ─── Phase 5: sub_section 기반 drill-down ───
        // Why: 7개 심각 섹션(강관용접, 강판용접, Flange 등)에 sub_section 속성이 추가되어
        //      Section → SubSection → WorkType 3단계 탐색 가능
        if (workTypes.length > 0 && !subFilter) {
            const drillResult = subSectionDrillDown(
                workTypes, sectionPath, actualSectionId,
                chunk?.title || actualSectionId
            );
            if (drillResult) return drillResult;
        }

        // ─── sub_section 필터 적용 ───
        if (subFilter && workTypes.length > 0) {
            const beforeCount = workTypes.length;
            workTypes = workTypes.filter((wt: any) => wt.properties?.sub_section === subFilter);
            console.log(`[graphClarify] Step 2-sub: subFilter="${subFilter}" → ${beforeCount} → ${workTypes.length}개`);
        }

        // Fix B0: WT 0건 → 하위 절(children) 탐색
        // Why: "2-12 공통장비" 같은 상위 절은 WT 0건이지만,
        //      하위 절 "2-12-1", "2-12-2"에 실제 데이터 존재 (145개 중 ~120개 해당)
        let childSections: any[] = [];
        if (workTypes.length === 0 && !subFilter) {
            // sectionId에서 base 추출: "2-12#3" → "2-12", "1-1" → "1-1"
            const baseSectionId = actualSectionId.includes('#') ? actualSectionId.split('#')[0] : actualSectionId;
            const childPrefix = baseSectionId + '-';
            const dept = chunk?.department || '';

            console.log(`[graphClarify] Step 2: WT 0건 → 하위 절 탐색 (prefix=${childPrefix}, dept=${dept})`);

            // 하위 절의 chunk 정보 조회
            const { data: childChunks } = await supabase
                .from("graph_chunks")
                .select("section_id, title, department")
                .ilike("section_id", `${childPrefix}%`)
                .eq("department", dept);

            // 중복 제거 (같은 section_id의 여러 chunk)
            const uniqueChildren = new Map<string, any>();
            (childChunks || []).forEach((c: any) => {
                if (!uniqueChildren.has(c.section_id)) {
                    uniqueChildren.set(c.section_id, c);
                }
            });
            childSections = Array.from(uniqueChildren.values());
            console.log(`[graphClarify] Step 2: 하위 절 ${childSections.length}건 발견`);

            // 하위 절에서 WorkType도 가져오기 (개별 옵션용)
            if (childSections.length > 0) {
                const childSectionIds = childSections.map(c => c.section_id);
                const { data: childWTs } = await supabase
                    .from("graph_entities")
                    .select("id, name, type, source_section, properties")
                    .eq("type", "WorkType")
                    .in("source_section", childSectionIds)
                    .limit(50);
                workTypes = (childWTs || []) as any[];
                console.log(`[graphClarify] Step 2: 하위 절 WorkType ${workTypes.length}건`);
            }
        }

        // Phase 4A: 이름 정규화 기준 중복 제거
        // Why: "인 력(인)" vs "인력(인)" 같은 미세 차이로 V형/U형 데이터가 중복 표시되는 문제 방지
        if (workTypes.length > 0) {
            const uniqueWTs = new Map<string, any>();
            for (const wt of workTypes) {
                const normKey = wt.name.replace(/\s+/g, '').toLowerCase();
                if (!uniqueWTs.has(normKey)) {
                    uniqueWTs.set(normKey, wt);
                }
            }
            const beforeCount = workTypes.length;
            workTypes = Array.from(uniqueWTs.values());
            if (beforeCount !== workTypes.length) {
                console.log(`[graphClarify] Step 2: dedup ${beforeCount} → ${workTypes.length}개`);
            }
        }

        const options: ClarifyOption[] = [];

        // "전체 내용 보기" 옵션 (원문 청크 전체 반환용)
        options.push({
            label: `📋 ${chunk?.title || actualSectionId}${subFilter ? ` > ${subFilter}` : ''} 전체 내용 보기`,
            query: `${chunk?.title || actualSectionId} 전체 품셈`,
            section_id: actualSectionId,
            option_type: "full_view",
        });

        if (childSections.length > 0 && workTypes.length > 10) {
            // 하위 절이 많으면 → 절 단위로 옵션 제시 (더 깔끔한 UX)
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
                options.push({
                    label: wt.name,
                    query: `${wt.name} 품셈`,
                    entity_id: wt.id,
                    source_section: wt.source_section,
                    option_type: "worktype",
                });
            }
        }

        // 메시지 분기: WT 존재 여부 + 하위 절 여부
        let clarifyMessage: string;
        if (subFilter) {
            // sub_section 필터 적용 상태
            clarifyMessage = `**${sectionPath} > ${subFilter}** 품셈은 ${workTypes.length}개 작업으로 분류되어 있습니다.\n어떤 작업의 품셈을 찾으시나요?`;
        } else if (workTypes.length > 0 && childSections.length > 0) {
            if (workTypes.length <= 10) {
                clarifyMessage = `**${sectionPath}** 품셈은 ${workTypes.length}개 작업으로 분류되어 있습니다.\n어떤 작업의 품셈을 찾으시나요?`;
            } else {
                clarifyMessage = `**${sectionPath}** 품셈에는 ${childSections.length}개 분류(총 ${workTypes.length}개 작업)가 있습니다.\n분류를 선택해 주세요.`;
            }
        } else if (workTypes.length > 0) {
            clarifyMessage = `**${sectionPath}** 품셈은 ${workTypes.length}개 작업으로 분류되어 있습니다.\n어떤 작업의 품셈을 찾으시나요?`;
        } else {
            // WT=0, 하위절=0 → Note 수 조회하여 안내 메시지 구성
            const { count: noteCount } = await supabase
                .from("graph_entities")
                .select("id", { count: "exact", head: true })
                .eq("type", "Note")
                .eq("source_section", actualSectionId);
            const nc = noteCount || 0;
            clarifyMessage = nc > 0
                ? `**${sectionPath}** 품셈은 개별 작업이 분류되어 있지 않고, **기준 및 주의사항 ${nc}건**을 포함하고 있습니다.\n아래 "전체 내용 보기"를 통해 확인해 주세요.`
                : `**${sectionPath}** 품셈의 상세 작업이 개별 등록되어 있지 않습니다.\n아래 "전체 내용 보기" 버튼으로 해당 절의 품셈 데이터를 확인해 주세요.`;
        }

        // 7개 이상 옵션 → Selector Panel 생성
        const selector = buildSelectorPanel(options, sectionPath);

        return {
            message: clarifyMessage,
            options,
            ...(selector ? { selector } : {}),
        };
    }

    // ═══ Step 1: sectionId 없음 → 섹션 탐색 ═══

    // ─── 범용 동사 목록 (전략 3 독립검색에서 제외) ───
    const ACTION_VERBS = new Set([
        "제작", "설치", "시공", "공사", "운반", "보수", "해체", "조립",
        "철거", "가공", "타설", "양생", "포설", "다짐", "절단", "용접",
        "도장", "배관", "배선", "측량", "검사", "인양", "적재",
    ]);

    // 전략 1-A: Section 레벨 탐색 — work_name으로 Section 정확 매칭
    const sectionPattern = "%" + searchTerms[0] + "%";
    const { data: sections } = await supabase
        .from("graph_entities")
        .select("id, name, type, source_section, properties")
        .eq("type", "Section")
        .ilike("name", sectionPattern)
        .limit(5);

    // 전략 1-B: 정확 매칭 실패 시 토큰 분리 ILIKE 폴백
    // Why: "강판용접" → "강판 전기아크용접" (중간 수식어 포함 케이스)
    //      searchTerms[0]을 2음절 단위로 분리하여 AND 조건 매칭
    let tokenFallbackSections: any[] = [];
    if ((!sections || sections.length === 0) && searchTerms[0].length >= 4) {
        // Step 1: 공백/영문 기준 분리 시도
        let tokens = searchTerms[0].match(/[가-힣]{2,}|[a-zA-Z]+/g) || [];

        // Step 2: 단일 토큰(공백 없는 4글자+ 한글)이면 2음절씩 분리
        // "강판용접" → ["강판", "용접"], "밸브취부" → ["밸브", "취부"]
        if (tokens.length === 1 && tokens[0].length >= 4) {
            const word = tokens[0];
            const halfLen = Math.ceil(word.length / 2);
            tokens = [word.substring(0, halfLen), word.substring(halfLen)];
        }

        if (tokens.length >= 2) {
            // 동적 AND 조건: name ILIKE '%강판%' AND name ILIKE '%용접%'
            let query = supabase.from("graph_entities")
                .select("id, name, type, source_section, properties")
                .eq("type", "Section");
            for (const token of tokens) {
                query = query.ilike("name", `%${token}%`);
            }
            const { data: tokenSections } = await query.limit(10);
            if (tokenSections) tokenFallbackSections = tokenSections;
            console.log(`[graphClarify] 전략 1-B 토큰분리: "${tokens.join('","')}" → ${tokenFallbackSections.length}건`);
        }
    }

    // 전략 1 결과 통합: 정확 매칭 우선, 없으면 토큰 분리 결과 사용
    const effectiveSections = (sections && sections.length > 0) ? sections : tokenFallbackSections;

    // 매칭된 Section의 source_section으로 하위 WorkType 조회
    let sectionChildWorkTypes: any[] = [];
    const sectionSourceSections = new Set<string>();
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
            console.log(`[graphClarify] Section ${sourceSections.join(",")} 하위 WorkType ${childWTs?.length || 0}개 발견`);
        }
    }

    // 전략 2: WorkType 직접 탐색 — 키워드로 WorkType 매칭 (korean_alias 포함)
    const workPattern = "%" + searchTerms.join("%") + "%";
    const { data: workTypes } = await supabase
        .from("graph_entities")
        .select("id, name, type, source_section, properties")
        .eq("type", "WorkType")
        .or(`name.ilike.${workPattern},properties->>korean_alias.ilike.${workPattern}`)
        .limit(200);

    // 전략 3: 키워드별 독립 검색 (범용 동사 제외, 고유명사만)
    let extraWorkTypes: any[] = [];
    for (const kw of keywords) {
        // 범용 동사는 독립 검색 제외 (오염 방지)
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

    // 전략 4: chunk 본문 텍스트 검색 (Layer 4)
    // Why: "장비편성", "인력편성" 등 엔티티 이름에 없지만 chunk 본문에만 존재하는 용어
    //       전략 1~3 결과에 복합어가 이름에 없을 때만 실행 (안전 조건)
    let chunkTextResults: any[] = [];
    const prelimResults = [...effectiveSections, ...sectionChildWorkTypes, ...(workTypes || []), ...extraWorkTypes];
    // 복합어 생성: keywords 사용, 없으면 work_name 토큰화
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
        console.log(`[graphClarify] 전략 4: chunk text fallback 시도 (복합어 "${compoundTerms.join(',')}" 미매칭)`);
        const chunkQuestion = searchTerms.join(' ');
        const chunkFallback = await chunkTextFallbackSearch(chunkQuestion);
        console.log(`[graphClarify] 전략 4: ${chunkFallback.length}건`);
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
        return {
            message: `"${searchTerms.join(" ")}"와 관련된 품셈 항목을 찾지 못했습니다.\n정확한 공종명을 입력해 주세요.`,
            options: [],
        };
    }

    // ─── graph_chunks에서 부문/장/절 메타데이터 조회 ───
    // Why: source_section별로 "건축부문 > 제8장 > 잡철물" vs "기계설비부문 > 제9장 > 잡철물" 구분
    const allSourceSections = [...new Set(uniqueResults.map(r => r.source_section).filter(Boolean))];
    const chunkMeta = new Map<string, { department: string; chapter: string; title: string }>();
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
        console.log(`[graphClarify] graph_chunks 메타: ${chunkMeta.size}개 섹션 조회`);
    }

    // label 생성 헬퍼: [부문 > 장] 규격명 형태
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

    // ─── 관련성 점수 산출 (Relevance Scoring) ───
    // 점수 기준:
    //   +50: Section 직계 하위 (source_section 일치)
    //   +30: 이름에 work_name 포함
    //   +10: 이름에 각 키워드 포함 (키워드당)
    //   -5:  Section 타입 (이미 하위 WorkType으로 펼쳐짐)
    const scoredResults = uniqueResults.map(r => {
        let score = 0;
        const name = r.name || "";
        const nameLC = name.toLowerCase();

        // (1) Section 직계 하위인지
        if (r.type === "WorkType" && sectionSourceSections.has(r.source_section)) {
            score += 50;
        }

        // (2) work_name 포함 여부
        if (work_name && nameLC.includes(work_name.toLowerCase())) {
            score += 30;
        }

        // (3) 각 키워드(work_name 제외)와의 매칭
        for (const kw of keywords) {
            if (nameLC.includes(kw.toLowerCase())) {
                score += 10;
            }
        }

        // (4) Section은 WorkType보다 낮은 우선순위
        if (r.type === "Section") {
            score -= 5;
        }

        return { ...r, _score: score };
    });

    // 점수 내림차순 정렬
    scoredResults.sort((a, b) => b._score - a._score);

    console.log(`[graphClarify] 관련성 점수 상위:`,
        scoredResults.slice(0, 5).map(r => `${r.name}(${r._score})`).join(", "));

    // ─── 케이스 분기 ───
    const matchedSections = scoredResults.filter(r => r.type === "Section");
    const matchedWorkTypes = scoredResults.filter(r => r.type === "WorkType");

    // Phase 3-C: chunk text fallback 결과가 WorkType을 찾았으면 → 최우선 표시
    // Why: 전략 4가 "장비편성" 같은 chunk 전용 키워드에서 정확한 WorkType을 찾았으므로
    //       복수 섹션 분기보다 우선. 기존 전략 1~3의 Section 노이즈를 무시.
    const chunkWorkTypes = chunkTextResults.filter((r: any) => r.type === 'WorkType');
    if (chunkWorkTypes.length > 0) {
        console.log(`[graphClarify] Phase 3-C: chunk text WorkType ${chunkWorkTypes.length}건 → 우선 표시`);

        // ─── Phase 3-C에서도 sub_section drill-down 시도 ───
        // Why: 전기아크용접 등 단일 섹션이지만 V형/U형/X형 등 sub_section이 있는 경우
        //      chunkWorkTypes만으로는 불충분할 수 있으므로 sectionChildWorkTypes도 합류
        const allWTsForDrill = sectionChildWorkTypes.length > 0
            ? sectionChildWorkTypes  // 전략 1에서 이미 해당 섹션 전체 WT를 조회함
            : chunkWorkTypes;
        const drillSectionId = matchedSections[0]?.source_section
            || chunkWorkTypes[0]?.source_section || '';
        const drillMeta = drillSectionId ? chunkMeta.get(drillSectionId) : null;
        const drillSectionName = matchedSections[0]?.name || work_name || searchTerms[0];
        const drillSectionPath = drillMeta
            ? `${drillMeta.department} > ${drillMeta.chapter} > ${drillMeta.title}`
            : drillSectionName;

        const drillResult = subSectionDrillDown(
            allWTsForDrill, drillSectionPath, drillSectionId, drillSectionName
        );
        if (drillResult) return drillResult;
        // ─── sub_section 없으면 기존 Phase 3-C 로직 계속 ───

        const options: ClarifyOption[] = [];

        // full_view 옵션 추가 (해당 섹션의 전체 내용 보기)
        const primarySection = matchedSections[0];
        if (primarySection?.source_section) {
            options.push({
                label: `📋 ${primarySection.name} 전체 내용 보기`,
                query: `${primarySection.name} 전체 품셈`,
                section_id: primarySection.source_section,
                option_type: 'full_view' as const,
            });
        } else if (chunkWorkTypes.length > 0) {
            // Section 매칭 없이 chunk에서만 WorkType을 찾은 경우
            // chunkWorkTypes의 source_section에서 섹션 정보 추출
            const firstSectionId = chunkWorkTypes[0].source_section;
            const meta = chunkMeta.get(firstSectionId);
            const sectionLabel = meta ? meta.title : firstSectionId;
            options.push({
                label: `📋 ${sectionLabel} 전체 내용 보기`,
                query: `${sectionLabel} 전체 품셈`,
                section_id: firstSectionId,
                option_type: 'full_view' as const,
            });
        }

        for (const wt of chunkWorkTypes.slice(0, 10)) {
            options.push({
                label: makeLabel(wt),
                query: `${wt.name} 품셈`,
                entity_id: wt.id,
                source_section: wt.source_section,
                option_type: 'worktype' as const,
            });
        }

        return {
            message: `\"${searchTerms.join(" ")}\" 관련 품셈 항목입니다.\n어떤 작업의 품셈을 찾으시나요?`,
            options,
        };
    }

    // Phase 3: 복수 섹션이면 항상 섹션 선택을 우선 (케이스 A보다 우선)
    // Why: "잡철물 제작" → 8-3-1(건축) vs 9-1-2(기계설비) 분야 선택이 먼저
    const uniqueSectionIds = [...new Set(matchedSections.map(s => s.source_section).filter(Boolean))];
    if (uniqueSectionIds.length > 1) {
        // 복수 분야 → Step 1: 섹션 선택
        console.log(`[graphClarify] Step 1: ${uniqueSectionIds.length}개 분야 → 섹션 선택`);
        const options: ClarifyOption[] = matchedSections.slice(0, 10).map(s => {
            const meta = chunkMeta.get(s.source_section);
            const secTag = s.source_section ? ` (${displayCode(s.source_section)})` : "";
            const label = meta
                ? `${meta.department} > ${meta.chapter} > ${meta.title}${secTag}`
                : `[${displayCode(s.source_section)}] ${s.name}`;
            return {
                label,
                query: `${s.name} 품셈`,
                source_section: s.source_section,
                section_id: s.source_section,     // Step 2 트리거용
                option_type: 'section' as const,
            };
        });

        const selector = buildSelectorPanel(options, searchTerms[0]);
        return {
            message: `"${searchTerms.join(" ")}" 관련 품셈이 **${uniqueSectionIds.length}개 분야**에 있습니다.\n어떤 분야의 품셈을 찾으시나요?`,
            options,
            selector,
        };
    }

    // 케이스 A: WorkType이 많이 매칭 (단일 섹션) → 하목 선택
    if (matchedWorkTypes.length > 3) {
        // ─── Phase 5: 케이스 A에서도 sub_section drill-down ───
        // Why: Step 1에서 section→WT를 직접 펼칠 때도 sub_section이 있으면
        //      sub_section 선택 단계를 먼저 삽입 (Step 2와 동일 로직)
        const sectionNameA = matchedSections[0]?.name || work_name || searchTerms[0];
        const sectionMetaA = matchedSections[0] ? chunkMeta.get(matchedSections[0].source_section) : null;
        const fullSectionPathA = sectionMetaA
            ? `${sectionMetaA.department} > ${sectionMetaA.chapter} > ${sectionMetaA.title}`
            : sectionNameA;
        const primarySectionIdA = matchedSections[0]?.source_section
            || matchedWorkTypes[0]?.source_section || '';

        const drillResult = subSectionDrillDown(
            matchedWorkTypes, fullSectionPathA, primarySectionIdA, sectionNameA
        );
        if (drillResult) return drillResult;
        // ─── sub_section 없으면 기존 로직 ───

        const selectedOptions: ClarifyOption[] = [];

        // "전체 보기" 옵션 (원문 청크 포함)
        // Fix: Section.source_section이 null일 때 WorkType.source_section으로 대체
        const primarySection = matchedSections[0];
        const fullViewSectionId = primarySection?.source_section
            || matchedWorkTypes[0]?.source_section || null;
        const fullViewLabel = primarySection?.name || work_name || searchTerms[0];
        if (fullViewSectionId) {
            selectedOptions.push({
                label: `📋 ${fullViewLabel} 전체 내용 보기`,
                query: `${fullViewLabel} 전체 품셈`,
                section_id: fullViewSectionId,
                option_type: 'full_view' as const,
            });
        }

        for (const wt of matchedWorkTypes) {
            // 12개 제한 제거 — Selector Panel에서 전체 표시
            if (selectedOptions.find(o => o.entity_id === wt.id)) continue;

            selectedOptions.push({
                label: makeLabel(wt),
                query: `${wt.name} 품셈`,
                entity_id: wt.id,
                source_section: wt.source_section,
                option_type: 'worktype' as const,
            });
        }

        // Section 이름 + 부문 정보로 풍부한 메시지 구성
        const sectionName = matchedSections[0]?.name || work_name || searchTerms[0];
        const sectionMeta = matchedSections[0] ? chunkMeta.get(matchedSections[0].source_section) : null;
        const fullSectionPath = sectionMeta
            ? `${sectionMeta.department} > ${sectionMeta.chapter} > ${sectionMeta.title}`
            : sectionName;

        const selector = buildSelectorPanel(selectedOptions, work_name || searchTerms[0]);
        return {
            message: `**${fullSectionPath}** 품셈은 ${matchedWorkTypes.length}개 작업으로 분류되어 있습니다.\n어떤 작업의 품셈을 찾으시나요?`,
            options: selectedOptions,
            selector,
        };
    }

    // 케이스 B: Section 1개 + WorkType 소수 → 하목 확인
    if (matchedSections.length === 1 && matchedWorkTypes.length > 0) {
        const section = matchedSections[0];
        const meta = chunkMeta.get(section.source_section);
        const sectionPath = meta
            ? `${meta.department} > ${meta.chapter} > ${meta.title}`
            : section.name;

        // Fix: Section.source_section null 대비 WorkType fallback
        const caseBSectionId = section.source_section
            || matchedWorkTypes[0]?.source_section || null;
        const options: ClarifyOption[] = caseBSectionId ? [{
            label: `📋 ${section.name} 전체 내용 보기`,
            query: `${section.name} 전체 품셈`,
            section_id: caseBSectionId,
            option_type: 'full_view' as const,
        }] : [];

        for (const wt of matchedWorkTypes.slice(0, 10)) {
            options.push({
                label: wt.name,
                query: `${wt.name} 품셈`,
                entity_id: wt.id,
                source_section: wt.source_section,
                option_type: 'worktype' as const,
            });
        }

        const selector = buildSelectorPanel(options, section.name);
        return {
            message: `**${sectionPath}** 하위 ${matchedWorkTypes.length}개 작업이 있습니다.\n어떤 작업의 품셈을 찾으시나요?`,
            options,
            selector,
        };
    }

    // 케이스 C: 소수 결과 → 확인 질문 (부문 정보 포함)
    const options: ClarifyOption[] = uniqueResults.slice(0, 10).map(r => ({
        label: makeLabel(r),
        query: `${r.name} 품셈`,
        entity_id: r.id,
        source_section: r.source_section,
        option_type: (r.type === 'Section' ? 'section' : 'worktype') as 'section' | 'worktype',
        ...(r.type === 'Section' ? { section_id: r.source_section } : {}),
    }));

    return {
        message: `다음 중 찾으시는 항목이 있나요?`,
        options,
    };
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