# -*- coding: utf-8 -*-
"""Step 2.2: LLM 기반 엔티티 & 관계 추출

Step 2.1(테이블 규칙 추출)에서 커버하지 못한 청크를 Gemini 3.0 Flash로 처리한다.

대상:
  1. 테이블이 없는 텍스트 전용 청크 (364건)
  2. D_기타 / C_구분설명 테이블을 가진 청크 (~2,092건)
  3. Step 2.1에서 WorkType이 추출되지 않은 청크
  4. Step 2.1 경고가 있는 청크 (인식 불가 헤더 등)

llm-structured-extraction 스킬 적용:
  - Pydantic 스키마로 구조화된 출력 강제
  - Few-shot + Chain-of-Thought 프롬프트
  - 비동기 배치 처리 (동시 5개)
  - 지수 백오프 재시도 (최대 3회)
  - 자기 교정 (Self-Correction) 패턴
"""
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from config import (
    CHUNKS_FILE, PHASE2_OUTPUT, TABLE_ENTITIES_FILE, LLM_ENTITIES_FILE,
    LLM_MODEL, LLM_TEMPERATURE, LLM_CONCURRENCY, LLM_RETRY_COUNT, LLM_MAX_TOKENS,
)
from schemas import (
    Entity, Relationship, ChunkExtraction, BatchResult,
    EntityType, RelationType,
)

ISOLATED_CHUNKS = {
    "C-0172", "C-0578-A", "C-0578-B", 
    "C-0623-A", "C-0623-B", "C-0759", 
    "C-0923", "C-1124", "C-1149"
}

sys.stdout.reconfigure(encoding="utf-8")


# ─── .env 로드 & DeepSeek 클라이언트 초기화 ────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")

client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)


# ─── LLM 출력용 Pydantic 스키마 (간소화) ─────────────────────
# Why: DeepSeek JSON mode는 프롬프트에 스키마를 포함해야 함.
#      Phase 2의 Entity 전체 스키마 대신 추출에 필수인 필드만 포함.

class LLMEntity(BaseModel):
    """LLM이 추출할 엔티티"""
    type: str = Field(description="엔티티 유형: WorkType, Labor, Equipment, Material, Note, Standard 중 하나")
    name: str = Field(description="엔티티 이름 (원본 텍스트에 있는 정확한 이름)")
    spec: Optional[str] = Field(None, description="규격/사양 (예: 0.6m³, D13, 25-24-15)")
    unit: Optional[str] = Field(None, description="단위 (예: 인, m³, 대, ton)")
    quantity: Optional[float] = Field(None, description="수량 (숫자만)")


class LLMRelationship(BaseModel):
    """LLM이 추출할 관계"""
    source: str = Field(description="출발 엔티티 이름")
    target: str = Field(description="도착 엔티티 이름")
    relation_type: str = Field(description="관계: REQUIRES_LABOR, REQUIRES_EQUIPMENT, USES_MATERIAL, HAS_NOTE, APPLIES_STANDARD 중 하나")
    relation_type: str = Field(description="관계: REQUIRES_LABOR, REQUIRES_EQUIPMENT, USES_MATERIAL, HAS_NOTE, APPLIES_STANDARD 중 하나")
    quantity: Optional[float] = Field(None, description="투입 수량")
    unit: Optional[str] = Field(None, description="투입 단위")
    per_unit: Optional[str] = Field(None, description="기준 단위 (예: '1m3당', '100m당')")
    # 💡 [Track A] 규격별 수량 추적을 위한 자유형 Dict
    # Why: 매트릭스(2D) 표에서 동일 source-target 쌍이 규격별로 다른 수량을 가질 때
    #       {"source_spec": "200mm"} 형태로 규격을 기록하여 관계를 고유하게 식별
    properties: Optional[dict] = Field(default_factory=dict, description="추가 속성 (source_spec 등)")


class LLMExtractionResult(BaseModel):
    """LLM 추출 전체 결과"""
    # 💡 [Track A] Chain-of-Thought 버퍼
    # Why: 매트릭스 표 파싱 시 LLM이 "몇 개 규격을 전개할 것인지" 사고 과정을 기록
    #       이를 통해 누락 여부를 사후 검증할 수 있음 (디버깅용, 파이프라인에 영향 없음)
    matrix_analysis_scratchpad: Optional[str] = Field(
        default="",
        description="다중 규격 표 파싱 시 LLM의 사고 과정 기록"
    )
    entities: list[LLMEntity] = Field(default_factory=list)
    relationships: list[LLMRelationship] = Field(default_factory=list)
    summary: str = Field(default="", description="청크 내용 1줄 요약 (한국어)")
    confidence: float = Field(default=0.8, ge=0, le=1, description="추출 신뢰도 0~1")


