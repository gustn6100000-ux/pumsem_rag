# -*- coding: utf-8 -*-
"""13-2-4 강판 전기아크용접 — DeepSeek vs Solar Pro 3 심층 비교"""
import json, os, sys, time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent.parent / ".env")

from step2_llm_extractor import (
    SYSTEM_PROMPT, FEW_SHOT_EXAMPLE, build_user_prompt, LLMExtractionResult,
)
from config import CHUNKS_FILE, LLM_TEMPERATURE

sys.stdout.reconfigure(encoding="utf-8")

# 클라이언트
clients = {
    "deepseek": OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    ),
    "solar": OpenAI(
        api_key=os.environ["UPSTAGE_API_KEY"],
        base_url="https://api.upstage.ai/v1/solar",
    ),
}
MODELS = {"deepseek": "deepseek-chat", "solar": "solar-pro3"}

# 13-2-4 청크 로드
chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))["chunks"]
target = [c for c in chunks if c.get("section_id", "").startswith("13-2-4")]
print(f"=== 13-2-4 강판 전기아크용접 ===")
print(f"총 {len(target)}개 청크\n")

# 청크 개요
for c in target:
    tables = c.get("tables", [])
    t_info = []
    for t in tables:
        h = t.get("headers", [])
        r = len(t.get("rows", []))
        t_info.append(f"{t.get('type','')}({r}rows, {len(h)}cols)")
    print(f"  {c['chunk_id']}: text={len(c.get('text',''))}ch, "
          f"tables=[{', '.join(t_info)}]")

print()

# 각 청크에 대해 양쪽 모델 실행
all_results = {}

for i, chunk in enumerate(target, 1):
    cid = chunk["chunk_id"]
    print(f"\n{'='*60}")
    print(f"[{i}/{len(target)}] {cid}")
    print(f"  Title: {chunk.get('title','')}")
    
    # 테이블 헤더 출력 (원본 확인)
    for j, t in enumerate(chunk.get("tables", [])):
        h = t.get("headers", [])
        print(f"  Table[{j}] type={t.get('type','')} headers({len(h)}): {h}")
        # 처음 2행만 출력
        for k, row in enumerate(t.get("rows", [])[:2]):
            print(f"    row[{k}]: {row}")
    
    prompt = build_user_prompt(chunk)
    print(f"  Prompt: {len(prompt)} chars")
    
    chunk_result = {}
    
    for model_key in ["deepseek", "solar"]:
        label = "DS" if model_key == "deepseek" else "SL"
        print(f"\n  [{label}] Calling {MODELS[model_key]}...", end=" ", flush=True)
        
        start = time.time()
        try:
            resp = clients[model_key].chat.completions.create(
                model=MODELS[model_key],
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": FEW_SHOT_EXAMPLE + "\n---\n" + prompt},
                ],
                response_format={"type": "json_object"},
                temperature=LLM_TEMPERATURE,
                max_tokens=8192,
            )
            raw = resp.choices[0].message.content
            elapsed = time.time() - start
            usage = resp.usage
            
            try:
                result = LLMExtractionResult.model_validate_json(raw)
                entities = [e.model_dump() for e in result.entities]
                relationships = [r.model_dump() for r in result.relationships]
                
                print(f"{elapsed:.1f}s | {len(entities)}E/{len(relationships)}R | "
                      f"in={usage.prompt_tokens} out={usage.completion_tokens}")
                
                # scratchpad
                if result.matrix_analysis_scratchpad:
                    print(f"    Scratchpad: {result.matrix_analysis_scratchpad}")
                
                # 엔티티 요약
                from collections import Counter
                e_types = Counter(e["type"] for e in entities)
                print(f"    Entities: {dict(e_types)}")
                
                # 관계 요약
                r_types = Counter(r["relation_type"] for r in relationships)
                print(f"    Relations: {dict(r_types)}")
                
                # 관계 상세 (REQUIRES_LABOR만)
                labor_rels = [r for r in relationships if r["relation_type"] == "REQUIRES_LABOR"]
                if labor_rels:
                    print(f"    REQUIRES_LABOR details ({len(labor_rels)}):")
                    for r in labor_rels[:15]:
                        spec = r.get("properties", {}).get("source_spec", "N/A")
                        print(f"      {r['source'][:25]} → {r['target']}: "
                              f"qty={r.get('quantity')} {r.get('unit','')} spec={spec}")
                    if len(labor_rels) > 15:
                        print(f"      ... +{len(labor_rels)-15} more")
                
                chunk_result[model_key] = {
                    "success": True,
                    "entities": entities,
                    "relationships": relationships,
                    "scratchpad": result.matrix_analysis_scratchpad,
                    "confidence": result.confidence,
                    "summary": result.summary,
                    "elapsed": elapsed,
                    "in_tokens": usage.prompt_tokens,
                    "out_tokens": usage.completion_tokens,
                }
            except Exception as e:
                print(f"VALIDATION FAIL ({elapsed:.1f}s): {str(e)[:200]}")
                chunk_result[model_key] = {
                    "success": False, "error": str(e)[:500],
                    "elapsed": elapsed, "raw_preview": raw[:500] if raw else "",
                }
        except Exception as e:
            elapsed = time.time() - start
            print(f"API ERROR ({elapsed:.1f}s): {str(e)[:200]}")
            chunk_result[model_key] = {
                "success": False, "error": str(e)[:500], "elapsed": elapsed,
            }
        
        time.sleep(0.5)
    
    all_results[cid] = chunk_result

# 결과 JSON 저장
output_dir = Path(__file__).parent.parent / "phase2_output" / "_ab_test"
output_dir.mkdir(parents=True, exist_ok=True)
result_file = output_dir / "ab_test_13-2-4_detail.json"
result_file.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n\n결과 저장: {result_file}")
print("Done.")
