import os
import json

files = []
directory = "edge-function"

for filename in os.listdir(directory):
    if filename.endswith(".ts"):
        filepath = os.path.join(directory, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        
        # Deploy tool expects 'name' and 'content' for each file
        files.append({
            "name": filename,
            "content": content
        })

with open("deploy_payload.json", "w", encoding="utf-8") as f:
    json.dump(files, f, ensure_ascii=False)