# ─── 프롬프트 ─────────────────────────────────────────────────

SYSTEM_PROMPT = """당신은 건설 표준품셈 문서에서 엔티티(개체)와 관계를 추출하는 전문가입니다.

## 엔티티 유형
- **WorkType**: 공종/작업 (예: 콘크리트 타설, 철근 가공, 거푸집 설치)
- **Labor**: 노무/인력 (예: 특별인부, 보통인부, 철근공, 비계공, 형틀목공)
- **Equipment**: 장비/기계 (예: 굴착기, 크레인, 레미콘, 펌프카)
- **Material**: 자재/재료 (예: 시멘트, 골재, 철근, 거푸집판)
- **Note**: 주석/조건/할증 (예: 할증률, 적용 조건, 보정계수)
- **Standard**: 적용 기준/규격 (예: KCS, KDS, 콘크리트 표준시방서)

## 관계 유형
- **REQUIRES_LABOR**: 공종 → 노무 (인력 투입, 반드시 quantity/unit 포함)
- **REQUIRES_EQUIPMENT**: 공종 → 장비 (장비 투입)
- **USES_MATERIAL**: 공종 → 자재 (자재 사용)
- **HAS_NOTE**: 공종/섹션 → 주석 (조건/할증)
- **APPLIES_STANDARD**: 공종 → 기준 (적용 규격)

## 규칙
1. 원본 텍스트에 **실제로 존재하는** 이름과 수치만 추출한다 (할루시네이션 금지)
2. 수량은 반드시 원본의 숫자를 그대로 사용한다
3. 같은 엔티티를 다른 이름으로 중복 추출하지 않는다
4. 테이블이 있으면 행/열 구조를 정확히 해석한다
5. '1m³당', '100m당' 등 기준 단위(per_unit)도 반드시 포함하여 추출한다.
6. 확실하지 않은 정보는 confidence를 낮게 설정한다
7. 🚨 **[매트릭스 표 전개 규칙]** 가로축에 여러 규격(63mm, 75mm, 200mm 등)이 나열된 표는
   절대 중간 규격을 생략하거나 "등"으로 묶지 마십시오.
   **모든 규격에 대해 독립된 관계(relationship) 객체를 100% 전개(Unroll)**해야 합니다.
8. 각 관계의 `properties.source_spec`에 해당 수량의 **정확한 규격 문자열**을 반드시 기록하십시오.
9. 매트릭스 표가 감지되면 `matrix_analysis_scratchpad`에 "[규격 수] × [직종 수] = [총 관계 수]"
   형태로 사고 과정을 기록한 뒤 전개를 시작하십시오.

## 출력 JSON 스키마 (반드시 이 형식으로 출력)
```json
{
  "matrix_analysis_scratchpad": "다중 규격 표가 있으면 사고 과정을 여기에 기록",
  "entities": [{"type": "WorkType|Labor|Equipment|Material|Note|Standard", "name": "문자열", "spec": "문자열 or null", "unit": "문자열 or null", "quantity": 숫자 or null}],
  "relationships": [{
    "source": "출발엔티티명",
    "target": "도착엔티티명",
    "relation_type": "REQUIRES_LABOR|REQUIRES_EQUIPMENT|USES_MATERIAL|HAS_NOTE|APPLIES_STANDARD",
    "quantity": 숫자 or null,
    "unit": "문자열 or null",
    "per_unit": "문자열 or null",
    "properties": {"source_spec": "해당 수량의 규격 (예: 200mm)"}
  }],
  "summary": "1줄 요약 (한국어)",
  "confidence": 0.0~1.0
}
```"""


