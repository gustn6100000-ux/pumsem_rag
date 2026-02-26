import os
import json
import urllib.request
import gzip

project_ref = "bfomacoarwtqzjfxszdr"
token = os.environ.get("SUPABASE_ACCESS_TOKEN") or ""

with open("deploy_payload.json", "r", encoding="utf-8") as f:
    files_data = json.load(f)

# The deployment API expects a somewhat different payload 
# Let's try finding the local token from the CLI config
try:
    with open(os.path.expanduser("~/.supabase/access-token"), "r") as f:
        token = f.read().strip()
except Exception:
    pass

print(f"Token found: {'Yes' if token else 'No'}")
if not token:
    print("No Supabase access token found in ~/.supabase/access-token or environment. Deployment requires manual CLI execution.")
    exit(1)

# Just print out instructions to manual deploy if api fails
print("Deploy manually in WSL or via Docker:")
print("supabase functions deploy rag-chat --project-ref bfomacoarwtqzjfxszdr")
