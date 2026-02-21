// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 건설 품셈 AI 어시스턴트 — app.js
// v1.1 계획서 기반 구현 (DOMPurify XSS 방어 포함)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

// ━━━ 설정 ━━━
const CONFIG = {
    EDGE_FUNCTION_URL: 'https://bfomacoarwtqzjfxszdr.supabase.co/functions/v1/rag-chat',
    // EDGE_FUNCTION_URL: 'http://127.0.0.1:8888',  // 로컬 DeepSeek RAG 서버
    API_KEY: '', // RAG_API_KEY가 설정된 경우 여기에 입력
    MAX_HISTORY: 5,
    MAX_QUESTION_LENGTH: 500,
};

// ━━━ 상태 ━━━
const state = {
    history: [],    // ChatMessage[]
    isLoading: false,
};

// ━━━ DOM 요소 ━━━
const chatMessages = document.getElementById('chatMessages');
const chatForm = document.getElementById('chatForm');
const questionInput = document.getElementById('questionInput');
const sendButton = document.getElementById('sendButton');
const charCount = document.getElementById('charCount');

// ━━━ DOMPurify 설정 (Codex F2 — XSS 방어) ━━━
const PURIFY_CONFIG = {
    ALLOWED_TAGS: [
        'p', 'br', 'strong', 'em', 'b', 'i', 'u',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'blockquote', 'pre', 'code',
        'hr', 'span', 'div',
    ],
    ALLOWED_ATTR: ['class'],
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'input'],
    FORBID_ATTR: ['style', 'onerror', 'onclick', 'onload'],
};

// ━━━ 마크다운 → 안전한 HTML 변환 ━━━
function renderMarkdown(text) {
    // marked.js로 마크다운 → HTML
    const rawHtml = marked.parse(text, { breaks: true });
    // DOMPurify로 sanitize (XSS 방어)
    return DOMPurify.sanitize(rawHtml, PURIFY_CONFIG);
}