FEW_SHOT_EXAMPLE = """
## 예시 1: 단일 규격 (기존)

### 입력
섹션: 콘크리트 타설 (레미콘 25-24-15)
텍스트: "1m³당 특별인부 0.33인, 보통인부 0.67인, 콘크리트공 0.15인"

### 출력
{
  "matrix_analysis_scratchpad": "",
  "entities": [
    {"type": "WorkType", "name": "콘크리트 타설", "spec": "레미콘 25-24-15", "unit": "m³", "quantity": null},
    {"type": "Labor", "name": "특별인부", "spec": null, "unit": "인", "quantity": 0.33},
    {"type": "Labor", "name": "보통인부", "spec": null, "unit": "인", "quantity": 0.67},
    {"type": "Labor", "name": "콘크리트공", "spec": null, "unit": "인", "quantity": 0.15}
  ],
  "relationships": [
    {"source": "콘크리트 타설", "target": "특별인부", "relation_type": "REQUIRES_LABOR", "quantity": 0.33, "unit": "인", "per_unit": "1m³당", "properties": {"source_spec": "레미콘 25-24-15"}},
    {"source": "콘크리트 타설", "target": "보통인부", "relation_type": "REQUIRES_LABOR", "quantity": 0.67, "unit": "인", "per_unit": "1m³당", "properties": {"source_spec": "레미콘 25-24-15"}},
    {"source": "콘크리트 타설", "target": "콘크리트공", "relation_type": "REQUIRES_LABOR", "quantity": 0.15, "unit": "인", "per_unit": "1m³당", "properties": {"source_spec": "레미콘 25-24-15"}}
  ],
  "summary": "콘크리트 타설(레미콘 25-24-15) 1m³당 인력투입 기준",
  "confidence": 0.95
}

## 예시 2: 매트릭스 표 전개 (🚨 핵심)

### 입력
섹션: 가스용 폴리에틸렌(PE)관 접합 및 부설

| 구분 | 63mm | 200mm |
| --- | --- | --- |
| 배관공 | 0.184 | 0.521 |
| 특별인부 | 0.052 | 0.113 |

### 출력
{
  "matrix_analysis_scratchpad": "2개 규격(63mm, 200mm) × 2개 직종(배관공, 특별인부) = 4개 관계. 모두 전개.",
  "entities": [
    {"type": "WorkType", "name": "가스용 폴리에틸렌(PE)관 접합 및 부설", "spec": null, "unit": null, "quantity": null},
    {"type": "Labor", "name": "배관공", "spec": null, "unit": "인", "quantity": null},
    {"type": "Labor", "name": "특별인부", "spec": null, "unit": "인", "quantity": null}
  ],
  "relationships": [
    {"source": "가스용 폴리에틸렌(PE)관 접합 및 부설", "target": "배관공", "relation_type": "REQUIRES_LABOR", "quantity": 0.184, "unit": "인", "properties": {"source_spec": "63mm"}},
    {"source": "가스용 폴리에틸렌(PE)관 접합 및 부설", "target": "배관공", "relation_type": "REQUIRES_LABOR", "quantity": 0.521, "unit": "인", "properties": {"source_spec": "200mm"}},
    {"source": "가스용 폴리에틸렌(PE)관 접합 및 부설", "target": "특별인부", "relation_type": "REQUIRES_LABOR", "quantity": 0.052, "unit": "인", "properties": {"source_spec": "63mm"}},
    {"source": "가스용 폴리에틸렌(PE)관 접합 및 부설", "target": "특별인부", "relation_type": "REQUIRES_LABOR", "quantity": 0.113, "unit": "인", "properties": {"source_spec": "200mm"}}
  ],
  "summary": "가스용 PE관 접합 규격별(63mm, 200mm) 인력투입 기준 — 전체 전개",
  "confidence": 0.95
}
"""


