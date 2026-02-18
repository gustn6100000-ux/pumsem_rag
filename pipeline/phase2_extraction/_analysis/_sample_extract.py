# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8")

data = json.loads(open(
    r"G:\내 드라이브\Antigravity\python_code\phase2_output\llm_entities.json",
    encoding="utf-8"
).read())

# 엔티티와 관계가 풍부한 extraction 샘플 출력
for ext in data["extractions"]:
    if len(ext["entities"]) >= 4 and len(ext["relationships"]) >= 3:
        out = {
            "chunk_id": ext["chunk_id"],
            "section_id": ext["section_id"],
            "title": ext["title"],
            "summary": ext.get("summary", ""),
            "confidence": ext.get("confidence", 0),
            "entities": ext["entities"][:6],  # 최대 6개만
            "relationships": ext["relationships"][:5],  # 최대 5개만
        }
        with open(r"G:\내 드라이브\Antigravity\python_code\phase2_output\_sample_extraction.json", "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print("저장 완료")
        break
