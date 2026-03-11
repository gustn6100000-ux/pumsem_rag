# -*- coding: utf-8 -*-
"""Track A: Matrix Unroll — 다방면 테스트 스크립트

Test A: Pydantic 스키마 호환성 (새 필드 파싱)
Test B: 하위 호환성 (기존 형식 파싱 — properties 없음)
Test C: LLM API 실제 호출 (PE관 매트릭스 청크)
"""
import json
import os
import sys

sys.path.insert(0, r"G:\My Drive\Antigravity\pipeline\phase2_extraction")
from pathlib import Path
from dotenv import load_dotenv

# .env 로드
env_path = Path(r"G:\My Drive\Antigravity\pipeline\.env")
if env_path.exists():
    load_dotenv(env_path)
    print(f"✅ .env 로드 성공: {env_path}")
else:
    print(f"⚠️ .env 파일 없음: {env_path}")
    # 환경변수에서 직접 확인
    if "DEEPSEEK_API_KEY" in os.environ:
        print("   (환경변수에서 DEEPSEEK_API_KEY 발견)")
    else:
        print("   ❌ DEEPSEEK_API_KEY 환경변수도 없음 — Test C 불가")

print("=" * 60)

# ═══════════════════════════════════════════════════════════
# Test A: Pydantic 스키마 — 새 필드가 올바르게 파싱되는가?
# ═══════════════════════════════════════════════════════════
print("\n🅰️  Test A: Pydantic 스키마 호환성 (새 필드)")
print("-" * 60)

try:
    from pydantic import BaseModel, Field
    from typing import Optional

    # 스키마 임포트 (step2에서 정의한 것)
    from step2_llm_extractor import LLMRelationship, LLMExtractionResult

    # Case 1: 모든 새 필드 포함
    json_full = {
        "matrix_analysis_scratchpad": "2개 규격(63mm, 200mm) × 2직종 = 4관계",
        "entities": [
            {"type": "WorkType", "name": "PE관 접합", "spec": None, "unit": None, "quantity": None},
            {"type": "Labor", "name": "배관공", "spec": None, "unit": "인", "quantity": None},
        ],
        "relationships": [
            {
                "source": "PE관 접합", "target": "배관공",
                "relation_type": "REQUIRES_LABOR",
                "quantity": 0.184, "unit": "인",
                "properties": {"source_spec": "63mm"}
            },
            {
                "source": "PE관 접합", "target": "배관공",
                "relation_type": "REQUIRES_LABOR",
                "quantity": 0.521, "unit": "인",
                "properties": {"source_spec": "200mm"}
            },
        ],
        "summary": "PE관 접합 규격별 인력투입",
        "confidence": 0.95
    }

    result = LLMExtractionResult.model_validate(json_full)
    assert result.matrix_analysis_scratchpad == "2개 규격(63mm, 200mm) × 2직종 = 4관계"
    assert len(result.relationships) == 2
    assert result.relationships[0].properties == {"source_spec": "63mm"}
    assert result.relationships[1].properties == {"source_spec": "200mm"}
    print("  ✅ Case 1: 전체 필드 파싱 성공")
    print(f"     scratchpad = '{result.matrix_analysis_scratchpad}'")
    print(f"     rel[0].properties = {result.relationships[0].properties}")
    print(f"     rel[1].properties = {result.relationships[1].properties}")

    # Case 2: JSON 문자열에서 파싱 (LLM 출력 시뮬레이션)
    json_str = json.dumps(json_full, ensure_ascii=False)
    result2 = LLMExtractionResult.model_validate_json(json_str)
    assert result2.matrix_analysis_scratchpad == result.matrix_analysis_scratchpad
    assert result2.relationships[0].properties["source_spec"] == "63mm"
    print("  ✅ Case 2: JSON 문자열 파싱 성공 (LLM 출력 시뮬레이션)")

    print("\n🅰️  Test A: ✅ PASS\n")

except Exception as e:
    print(f"\n🅰️  Test A: ❌ FAIL — {e}\n")
    import traceback; traceback.print_exc()

# ═══════════════════════════════════════════════════════════
# Test B: 하위 호환성 — properties/scratchpad 없이도 파싱되는가?
# ═══════════════════════════════════════════════════════════
print("🅱️  Test B: 하위 호환성 (기존 형식 — 새 필드 없음)")
print("-" * 60)

