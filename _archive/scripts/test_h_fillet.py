import urllib.request
import urllib.error
import json
import os
from dotenv import load_dotenv

load_dotenv()
url = os.environ.get("SUPABASE_URL") + "/functions/v1/rag-chat"
key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

tests = [
    {"query": "강판 전기아크용접 H형", "sub": "3. 전기아크용접(H형)"},
    {"query": "강판 전기아크용접 Fillet용접", "sub": "5. 전기아크용접(Fillet용접)"}
]

for t in tests:
    print(f"\n======================\nTesting {t['query']} ...\n======================")
    req = urllib.request.Request(url, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")

    data = {
        "messages": [
            {"id": "test-id", "content": "13-2-4", "role": "user", "clarify": {
                "originalQuery": t["query"],
                "sectionId": "13-2-4",
                "subKeyword": t["sub"]
            }}
        ]
    }

    try:
        response = urllib.request.urlopen(req, data=json.dumps(data).encode("utf-8"), timeout=150)
        res_text = response.read().decode("utf-8")
        lines = res_text.splitlines()
        output = []
        for line in lines:
            if line.startswith("0:"):
                output.append(json.loads(line[2:]))
        
        full_text = "".join(output)
        print(f"--- Result (First 1000 chars) ---\n{full_text[:1000]}")
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
    except Exception as e:
        print(f"Error: {e}")
