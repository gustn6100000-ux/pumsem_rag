// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// embedding.ts — Gemini 임베딩 생성
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import { GEMINI_API_KEY } from "./config.ts";

// Why gemini-embedding-001: Step 2.7에서 동일 모델로 임베딩 생성
const EMBEDDING_URL =
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent";

export async function generateEmbedding(text: string): Promise<number[]> {
    // DB에 768차원으로 저장됨 → outputDimensionality 명시 필수
    // gemini-embedding-001 기본 출력 3072차원이므로 미지정 시 벡터 파싱 실패
    const embeddingBody = {
        model: "models/gemini-embedding-001",
        content: { parts: [{ text }] },
        outputDimensionality: 768,
    };

    const response = await fetch(`${EMBEDDING_URL}?key=${GEMINI_API_KEY}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(embeddingBody),
    });

    if (!response.ok) {
        // 1회 재시도 (Codex 권장 폴백)
        const retry = await fetch(`${EMBEDDING_URL}?key=${GEMINI_API_KEY}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(embeddingBody),
        });
        if (!retry.ok) {
            throw new Error(`Embedding API failed: ${retry.status}`);
        }
        const data = await retry.json();
        return data.embedding?.values ?? [];
    }

    const data = await response.json();
    return data.embedding?.values ?? [];
}