try:
    # Case 1: properties 필드 없는 기존 relationship
    json_old_rel = {
        "source": "콘크리트 타설", "target": "특별인부",
        "relation_type": "REQUIRES_LABOR",
        "quantity": 0.33, "unit": "인"
        # properties 없음!
    }
    old_rel = LLMRelationship.model_validate(json_old_rel)
    assert old_rel.properties == {} or old_rel.properties is None or old_rel.properties == {}
    print(f"  ✅ Case 1: properties 없는 관계 파싱 성공 (properties={old_rel.properties})")

    # Case 2: 전체 결과에서 scratchpad 없는 기존 형식
    json_old_full = {
        "entities": [
            {"type": "WorkType", "name": "콘크리트 타설", "spec": "레미콘", "unit": "m³", "quantity": None},
        ],
        "relationships": [
            {"source": "콘크리트 타설", "target": "특별인부", "relation_type": "REQUIRES_LABOR", "quantity": 0.33, "unit": "인"},
        ],
        "summary": "콘크리트 타설 인력투입",
        "confidence": 0.90
        # matrix_analysis_scratchpad 없음!
    }
    old_result = LLMExtractionResult.model_validate(json_old_full)
    assert old_result.matrix_analysis_scratchpad == ""
    assert old_result.relationships[0].properties == {} or old_result.relationships[0].properties is not None
    print(f"  ✅ Case 2: scratchpad 없는 전체 결과 파싱 성공 (scratchpad='{old_result.matrix_analysis_scratchpad}')")

    # Case 3: JSON 문자열로도 기존 형식 파싱 가능한가?
    json_old_str = json.dumps(json_old_full, ensure_ascii=False)
    old_from_str = LLMExtractionResult.model_validate_json(json_old_str)
    print(f"  ✅ Case 3: 기존 형식 JSON 문자열 파싱 성공")

    # Case 4: Relationship 스키마(schemas.py)와 연동
    from schemas import Relationship, RelationType, EntityType

    rel_with_props = Relationship(
        source="PE관 접합",
        source_type=EntityType.WORK_TYPE,
        target="배관공",
        target_type=EntityType.LABOR,
        type=RelationType.REQUIRES_LABOR,
        quantity=0.184,
        unit="인",
        properties={"source_spec": "63mm"},
        source_chunk_id="test-001"
    )
    assert rel_with_props.properties["source_spec"] == "63mm"
    print(f"  ✅ Case 4: schemas.Relationship에 properties 전달 성공")

    rel_no_props = Relationship(
        source="콘크리트 타설",
        source_type=EntityType.WORK_TYPE,
        target="특별인부",
        target_type=EntityType.LABOR,
        type=RelationType.REQUIRES_LABOR,
        quantity=0.33,
        unit="인",
        source_chunk_id="test-002"
    )
    assert rel_no_props.properties == {}
    print(f"  ✅ Case 5: schemas.Relationship properties 기본값({{}}) 확인")

    print("\n🅱️  Test B: ✅ PASS\n")

except Exception as e:
    print(f"\n🅱️  Test B: ❌ FAIL — {e}\n")
    import traceback; traceback.print_exc()

# ═══════════════════════════════════════════════════════════
# Test C: 실제 LLM API 호출 — PE관 매트릭스 청크
# ═══════════════════════════════════════════════════════════
print("🅲  Test C: LLM API 실제 호출 (PE관 매트릭스 청크)")
print("-" * 60)

api_key = os.environ.get("DEEPSEEK_API_KEY", "")
if not api_key:
    print("  ⚠️ DEEPSEEK_API_KEY 없음 — Test C 스킵")
    print("\n🅲  Test C: ⏭️ SKIPPED\n")