def build_user_prompt(chunk: dict, all_chunks: list[dict] = []) -> str:
    """청크 데이터 → LLM 입력 프롬프트 생성"""
    parts = []

    # 섹션 메타데이터
    parts.append(f"## 섹션 정보")
    parts.append(f"- 섹션ID: {chunk.get('section_id', '')}")
    parts.append(f"- 제목: {chunk.get('title', '')}")
    parts.append(f"- 부문: {chunk.get('department', '')}")
    parts.append(f"- 장: {chunk.get('chapter', '')}")
    if chunk.get('unit_basis'):
        parts.append(f"- 기준단위: {chunk['unit_basis']}")

    # 본문 텍스트 (빈 텍스트면 형제 찾아서 주입)
    text = chunk.get("text", "").strip()
    if text:
        parts.append(f"\n## 본문 텍스트\n{text}")
    else:
        # 빈 텍스트일 때 형제 청크에서 컨텍스트 복원
        chunk_id = chunk.get("chunk_id", "")
        match = re.match(r"(C-\d+)", chunk_id)
        if match and all_chunks:
            base_id = match.group(1)
            siblings = [c for c in all_chunks 
                        if c.get("chunk_id", "").startswith(base_id) and c.get("text", "").strip()]
            if siblings:
                sibling_text = siblings[0].get("text", "").strip()
                sibling_id = siblings[0].get("chunk_id", "")
                parts.append(f"\n## 관련 컨텍스트 (동일 섹션 {sibling_id}에서 참조)")
                parts.append(sibling_text)
                parts.append(f"\n⚠️ 위 텍스트는 동일 섹션의 다른 청크에서 가져온 참조 컨텍스트입니다. "
                             f"테이블에 실제로 존재하는 데이터만 추출하세요 (형제의 다른 규격을 함부로 혼용하지 말 것).")

    # 테이블 데이터 → Markdown 형식으로 변환
    tables = chunk.get("tables", [])
    for i, table in enumerate(tables):
        headers = table.get("headers", [])
        rows = table.get("rows", [])
        if not headers:
            continue

        parts.append(f"\n## 테이블 {i+1} (유형: {table.get('type', 'unknown')})")

        # Markdown 테이블 생성
        parts.append("| " + " | ".join(headers) + " |")
        parts.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in rows:
            cells = [str(row.get(h, "")) for h in headers]
            parts.append("| " + " | ".join(cells) + " |")

        # 테이블 내 주석
        notes = table.get("notes_in_table", [])
        if notes:
            parts.append(f"\n테이블 주석: {'; '.join(str(n) for n in notes)}")

    # 청크 주석
    chunk_notes = chunk.get("notes", [])
    if chunk_notes:
        parts.append(f"\n## 주석\n" + "\n".join(str(n) for n in chunk_notes))

    # cross_references
    xrefs = chunk.get("cross_references", [])
    if xrefs:
        parts.append(f"\n## 교차참조")
        for xref in xrefs:
            parts.append(f"- → {xref.get('target_section_id', '')} ({xref.get('context', '')[:50]})")

    parts.append(f"\n## 지시사항")
    parts.append("위 품셈 텍스트와 테이블에서 엔티티(공종, 노무, 장비, 자재, 주석, 기준)와 관계를 추출하세요.")

    return "\n".join(parts)


# ─── LLM 호출 ─────────────────────────────────────────────────

# Why: API 호출이 hang될 경우 무한 대기 방지. 120초 초과 시 타임아웃 에러 발생.
API_TIMEOUT_SECONDS = 120


