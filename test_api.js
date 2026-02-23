const EDGE_FUNCTION_URL = 'https://bfomacoarwtqzjfxszdr.supabase.co/functions/v1/rag-chat';

async function test() {
    try {
        console.log("Sending query: 플랜지 배관설치...");
        const response = await fetch(EDGE_FUNCTION_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                question: "플랜지 배관설치",
                history: []
            }),
        });

        const data = await response.json();
        if (data.type === 'clarify' && data.clarification.selector) {
            console.log("--- SELECTOR ITEMS SAMPLE ---");
            console.log(JSON.stringify(data.clarification.selector.items.slice(0, 3), null, 2));
            console.log("--- FILTERS ---");
            console.log(JSON.stringify(data.clarification.selector.filters, null, 2));
        }
    } catch (err) {
        console.error("Fetch failed:", err);
    }
}

test();
