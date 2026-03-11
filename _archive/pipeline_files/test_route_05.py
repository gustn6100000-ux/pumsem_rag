import json
import urllib.request
import os
from dotenv import load_dotenv

load_dotenv('g:/My Drive/Antigravity/pipeline/.env')
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

payload = {
    "question": "13-1-1 플랜트 배관 설치 배관용 탄소강관 200mm 옥내 용접식 10m 노무비",
    "history": []
}

req = urllib.request.Request(f"{url}/functions/v1/rag-chat", data=json.dumps(payload).encode('utf-8'))
req.add_header('apikey', key)
req.add_header('Authorization', f'Bearer {key}')
req.add_header('Content-Type', 'application/json')

try:
    response = urllib.request.urlopen(req)
    print("Success:", response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print("Error:", e.read().decode('utf-8'))