async def extract_single_chunk(
    chunk: dict,
    semaphore: asyncio.Semaphore,
    all_chunks: list[dict] = [],
) -> ChunkExtraction:
    """단일 청크에 대해 LLM 추출 실행 (비동기, 타임아웃+재시도 포함)"""
    chunk_id = chunk["chunk_id"]
    section_id = chunk["section_id"]

    async with semaphore:
        user_prompt = build_user_prompt(chunk, all_chunks)

        for attempt in range(LLM_RETRY_COUNT):
            try:
                # Why: asyncio.wait_for로 120초 타임아웃 설정.
                #      이전 버전에서 타임아웃 없이 무한 hang 발생 (5시간+)
                api_call = asyncio.to_thread(
                    client.chat.completions.create,
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": FEW_SHOT_EXAMPLE + "\n\n---\n\n" + user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=LLM_TEMPERATURE,
                    max_tokens=LLM_MAX_TOKENS,  # 💡 [Track A] 매트릭스 전개 시 출력 토큰 부족(Truncation) 방지, 16384 확장
                )
                response = await asyncio.wait_for(
                    api_call, timeout=API_TIMEOUT_SECONDS
                )

                # 파싱
                raw_text = response.choices[0].message.content
                llm_result = LLMExtractionResult.model_validate_json(raw_text)

                # LLM 결과 → Phase 2 스키마로 변환
                entities = []
                relationships = []

                base_conf = llm_result.confidence
                if chunk_id in ISOLATED_CHUNKS:
                    base_conf = min(0.7, base_conf)

                for le in llm_result.entities:
                    try:
                        etype = EntityType(le.type)
                    except ValueError:
                        continue  # 잘못된 타입 스킵

                    entity = Entity(
                        type=etype,
                        name=le.name,
                        spec=le.spec,
                        unit=le.unit,
                        quantity=le.quantity,
                        source_chunk_id=chunk_id,
                        source_section_id=section_id,
                        source_method="llm",
                        confidence=base_conf,
                    )
                    entities.append(entity)

                for lr in llm_result.relationships:
                    try:
                        rtype = RelationType(lr.relation_type)
                    except ValueError:
                        continue

                    source_type = _find_entity_type(lr.source, entities)
                    target_type = _find_entity_type(lr.target, entities)

                    rel = Relationship(
                        source=lr.source,
                        source_type=source_type,
                        target=lr.target,
                        target_type=target_type,
                        type=rtype,
                        quantity=lr.quantity,
                        unit=lr.unit,
                        per_unit=lr.per_unit,
                        properties=lr.properties if lr.properties else {},  # 💡 [Track A] source_spec 전달
                        source_chunk_id=chunk_id,
                    )
                    relationships.append(rel)

                return ChunkExtraction(
                    chunk_id=chunk_id,
                    section_id=section_id,
                    department=chunk.get("department", ""),
                    chapter=chunk.get("chapter", ""),
                    title=chunk.get("title", ""),
                    entities=entities,
                    relationships=relationships,
                    summary=llm_result.summary,
                    confidence=base_conf,
                    source_method="llm",
                )

            except asyncio.TimeoutError:
                err_msg = f"API 타임아웃 ({API_TIMEOUT_SECONDS}초, 시도 {attempt+1}/{LLM_RETRY_COUNT})"
                if attempt < LLM_RETRY_COUNT - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return ChunkExtraction(
                        chunk_id=chunk_id, section_id=section_id,
                        department=chunk.get("department", ""),
                        chapter=chunk.get("chapter", ""),
                        title=chunk.get("title", ""),
                        source_method="llm", confidence=0.0,
                        warnings=[err_msg],
                    )
            except Exception as e:
                if attempt < LLM_RETRY_COUNT - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return ChunkExtraction(
                        chunk_id=chunk_id, section_id=section_id,
                        department=chunk.get("department", ""),
                        chapter=chunk.get("chapter", ""),
                        title=chunk.get("title", ""),
                        source_method="llm", confidence=0.0,
                        warnings=[f"LLM 추출 실패 (시도 {LLM_RETRY_COUNT}회): {str(e)[:200]}"],
                    )


def _find_entity_type(name: str, entities: list[Entity]) -> EntityType:
    """엔티티 목록에서 이름으로 타입 찾기"""
    for e in entities:
        if e.name == name:
            return e.type
    return EntityType.WORK_TYPE  # 기본값


# ─── 대상 청크 필터링 ─────────────────────────────────────────

def select_llm_target_chunks(
    chunks: list[dict],
    step1_result: BatchResult | None,
) -> list[dict]:
    """Step 2.1 결과를 참고하여 LLM 추출이 필요한 청크를 선별

    대상:
    1. 테이블이 아예 없는 청크 (텍스트에서 정보 추출)
    2. D_기타/C_구분설명만 있는 청크 (규칙 추출 미대상)
    3. Step 2.1에서 WorkType이 0개인 청크 (보강 필요)
    4. Step 2.1에서 경고가 있는 청크 (인식 불가 헤더)
    """
    # Step 2.1 결과가 없으면 전체 대상
    if step1_result is None:
        return chunks

    # Step 2.1 결과를 chunk_id별로 인덱싱
    step1_map = {e.chunk_id: e for e in step1_result.extractions}

    targets = []
    reasons = Counter()

    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        s1 = step1_map.get(chunk_id)

        # 텍스트가 너무 짧으면 스킵 (섹션 제목만 있는 경우)
        text = chunk.get("text", "")
        tables = chunk.get("tables", [])
        if len(text) < 20 and not tables:
            reasons["텍스트 너무 짧음 (스킵)"] += 1
            continue

        if s1 is None:
            targets.append(chunk)
            reasons["Step 2.1 결과 없음"] += 1
            continue

        # 조건 1: 테이블 없는 텍스트 청크
        if not tables and len(text) > 30:
            targets.append(chunk)
            reasons["테이블 없음 (텍스트 추출)"] += 1
            continue

        # 조건 2: D_기타/C_구분설명만 있는 테이블
        # 단, step1에서 이미 WorkType을 추출한 청크는 제외 (매트릭스 추출 성공)
        table_types = {t.get("type", "") for t in tables}
        if table_types <= {"D_기타", "C_구분설명"}:
            has_worktype = any(e.type == EntityType.WORK_TYPE for e in s1.entities)
            if not has_worktype:
                targets.append(chunk)
                reasons["D_기타/C_구분설명 테이블만 (WorkType 없음)"] += 1
                continue

        # 조건 3: WorkType 추출 안 됨
        has_worktype = any(e.type == EntityType.WORK_TYPE for e in s1.entities)
        if not has_worktype and s1.entities:
            targets.append(chunk)
            reasons["WorkType 미추출"] += 1
            continue

        # 조건 4: 경고 존재 (인식 불가 헤더 등)
        meaningful_warnings = [w for w in s1.warnings if "테이블 없음" not in w]
        if meaningful_warnings:
            targets.append(chunk)
            reasons["Step 2.1 경고 있음"] += 1
            continue

    return targets, reasons


