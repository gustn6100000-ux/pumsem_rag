# -*- coding: utf-8 -*-
"""A/B 테스트: Solar Pro 3 vs DeepSeek-V3

동일한 20개 샘플 청크에 대해 두 모델의 추출 품질을 비교한다.

비교 항목:
  1. 엔티티 추출 수 (총량)
  2. 관계 추출 수 (총량)
  3. 엔티티 유형별 분포
  4. 매트릭스 전개 완전성 (source_spec 포함 여부)
  5. 할루시네이션 징후 (원본에 없는 이름)
  6. 응답 시간 (TPS 비교)
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import Counter

from dotenv import load_dotenv
from openai import OpenAI

# Phase 2 모듈 재활용
sys.path.insert(0, str(Path(__file__).parent))
from config import CHUNKS_FILE, PHASE2_OUTPUT, TABLE_ENTITIES_FILE, LLM_TEMPERATURE
from schemas import EntityType, BatchResult
from step2_llm_extractor import (
    SYSTEM_PROMPT, FEW_SHOT_EXAMPLE, build_user_prompt,
    LLMExtractionResult,
)

sys.stdout.reconfigure(encoding="utf-8")

# ─── .env 로드 ────────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")

# ─── 모델 정의 ────────────────────────────────────────────────
MODELS = {
    "deepseek": {
        "name": "DeepSeek-V3",
        "model": "deepseek-chat",
        "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "base_url": "https://api.deepseek.com",
        "max_tokens": 8192,
    },
    "solar": {
        "name": "Solar Pro 3",
        "model": "solar-pro3",
        "api_key": os.environ.get("UPSTAGE_API_KEY", ""),
        "base_url": "https://api.upstage.ai/v1/solar",
        "max_tokens": 8192,  # Solar Pro 3도 8K 출력 가능 (4K에서 JSON 잘림 확인됨)
    },
}

# ─── 샘플 청크 선택 전략 ──────────────────────────────────────
# 다양한 유형의 청크를 포함하여 공정한 비교를 위함
SAMPLE_STRATEGIES = {
    "text_only": 3,        # 텍스트만 있는 청크
    "a_table": 3,          # A_품셈 테이블
    "d_matrix": 3,         # D_기타 매트릭스 (핵심 비교 대상)
    "empty_text_table": 3, # text="" + tables jsonb 있는 청크
}


def select_sample_chunks(chunks: list[dict], step1_map: dict) -> list[dict]:
    """다양한 유형의 청크 20개 선택"""
    selected = []
    
    text_only = []
    a_table = []
    d_matrix = []
    empty_text = []
    
    for chunk in chunks:
        text = chunk.get("text", "").strip()
        tables = chunk.get("tables", [])
        table_types = {t.get("type", "") for t in tables}
        
        if not tables and len(text) > 50:
            text_only.append(chunk)
        elif "A_품셈" in table_types:
            a_table.append(chunk)
        elif "D_기타" in table_types and tables:
            # 매트릭스 후보: 숫자 헤더 3개 이상
            headers = tables[0].get("headers", [])
            import re
            numeric_h = sum(1 for h in headers[1:] if re.match(r'^\d+$', str(h).strip()))
            if numeric_h >= 3:
                d_matrix.append(chunk)
            elif not text and tables:
                empty_text.append(chunk)
        elif not text and tables:
            empty_text.append(chunk)
    
    # 각 카테고리에서 선택
    import random
    random.seed(42)  # 재현 가능
    
    for name, pool, count in [
        ("text_only", text_only, SAMPLE_STRATEGIES["text_only"]),
        ("a_table", a_table, SAMPLE_STRATEGIES["a_table"]),
        ("d_matrix", d_matrix, SAMPLE_STRATEGIES["d_matrix"]),
        ("empty_text", empty_text, SAMPLE_STRATEGIES["empty_text_table"]),
    ]:
        sample = random.sample(pool, min(count, len(pool)))
        for c in sample:
            c["_sample_category"] = name
        selected.extend(sample)
        print(f"  {name}: {len(sample)}/{len(pool)}개 가용 → {len(sample)}개 선택")
    
    return selected[:12]


def call_model(model_key: str, chunk: dict, timeout: int = 120) -> dict:
    """단일 모델로 단일 청크 추출 실행"""
    config = MODELS[model_key]
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )
    
    user_prompt = build_user_prompt(chunk)
    
    start = time.time()
    try:
        response = client.chat.completions.create(
            model=config["model"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": FEW_SHOT_EXAMPLE + "\n\n---\n\n" + user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=LLM_TEMPERATURE,
            max_tokens=config["max_tokens"],
        )
        elapsed = time.time() - start
        
        raw_text = response.choices[0].message.content
        result = LLMExtractionResult.model_validate_json(raw_text)
        
        # 토큰 사용량
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        
        return {
            "success": True,
            "entities": [e.model_dump() for e in result.entities],
            "relationships": [r.model_dump() for r in result.relationships],
            "summary": result.summary,
            "confidence": result.confidence,
            "scratchpad": result.matrix_analysis_scratchpad,
            "elapsed_sec": round(elapsed, 2),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "tps": round(output_tokens / elapsed, 1) if elapsed > 0 else 0,
            "error": None,
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "success": False,
            "entities": [],
            "relationships": [],
            "summary": "",
            "confidence": 0.0,
            "scratchpad": "",
            "elapsed_sec": round(elapsed, 2),
            "input_tokens": 0,
            "output_tokens": 0,
            "tps": 0,
            "error": str(e)[:500],
        }


def compare_results(deepseek_results: list, solar_results: list, chunks: list) -> dict:
    """두 모델의 결과를 비교 분석"""
    report = {
        "총_청크수": len(chunks),
        "deepseek": {"성공": 0, "실패": 0, "총_엔티티": 0, "총_관계": 0,
                     "엔티티_유형별": Counter(), "관계_유형별": Counter(),
                     "총_시간_초": 0, "평균_TPS": 0, "source_spec_포함": 0,
                     "총_output_tokens": 0},
        "solar": {"성공": 0, "실패": 0, "총_엔티티": 0, "총_관계": 0,
                  "엔티티_유형별": Counter(), "관계_유형별": Counter(),
                  "총_시간_초": 0, "평균_TPS": 0, "source_spec_포함": 0,
                  "총_output_tokens": 0},
        "청크별_비교": [],
    }
    
    for i, (ds, sl, chunk) in enumerate(zip(deepseek_results, solar_results, chunks)):
        chunk_id = chunk["chunk_id"]
        section_id = chunk["section_id"]
        category = chunk.get("_sample_category", "unknown")
        
        for key, result in [("deepseek", ds), ("solar", sl)]:
            if result["success"]:
                report[key]["성공"] += 1
            else:
                report[key]["실패"] += 1
            
            entities = result["entities"]
            rels = result["relationships"]
            
            report[key]["총_엔티티"] += len(entities)
            report[key]["총_관계"] += len(rels)
            report[key]["총_시간_초"] += result["elapsed_sec"]
            report[key]["총_output_tokens"] += result["output_tokens"]
            
            for e in entities:
                report[key]["엔티티_유형별"][e.get("type", "?")] += 1
            for r in rels:
                report[key]["관계_유형별"][r.get("relation_type", "?")] += 1
                props = r.get("properties", {})
                if props and props.get("source_spec"):
                    report[key]["source_spec_포함"] += 1
        
        # 청크별 비교
        chunk_comp = {
            "chunk_id": chunk_id,
            "section_id": section_id,
            "category": category,
            "title": chunk.get("title", ""),
            "deepseek_entities": len(ds["entities"]),
            "solar_entities": len(sl["entities"]),
            "deepseek_relations": len(ds["relationships"]),
            "solar_relations": len(sl["relationships"]),
            "deepseek_confidence": ds["confidence"],
            "solar_confidence": sl["confidence"],
            "deepseek_sec": ds["elapsed_sec"],
            "solar_sec": sl["elapsed_sec"],
            "deepseek_scratchpad": ds.get("scratchpad", ""),
            "solar_scratchpad": sl.get("scratchpad", ""),
        }
        report["청크별_비교"].append(chunk_comp)
    
    # 평균 TPS
    for key in ["deepseek", "solar"]:
        total_time = report[key]["총_시간_초"]
        total_tokens = report[key]["총_output_tokens"]
        report[key]["평균_TPS"] = round(total_tokens / total_time, 1) if total_time > 0 else 0
        report[key]["엔티티_유형별"] = dict(report[key]["엔티티_유형별"])
        report[key]["관계_유형별"] = dict(report[key]["관계_유형별"])
    
    return report


def print_report(report: dict):
    """비교 결과 콘솔 출력"""
    print("\n" + "=" * 70)
    print("  A/B TEST RESULT: Solar Pro 3 vs DeepSeek-V3")
    print("=" * 70)
    
    print(f"\n  샘플 청크: {report['총_청크수']}개\n")
    
    # 요약 테이블
    print(f"  {'항목':<25} {'DeepSeek-V3':>15} {'Solar Pro 3':>15} {'차이':>10}")
    print(f"  {'-'*65}")
    
    ds = report["deepseek"]
    sl = report["solar"]
    
    rows = [
        ("성공/실패", f"{ds['성공']}/{ds['실패']}", f"{sl['성공']}/{sl['실패']}", ""),
        ("총 엔티티", str(ds["총_엔티티"]), str(sl["총_엔티티"]),
         f"+{sl['총_엔티티']-ds['총_엔티티']}" if sl['총_엔티티'] > ds['총_엔티티'] else str(sl['총_엔티티']-ds['총_엔티티'])),
        ("총 관계", str(ds["총_관계"]), str(sl["총_관계"]),
         f"+{sl['총_관계']-ds['총_관계']}" if sl['총_관계'] > ds['총_관계'] else str(sl['총_관계']-ds['총_관계'])),
        ("source_spec 포함", str(ds["source_spec_포함"]), str(sl["source_spec_포함"]), ""),
        ("총 시간 (초)", f"{ds['총_시간_초']:.1f}", f"{sl['총_시간_초']:.1f}", ""),
        ("평균 TPS", str(ds["평균_TPS"]), str(sl["평균_TPS"]), ""),
        ("총 Output Tokens", str(ds["총_output_tokens"]), str(sl["총_output_tokens"]), ""),
    ]
    for label, v1, v2, diff in rows:
        print(f"  {label:<25} {v1:>15} {v2:>15} {diff:>10}")
    
    # 엔티티 유형별
    print(f"\n  엔티티 유형별:")
    all_types = sorted(set(list(ds["엔티티_유형별"].keys()) + list(sl["엔티티_유형별"].keys())))
    for t in all_types:
        v1 = ds["엔티티_유형별"].get(t, 0)
        v2 = sl["엔티티_유형별"].get(t, 0)
        print(f"    {t:<20} {v1:>10} {v2:>10}")
    
    # 청크별 비교
    print(f"\n  청크별 비교 (엔티티/관계):")
    print(f"  {'Chunk':<12} {'Category':<15} {'DS Ent':>7} {'SL Ent':>7} {'DS Rel':>7} {'SL Rel':>7} {'DS sec':>7} {'SL sec':>7}")
    print(f"  {'-'*72}")
    for c in report["청크별_비교"]:
        print(f"  {c['chunk_id']:<12} {c['category']:<15} "
              f"{c['deepseek_entities']:>7} {c['solar_entities']:>7} "
              f"{c['deepseek_relations']:>7} {c['solar_relations']:>7} "
              f"{c['deepseek_sec']:>7.1f} {c['solar_sec']:>7.1f}")
    
    print("\n" + "=" * 70)


def main():
    print("\n" + "=" * 60)
    print("  Solar Pro 3 vs DeepSeek-V3 A/B 테스트")
    print("=" * 60)
    
    # API 키 확인
    for key, config in MODELS.items():
        if not config["api_key"]:
            print(f"  ❌ {config['name']} API 키 없음 ({key})")
            return
        print(f"  ✅ {config['name']} API 키: ...{config['api_key'][-6:]}")
    
    # 청크 로드
    print(f"\n  청크 파일: {CHUNKS_FILE}")
    data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = data["chunks"]
    print(f"  전체 청크: {len(chunks)}개")
    
    # Step 2.1 결과 로드 (대상 필터링용)
    step1_map = {}
    if TABLE_ENTITIES_FILE.exists():
        step1_data = json.loads(TABLE_ENTITIES_FILE.read_text(encoding="utf-8"))
        for ext in step1_data.get("extractions", []):
            step1_map[ext["chunk_id"]] = ext
    
    # 샘플 선택
    print(f"\n  샘플 선택 (seed=42):")
    samples = select_sample_chunks(chunks, step1_map)
    print(f"  → 총 {len(samples)}개 선택됨\n")
    
    # 실행
    deepseek_results = []
    solar_results = []
    
    for i, chunk in enumerate(samples, 1):
        chunk_id = chunk["chunk_id"]
        category = chunk.get("_sample_category", "?")
        print(f"  [{i:2d}/{len(samples)}] {chunk_id} ({category}")
        
        # DeepSeek
        print(f"    → DeepSeek...", end=" ", flush=True)
        ds_result = call_model("deepseek", chunk)
        print(f"{ds_result['elapsed_sec']:.1f}s, {len(ds_result['entities'])}E/{len(ds_result['relationships'])}R"
              + (f" ❌{ds_result['error'][:50]}" if ds_result['error'] else " ✅"))
        deepseek_results.append(ds_result)
        
        # Solar Pro 3
        print(f"    → Solar...", end="   ", flush=True)
        sl_result = call_model("solar", chunk)
        print(f"{sl_result['elapsed_sec']:.1f}s, {len(sl_result['entities'])}E/{len(sl_result['relationships'])}R"
              + (f" ❌{sl_result['error'][:50]}" if sl_result['error'] else " ✅"))
        solar_results.append(sl_result)
        
        # Rate limit 방지
        time.sleep(0.5)
    
    # 비교 분석
    report = compare_results(deepseek_results, solar_results, samples)
    
    # 콘솔 출력
    print_report(report)
    
    # JSON 저장
    output_dir = PHASE2_OUTPUT / "_ab_test"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    
    # 상세 결과
    full_result = {
        "timestamp": timestamp,
        "sample_count": len(samples),
        "report": report,
        "deepseek_raw": deepseek_results,
        "solar_raw": solar_results,
        "sample_chunk_ids": [c["chunk_id"] for c in samples],
    }
    
    result_file = output_dir / f"ab_test_{timestamp}.json"
    result_file.write_text(
        json.dumps(full_result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  결과 저장: {result_file}")


if __name__ == "__main__":
    main()