else:
    try:
        import asyncio
        from openai import OpenAI
        from step2_llm_extractor import (
            SYSTEM_PROMPT, FEW_SHOT_EXAMPLE,
            LLMExtractionResult, LLM_TEMPERATURE,
        )

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )

        # PE관 매트릭스 테스트 청크 (기술서 §9에서 정의한 핵심 시나리오)
        test_chunk_text = """
## 섹션: 가스용 폴리에틸렌(PE)관 접합 및 부설 (W-0890~W-0895)
### 1개소당

| 구분 | 63mm | 75mm | 100mm | 150mm | 200mm |
| --- | --- | --- | --- | --- | --- |
| 배관공 | 0.184 | 0.201 | 0.279 | 0.366 | 0.521 |
| 특별인부 | 0.052 | 0.058 | 0.078 | 0.102 | 0.113 |

## 지시사항
위 품셈 텍스트와 테이블에서 엔티티(공종, 노무, 장비, 자재, 주석, 기준)와 관계를 추출하세요.
"""

        print(f"  📡 DeepSeek API 호출 중... (timeout 120초)")
        start = time.time() if 'time' in dir() else 0
        import time
        start = time.time()

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": FEW_SHOT_EXAMPLE + "\n\n---\n\n" + test_chunk_text},
            ],
            response_format={"type": "json_object"},
            temperature=LLM_TEMPERATURE,
            max_tokens=8192,
        )
        elapsed = time.time() - start
        print(f"  ⏱️ 응답 시간: {elapsed:.1f}초")

        raw_text = response.choices[0].message.content
        print(f"  📦 원시 응답 길이: {len(raw_text)} chars")

        # 파싱 테스트
        llm_result = LLMExtractionResult.model_validate_json(raw_text)

        print(f"\n  [결과 요약]")
        print(f"  📝 scratchpad: {llm_result.matrix_analysis_scratchpad}")
        print(f"  📊 entities: {len(llm_result.entities)}개")
        for e in llm_result.entities:
            print(f"     - [{e.type}] {e.name} (spec={e.spec}, qty={e.quantity}, unit={e.unit})")

        print(f"  🔗 relationships: {len(llm_result.relationships)}개")
        for r in llm_result.relationships:
            spec = (r.properties or {}).get("source_spec", "N/A")
            print(f"     - {r.source} →({r.relation_type})→ {r.target}: {r.quantity} {r.unit} [spec={spec}]")

        print(f"  📈 confidence: {llm_result.confidence}")
        print(f"  📋 summary: {llm_result.summary}")

        # ─── 검증 ───
        print(f"\n  [핵심 검증 3항목]")

        # 검증 1: CoT 발동
        has_scratchpad = bool(llm_result.matrix_analysis_scratchpad and len(llm_result.matrix_analysis_scratchpad) > 5)
        status1 = "✅" if has_scratchpad else "❌"
        print(f"  {status1} 검증 1: CoT(matrix_analysis_scratchpad) 발동 = {has_scratchpad}")

        # 검증 2: 200mm 규격 생존 (배관공 0.521 + 특별인부 0.113)
        rels_200mm = [r for r in llm_result.relationships
                      if (r.properties or {}).get("source_spec", "") == "200mm"]
        has_200mm = len(rels_200mm) >= 2
        status2 = "✅" if has_200mm else "❌"
        print(f"  {status2} 검증 2: 200mm 규격 생존 = {len(rels_200mm)}건 (최소 2건 필요)")
        for r in rels_200mm:
            print(f"       → {r.target}: qty={r.quantity} unit={r.unit}")

        # 검증 3: source_spec 무결성 (모든 관계에 source_spec이 있는가)
        has_all_specs = all(
            (r.properties or {}).get("source_spec")
            for r in llm_result.relationships
            if r.relation_type in ("REQUIRES_LABOR",)
        )
        status3 = "✅" if has_all_specs else "❌"
        print(f"  {status3} 검증 3: source_spec 무결성 = {has_all_specs}")

        # 검증 4: 총 관계 수 (5규격 × 2직종 = 10개)
        labor_rels = [r for r in llm_result.relationships if r.relation_type == "REQUIRES_LABOR"]
        expected = 10  # 5 specs × 2 labor types
        status4 = "✅" if len(labor_rels) == expected else "⚠️"
        print(f"  {status4} 검증 4: 총 REQUIRES_LABOR 관계 수 = {len(labor_rels)}개 (기대: {expected})")

        # 검증 5: 각 규격별 수량 정확성 확인
        expected_values = {
            ("63mm", "배관공"): 0.184,
            ("63mm", "특별인부"): 0.052,
            ("75mm", "배관공"): 0.201,
            ("75mm", "특별인부"): 0.058,
            ("100mm", "배관공"): 0.279,
            ("100mm", "특별인부"): 0.078,
            ("150mm", "배관공"): 0.366,
            ("150mm", "특별인부"): 0.102,
            ("200mm", "배관공"): 0.521,
            ("200mm", "특별인부"): 0.113,
        }
        correct_count = 0
        for r in llm_result.relationships:
            if r.relation_type == "REQUIRES_LABOR":
                spec = (r.properties or {}).get("source_spec", "")
                key = (spec, r.target)
                if key in expected_values:
                    if r.quantity == expected_values[key]:
                        correct_count += 1
                    else:
                        print(f"     ⚠️ {key}: 기대={expected_values[key]}, 실제={r.quantity}")

        status5 = "✅" if correct_count == len(expected_values) else "⚠️"
        print(f"  {status5} 검증 5: 수량 정확성 = {correct_count}/{len(expected_values)} 일치")

        # 종합 판정
        all_pass = has_scratchpad and has_200mm and has_all_specs and len(labor_rels) == expected and correct_count == len(expected_values)
        overall = "✅ PASS" if all_pass else "⚠️ PARTIAL PASS" if (has_200mm and has_all_specs) else "❌ FAIL"
        print(f"\n🅲  Test C: {overall}")

        # 원시 JSON 저장
        output_path = Path(r"C:\Users\lhs\test_track_a_result.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(json.loads(raw_text), f, ensure_ascii=False, indent=2)
        print(f"  💾 원시 결과 저장: {output_path}")

    except Exception as e:
        print(f"\n🅲  Test C: ❌ FAIL — {e}")
        import traceback; traceback.print_exc()

# ═══════════════════════════════════════════════════════════
# 최종 요약
# ═══════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("📊 테스트 최종 요약")
print("=" * 60)