# ─── 메인 실행 ────────────────────────────────────────────────

# ─── 중간 저장 유틸 ────────────────────────────────────────────

PARTIAL_SAVE_FILE = PHASE2_OUTPUT / "llm_entities_partial.json"
SAVE_INTERVAL = 200  # 200건마다 중간 저장


def save_partial_result(result: BatchResult):
    """중간 결과 저장. crash 시에도 진행 상태 보존."""
    PHASE2_OUTPUT.mkdir(parents=True, exist_ok=True)
    PARTIAL_SAVE_FILE.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )


def load_existing_chunk_ids() -> set[str]:
    """이어하기(resume): 기존 결과에서 이미 처리된 chunk_id 로드"""
    done_ids = set()
    for path in [LLM_ENTITIES_FILE, PARTIAL_SAVE_FILE]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for ext in data.get("extractions", []):
                    done_ids.add(ext["chunk_id"])
            except Exception:
                pass
    return done_ids


def load_existing_extractions() -> list[ChunkExtraction]:
    """이어하기: 기존 결과에서 extractions 로드"""
    existing = []
    for path in [LLM_ENTITIES_FILE, PARTIAL_SAVE_FILE]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for ext_data in data.get("extractions", []):
                    existing.append(ChunkExtraction.model_validate(ext_data))
            except Exception:
                pass
    # chunk_id 기준으로 중복 제거 (나중 것 우선)
    seen = {}
    for ext in existing:
        seen[ext.chunk_id] = ext
    return list(seen.values())


async def run_step2_async(sample: bool = False, resume: bool = False, section_filter: str = None) -> BatchResult:
    """Step 2.2 비동기 실행

    Args:
        sample: True면 20개만 처리
        resume: True면 기존 결과에서 이어서 처리
        section_filter: 지정된 문자열이 section_id 에 포함된 청크만 추출
    """
    print("\n  Step 2.2: LLM 기반 엔티티/관계 추출 (DeepSeek-V3)")
    print("  " + "=" * 55)
    print(f"  타임아웃: {API_TIMEOUT_SECONDS}초/요청, 동시성: {LLM_CONCURRENCY}")

    # 데이터 로드
    data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = data["chunks"]

    # Step 2.1 결과 로드
    step1_result = None
    if TABLE_ENTITIES_FILE.exists():
        step1_data = json.loads(TABLE_ENTITIES_FILE.read_text(encoding="utf-8"))
        step1_result = BatchResult.model_validate(step1_data)
        print(f"  Step 2.1 결과 로드: {step1_result.total_entities} 엔티티")

    # 대상 청크 선별
    if section_filter:
        targets = [c for c in chunks if section_filter in c.get("section_id", "") or section_filter in c.get("subsection", "") or section_filter in c.get("title", "")]
        reasons = Counter({"섹션 필터 강제 지정": len(targets)})
        print(f"  [섹션 필터] '{section_filter}' 적용: 강제 추출 대상 {len(targets)}개 청크 발견")
    else:
        targets, reasons = select_llm_target_chunks(chunks, step1_result)

    # 이어하기: 기존 결과 로드
    existing_extractions = []
    if resume and not section_filter:  # 섹션 지정 테스트 시에는 이어하기 스킵
        done_ids = load_existing_chunk_ids()
        existing_extractions = load_existing_extractions()
        before = len(targets)
        targets = [c for c in targets if c["chunk_id"] not in done_ids]
        print(f"  [이어하기] 기존 {len(done_ids)}건 스킵, 잔여 {len(targets)}/{before}건")

    if sample and not section_filter:
        targets = targets[:20]
        print(f"  [샘플 모드] {len(targets)}개 청크만 처리")

    print(f"\n  LLM 추출 대상: {len(targets)}개 청크")
    for reason, cnt in reasons.most_common():
        print(f"    {reason}: {cnt}")

    if not targets:
        print("  추출 대상 없음. 종료.")
        if existing_extractions:
            # 기존 결과만으로 최종 파일 생성
            result = BatchResult(total_chunks=len(existing_extractions))
            result.extractions = existing_extractions
            result.processed_chunks = len(existing_extractions)
            _finalize_result(result)
            return result
        return BatchResult()

    # 비동기 추출 실행
    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    result = BatchResult(total_chunks=len(targets) + len(existing_extractions))
    result.extractions = list(existing_extractions)  # 기존 결과 포함
    result.processed_chunks = len(existing_extractions)

    print(f"\n  처리 시작 (동시 {LLM_CONCURRENCY}개, 타임아웃 {API_TIMEOUT_SECONDS}초)...")
    sys.stdout.flush()
    start_time = time.time()

    tasks = [extract_single_chunk(c, semaphore, chunks) for c in targets]

    # 진행률 표시 + 중간 저장
    completed = 0
    for coro in asyncio.as_completed(tasks):
        extraction = await coro
        result.extractions.append(extraction)
        result.processed_chunks += 1
        completed += 1

        if completed % 50 == 0 or completed == len(tasks):
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            remaining = (len(tasks) - completed) / rate if rate > 0 else 0
            print(f"    [{completed:4d}/{len(tasks)}] "
                  f"{elapsed:.0f}초 ({rate:.1f}건/초) "
                  f"잔여 ~{remaining:.0f}초")
            sys.stdout.flush()

        # Why: 200건마다 중간 저장 → 프로세스 crash 시에도 결과 보존
        if completed % SAVE_INTERVAL == 0:
            save_partial_result(result)

    elapsed = time.time() - start_time

    # 최종 통계 & 저장
    _finalize_result(result)

    print(f"\n  완료 ({elapsed:.0f}초 소요, {elapsed/60:.1f}분)")
    return result


