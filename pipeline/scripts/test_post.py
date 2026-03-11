import asyncio
import httpx
import json

async def main():
    async with httpx.AsyncClient() as client:
        res = await client.post('https://bfomacoarwtqzjfxszdr.supabase.co/functions/v1/rag-chat',
                                json={'question': '전기아크용접'},
                                timeout=60.0)
        with open('test_post_output.txt', 'w', encoding='utf-8') as f:
            f.write(f"Status: {res.status_code}\n")
            f.write(json.dumps(res.json(), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
