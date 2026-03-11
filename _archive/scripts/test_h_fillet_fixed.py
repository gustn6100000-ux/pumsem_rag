import urllib.request
import urllib.error
import json
import os
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("SUPABASE_URL") + "/functions/v1/rag-chat"
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

tests = [
    {"query": "강판 전기아크용접 H형", "section_id": "13-2-4:sub=3. 전기아크용접(H형)"},
    {"query": "강판 전기아크용접 Fillet용접", "section_id": "13-2-4:sub=5. 전기아크용접(Fillet용접)"}
]

for t in tests:
    print(f"\n======================\nTesting {t['query']} ...\n======================")
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")

    data = {
        "question": "데이터를 마크다운 표로 깔끔하게 정리해줘.",
        "section_id": t["section_id"]
    }

    try:
        response = urllib.request.urlopen(req, data=json.dumps(data).encode("utf-8"), timeout=150)
        res_text = response.read().decode("utf-8")
        result = json.loads(res_text)
        print(f"--- Answer (First 800 chars) ---\n{result.get('answer', '')[:800]}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
    except Exception as e:
        print(f"Error: {e}")