def _finalize_result(result: BatchResult):
    """통계 집계 + 최종 파일 저장"""
    entity_type_counter = Counter()
    rel_type_counter = Counter()
    failed_count = 0

    for ext in result.extractions:
        if ext.confidence == 0.0:
            failed_count += 1
            result.failed.append({
                "chunk_id": ext.chunk_id,
                "warnings": ext.warnings,
            })
        for e in ext.entities:
            entity_type_counter[e.type.value] += 1
        for r in ext.relationships:
            rel_type_counter[r.type.value] += 1

    result.total_entities = sum(entity_type_counter.values())
    result.total_relationships = sum(rel_type_counter.values())
    result.entity_type_counts = dict(entity_type_counter)
    result.relationship_type_counts = dict(rel_type_counter)

    # 최종 저장
    PHASE2_OUTPUT.mkdir(parents=True, exist_ok=True)
    LLM_ENTITIES_FILE.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    # 리포트 출력
    print(f"\n  결과:")
    print(f"    처리 청크: {result.processed_chunks}/{result.total_chunks}")
    print(f"    성공: {result.processed_chunks - failed_count} / 실패: {failed_count}")
    print(f"    총 엔티티: {result.total_entities}")
    for etype, cnt in sorted(entity_type_counter.items(), key=lambda x: -x[1]):
        print(f"      {etype}: {cnt}")
    print(f"    총 관계: {result.total_relationships}")
    for rtype, cnt in sorted(rel_type_counter.items(), key=lambda x: -x[1]):
        print(f"      {rtype}: {cnt}")

    print(f"\n  저장: {LLM_ENTITIES_FILE}")

    # partial 파일 정리
    if PARTIAL_SAVE_FILE.exists():
        PARTIAL_SAVE_FILE.unlink()


def run_step2(sample: bool = False, resume: bool = False, section_filter: str = None) -> BatchResult:
    """동기 래퍼"""
    return asyncio.run(run_step2_async(sample, resume, section_filter))


if __name__ == "__main__":
    sample_mode = "--sample" in sys.argv
    resume_mode = "--resume" in sys.argv
    
    section_arg = None
    if "--section" in sys.argv:
        idx = sys.argv.index("--section")
        section_arg = sys.argv[idx + 1]
        
    run_step2(sample=sample_mode, resume=resume_mode, section_filter=section_arg)
