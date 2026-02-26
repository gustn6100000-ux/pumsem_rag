// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// config.ts — 환경 설정, Supabase 클라이언트, CORS, Rate Limit
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ━━━ 환경변수 ━━━
export const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY")!;
export const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
export const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
export const RAG_API_KEY = Deno.env.get("RAG_API_KEY") || "";
// DeepSeek v3.2 — 의도 분석 전용 LLM
export const DEEPSEEK_API_KEY = Deno.env.get("DEEPSEEK_API_KEY") || "";
export const DEEPSEEK_URL = "https://api.deepseek.com/chat/completions";

// ━━━ Supabase 클라이언트 ━━━
export const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY);

// ━━━ CORS ━━━
// (Codex F1) CORS allowlist — '*' 금지
export const ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "https://pumsem-chat.pages.dev",
    "https://antigravity-chatbot.pages.dev",
];
// Cloudflare Pages preview URL 패턴 (ex: 1e3f64d6.antigravity-chatbot.pages.dev)
export const ALLOWED_SUFFIXES = [
    ".antigravity-chatbot.pages.dev",
    ".pumsem-chat.pages.dev",
];

export function getCorsHeaders(req: Request): Record<string, string> {
    const origin = req.headers.get("Origin") || "";
    const isAllowed = ALLOWED_ORIGINS.includes(origin) ||
        ALLOWED_SUFFIXES.some(suffix => {
            try { return new URL(origin).hostname.endsWith(suffix); } catch { return false; }
        });
    return {
        "Access-Control-Allow-Origin": isAllowed ? origin : "",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, x-api-key",
        "Vary": "Origin",
    };
}

// ━━━ Rate Limiting ━━━
// (Codex F1) IP 기반 Rate Limiting — 분당 10회
export const rateLimitMap = new Map<string, { count: number; resetAt: number }>();
export const RATE_LIMIT_MAX = 10;
export const RATE_LIMIT_WINDOW_MS = 60_000;

export function checkRateLimit(ip: string): boolean {
    const now = Date.now();
    const entry = rateLimitMap.get(ip);
    if (!entry || now > entry.resetAt) {
        rateLimitMap.set(ip, { count: 1, resetAt: now + RATE_LIMIT_WINDOW_MS });
        return true;
    }
    entry.count++;
    return entry.count <= RATE_LIMIT_MAX;
}