// ━━━ 메시지 추가 ━━━
function addMessage(role, content, extra = null) {
    const div = document.createElement('div');
    div.className = `message ${role}`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    if (role === 'assistant') {
        // AI 답변: 마크다운 렌더링 + DOMPurify sanitize
        contentDiv.innerHTML = renderMarkdown(content);

        // 출처 태그 (sources)
        if (extra?.sources?.length > 0) {
            const tagsDiv = document.createElement('div');
            tagsDiv.className = 'source-tags';
            extra.sources.forEach(src => {
                const tag = document.createElement('span');
                tag.className = 'source-tag';
                tag.textContent = `📌 ${src.section_label || src.source_section || src.entity_name}`;
                tagsDiv.appendChild(tag);
            });
            contentDiv.appendChild(tagsDiv);
        }

        // 디버그 패널 (search_info)
        if (extra?.search_info) {
            const toggle = document.createElement('div');
            toggle.className = 'debug-toggle';
            toggle.textContent = '🔍 검색 정보 보기';
            toggle.addEventListener('click', () => {
                panel.classList.toggle('open');
                toggle.textContent = panel.classList.contains('open')
                    ? '🔍 검색 정보 닫기'
                    : '🔍 검색 정보 보기';
            });

            const panel = document.createElement('div');
            panel.className = 'debug-panel';
            const info = extra.search_info;
            const token = info.token_usage || {};
            const hasToken = token.total_tokens > 0;
            panel.innerHTML = `
        <div class="debug-row">
          <span class="debug-label">검색된 엔티티</span>
          <span class="debug-value">${info.entities_found}건</span>
        </div>
        <div class="debug-row">
          <span class="debug-label">확장된 관계</span>
          <span class="debug-value">${info.relations_expanded}건</span>
        </div>
        <div class="debug-row">
          <span class="debug-label">일위대가 매칭</span>
          <span class="debug-value">${info.ilwi_matched}건</span>
        </div>
        <div class="debug-row">
          <span class="debug-label">원문 청크</span>
          <span class="debug-value">${info.chunks_retrieved}건</span>
        </div>
        <div class="debug-row">
          <span class="debug-label">응답 시간</span>
          <span class="debug-value">${(info.latency_ms / 1000).toFixed(2)}초</span>
        </div>
        ${hasToken ? `
        <div class="debug-divider"></div>
        <div class="debug-row">
          <span class="debug-label">📊 LLM Input 토큰</span>
          <span class="debug-value">${token.llm_input_tokens?.toLocaleString() || '-'}</span>
        </div>
        <div class="debug-row">
          <span class="debug-label">📊 LLM Output 토큰</span>
          <span class="debug-value">${token.llm_output_tokens?.toLocaleString() || '-'}</span>
        </div>
        <div class="debug-row">
          <span class="debug-label">📊 총 토큰</span>
          <span class="debug-value" style="color: #fbbf24;">${token.total_tokens?.toLocaleString() || '-'}</span>
        </div>
        <div class="debug-row">
          <span class="debug-label">💰 추정 비용</span>
          <span class="debug-value" style="color: #34d399;">₩${token.estimated_cost_krw?.toFixed(2) || '-'}</span>
        </div>
        ` : ''}
      `;

            contentDiv.appendChild(toggle);
            contentDiv.appendChild(panel);
        }
    } else {
        // 사용자 메시지
        contentDiv.textContent = content;
    }

    div.appendChild(avatar);
    div.appendChild(contentDiv);
    chatMessages.appendChild(div);

    // 스크롤 하단
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ━━━ 로딩 표시 ━━━
function showLoading() {
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = 'loadingMessage';

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '🤖';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.innerHTML = `
    <div class="typing-indicator">
      <span></span><span></span><span></span>
    </div>
  `;

    div.appendChild(avatar);
    div.appendChild(contentDiv);
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function hideLoading() {
    const el = document.getElementById('loadingMessage');
    if (el) el.remove();
}

// ━━━ 에러 표시 ━━━
function showError(message) {
    const div = document.createElement('div');
    div.className = 'message assistant';

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = '⚠️';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content error-message';
    contentDiv.textContent = message;

    div.appendChild(avatar);
    div.appendChild(contentDiv);
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ━━━ API 호출 ━━━
async function sendQuestion(question) {
    const headers = {
        'Content-Type': 'application/json',
    };

    // API Key가 설정된 경우에만 헤더 추가
    if (CONFIG.API_KEY) {
        headers['x-api-key'] = CONFIG.API_KEY;
    }

    const response = await fetch(CONFIG.EDGE_FUNCTION_URL, {
        method: 'POST',
        headers,
        body: JSON.stringify({
            question,
            history: state.history.slice(-CONFIG.MAX_HISTORY),
        }),
    });

    if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        const errorMap = {
            question_required: '질문을 입력해주세요.',
            unauthorized: 'API 인증에 실패했습니다.',
            rate_limited: '요청이 너무 많습니다. 잠시 후 다시 시도해주세요.',
            payload_too_large: '요청이 너무 큽니다.',
            embedding_failed: '임베딩 생성 중 오류가 발생했습니다.',
            llm_failed: 'AI 답변 생성 중 오류가 발생했습니다.',
        };
        const msg = errorMap[errorData.error] || `서버 오류 (${response.status})`;
        throw new Error(msg);
    }

    return await response.json();
}

// ━━━ 전송 처리 ━━━
async function handleSubmit(e) {
    e.preventDefault();

    const question = questionInput.value.trim();
    if (!question || state.isLoading) return;

    // UI 업데이트
    state.isLoading = true;
    sendButton.disabled = true;
    questionInput.value = '';
    charCount.textContent = '0';

    // 사용자 메시지 표시
    addMessage('user', question);

    // 대화 이력에 추가
    state.history.push({ role: 'user', content: question });

    // 로딩 표시
    showLoading();

    try {
        const result = await sendQuestion(question);

        hideLoading();

        // AI 답변 표시
        addMessage('assistant', result.answer, {
            sources: result.sources,
            search_info: result.search_info,
        });

        // 대화 이력에 추가
        state.history.push({ role: 'assistant', content: result.answer });

        // 이력 제한 (최대 5턴 = 10메시지)
        if (state.history.length > CONFIG.MAX_HISTORY * 2) {
            state.history = state.history.slice(-CONFIG.MAX_HISTORY * 2);
        }
    } catch (err) {
        hideLoading();
        showError(err.message || '알 수 없는 오류가 발생했습니다.');
    } finally {
        state.isLoading = false;
        sendButton.disabled = false;
        questionInput.focus();
    }
}

// ━━━ 이벤트 바인딩 ━━━

chatForm.addEventListener('submit', handleSubmit);

// 글자 수 카운터
questionInput.addEventListener('input', () => {
    charCount.textContent = questionInput.value.length;
});

// 예시 질문 클릭
document.addEventListener('click', (e) => {
    if (e.target.matches('.example-list li')) {
        questionInput.value = e.target.textContent;
        charCount.textContent = questionInput.value.length;
        questionInput.focus();
    }
});

// Enter 전송 (Shift+Enter는 줄바꿈)
questionInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatForm.dispatchEvent(new Event('submit'));
    }
});
