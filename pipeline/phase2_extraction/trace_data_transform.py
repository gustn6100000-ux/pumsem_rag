# -*- coding: utf-8 -*-
"""Phase 0-2: 데이터 변형 경로 추적

13-2-3 강관용접 데이터가 각 파이프라인 단계에서 어떻게 변형되는지 추적한다.

경로: llm_entities.json → merged_entities.json → normalized_entities.json → Supabase DB

사용법:
    python trace_data_transform.py
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import (
    TABLE_ENTITIES_FILE,
    LLM_ENTITIES_FILE,
    MERGED_ENTITIES_FILE,
    PHASE2_OUTPUT,
)

sys.stdout.reconfigure(encoding="utf-8")

NORMALIZED_FILE = PHASE2_OUTPUT / "normalized_entities.json"
TARGET_SECTION = "13-2-3"

# 원본 기대값 (MD 파일에서 확인한 값)
EXPECTED_VALUES = {
    ("φ200", "SCH 20", "용접공"): 0.287,
    ("φ200", "SCH 40", "플랜트 용접공"): 0.287,
    ("φ200", "SCH 60", "플랜트 용접공"): 0.325,
    ("φ200", "SCH 80", "플랜트 용접공"): 0.362,
    ("φ15", "SCH 40", "플랜트 용접공"): 0.066,
    ("φ15", "SCH 80", "플랜트 용접공"): 0.075,
    ("φ350", "SCH 20", "용접공"): 0.442,
}


def load_json(path):
    """JSON 파일 로드 (존재하지 않으면 빈 dict)"""
    if not path.exists():
        print(f"  ⚠️ 파일 없음: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def search_in_extractions(data, section_id):
    """extraction 데이터에서 특정 section의 엔티티/관계 검색"""
    results = {
        "entities": [],
        "relationships": [],
    }

    # extractions 구조: {"extractions": [{"chunk_id": ..., "entities": [...], "relationships": [...]}]}
    extractions = data.get("extractions", [])
    if not extractions:
        # normalized_entities는 다른 구조일 수 있음
        extractions = data.get("chunks", [])

    for ext in extractions:
        ext_section = ext.get("section_id", "")
        if ext_section != section_id:
            continue

        # 엔티티
        for ent in ext.get("entities", []):
            results["entities"].append({
                "name": ent.get("name", ""),
                "type": ent.get("type", ""),
                "spec": ent.get("spec", ""),
                "unit": ent.get("unit", ""),
                "quantity": ent.get("quantity"),
                "source_method": ent.get("source_method", ""),
                "chunk_id": ext.get("chunk_id", ""),
            })

        # 관계
        for rel in ext.get("relationships", []):
            results["relationships"].append({
                "source": rel.get("source", ""),
                "target": rel.get("target", ""),
                "type": rel.get("type", ""),
                "quantity": rel.get("quantity"),
                "unit": rel.get("unit", ""),
                "chunk_id": ext.get("chunk_id", ""),
            })

    return results


def find_value_matches(results, diameter, sch, job_name, expected_qty):
    """특정 규격/직종/수량 매칭 검색"""
    matches = []

    # 엔티티에서 찾기
    for ent in results["entities"]:
        name = ent.get("name", "")
        qty = ent.get("quantity")

        # 이름에 구경과 SCH가 포함되는지 확인
        d_clean = diameter.replace("φ", "").replace("Φ", "")
        if d_clean in name.replace(" ", "") or diameter in name:
            matches.append({
                "where": "entity",
                "name": name,
                "quantity": qty,
                "type": ent.get("type", ""),
                "match_qty": qty == expected_qty if qty is not None else None,
            })

    # 관계에서 찾기
    for rel in results["relationships"]:
        source = rel.get("source", "")
        target = rel.get("target", "")
        qty = rel.get("quantity")

        d_clean = diameter.replace("φ", "").replace("Φ", "")
        if (d_clean in source.replace(" ", "") or diameter in source):
            if job_name.replace(" ", "") in target.replace(" ", "") or target.replace(" ", "") in job_name.replace(" ", ""):
                matches.append({
                    "where": "relationship",
                    "source": source,
                    "target": target,
                    "quantity": qty,
                    "match_qty": abs(qty - expected_qty) < 0.001 if qty is not None else None,
                })

    return matches


def trace_all():
    """전체 파이프라인 추적"""
    print("=" * 60)
    print("Phase 0-2: 데이터 변형 경로 추적")
    print(f"대상: section {TARGET_SECTION}")
    print("=" * 60)

    steps = [
        ("1. table_entities (step1 규칙)", TABLE_ENTITIES_FILE),
        ("2. llm_entities (step2 LLM)", LLM_ENTITIES_FILE),
        ("3. merged_entities (step3 병합)", MERGED_ENTITIES_FILE),
        ("4. normalized_entities (step4 정규화)", NORMALIZED_FILE),
    ]

    step_results = {}
    trace_report = []

    for step_name, file_path in steps:
        print(f"\n{'─' * 50}")
        print(f"📂 {step_name}")
        print(f"   파일: {file_path.name}")

        data = load_json(file_path)
        if not data:
            continue

        results = search_in_extractions(data, TARGET_SECTION)
        step_results[step_name] = results

        wt_count = sum(1 for e in results["entities"] if e.get("type") in ("WorkType", "WORK_TYPE"))
        labor_count = sum(1 for e in results["entities"] if e.get("type") in ("Labor", "LABOR"))
        rel_count = len(results["relationships"])

        print(f"   엔티티 총: {len(results['entities'])}개 (WorkType: {wt_count}, Labor: {labor_count})")
        print(f"   관계 총: {rel_count}개")

        # WorkType 이름 샘플
        wt_names = sorted(set(
            e["name"] for e in results["entities"]
            if e.get("type") in ("WorkType", "WORK_TYPE")
        ))
        if wt_names:
            print(f"   WorkType 샘플: {wt_names[:5]}")

    # 기대값 추적
    print(f"\n{'═' * 60}")
    print("📊 기대값 추적 (원본 MD 기준)")
    print(f"{'═' * 60}")

    for (diameter, sch, job), expected_qty in EXPECTED_VALUES.items():
        print(f"\n🔍 {diameter} {sch} → {job} = {expected_qty}")

        for step_name, results in step_results.items():
            matches = find_value_matches(results, diameter, sch, job, expected_qty)
            if matches:
                for m in matches:
                    status = "✅" if m.get("match_qty") else "❌"
                    if m["where"] == "entity":
                        print(f"   {step_name}: {status} entity [{m['name'][:40]}] qty={m['quantity']}")
                    else:
                        print(f"   {step_name}: {status} rel [{m['source'][:30]} → {m['target'][:20]}] qty={m['quantity']}")
            else:
                print(f"   {step_name}: ⚪ 해당 없음")

            trace_report.append({
                "diameter": diameter,
                "sch": sch,
                "job": job,
                "expected": expected_qty,
                "step": step_name,
                "found": len(matches) > 0,
                "matches": matches,
            })

    # 전체 요약
    print(f"\n{'═' * 60}")
    print("📋 전체 요약")
    print(f"{'═' * 60}")

    for step_name in step_results:
        found = sum(1 for r in trace_report if r["step"] == step_name and r["found"])
        total = sum(1 for r in trace_report if r["step"] == step_name)
        exact = sum(
            1 for r in trace_report
            if r["step"] == step_name and r["found"]
            and any(m.get("match_qty") for m in r.get("matches", []))
        )
        print(f"  {step_name}: 발견 {found}/{total}건, 수치 일치 {exact}건")

    # JSON 저장
    output = {
        "target_section": TARGET_SECTION,
        "expected_values": {f"{d}_{s}_{j}": v for (d, s, j), v in EXPECTED_VALUES.items()},
        "step_summary": {
            step_name: {
                "entity_count": len(results["entities"]),
                "worktype_count": sum(1 for e in results["entities"] if e.get("type") in ("WorkType", "WORK_TYPE")),
                "relationship_count": len(results["relationships"]),
            }
            for step_name, results in step_results.items()
        },
        "trace_report": trace_report,
    }

    output_file = PHASE2_OUTPUT / "data_transform_trace.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 추적 결과 저장: {output_file}")


if __name__ == "__main__":
    trace_all()
