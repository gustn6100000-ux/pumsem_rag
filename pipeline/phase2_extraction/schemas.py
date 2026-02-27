# -*- coding: utf-8 -*-
"""Phase 2 Pydantic 스키마: 엔티티 & 관계 데이터 모델

graph-rag-builder 스킬의 온톨로지 + llm-structured-extraction 스킬의 패턴을
결합하여, 품셈 도메인에 최적화된 스키마를 정의한다.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ─── 엔티티 타입 열거형 ───────────────────────────────────────
class EntityType(str, Enum):
    """그래프 노드 유형 (graph-rag-builder 온톨로지 기반)"""
    WORK_TYPE = "WorkType"        # 공종/작업 (콘크리트 타설, 철근 가공 등)
    LABOR = "Labor"               # 노무/인력 (특별인부, 보통인부, 철근공 등)
    EQUIPMENT = "Equipment"       # 장비/기계 (굴착기, 크레인, 레미콘 등)
    MATERIAL = "Material"         # 자재/재료 (시멘트, 골재, 철근 등)
    SECTION = "Section"           # 품셈 섹션 (장-절-항 구조)
    NOTE = "Note"                 # 주석/조건/할증
    STANDARD = "Standard"         # 적용 기준/규격 (KCS, KDS 등)


class RelationType(str, Enum):
    """그래프 엣지 유형"""
    REQUIRES_LABOR = "REQUIRES_LABOR"          # 공종 → 노무
    REQUIRES_EQUIPMENT = "REQUIRES_EQUIPMENT"  # 공종 → 장비
    USES_MATERIAL = "USES_MATERIAL"            # 공종 → 자재
    BELONGS_TO = "BELONGS_TO"                  # 공종 → 섹션
    HAS_NOTE = "HAS_NOTE"                      # 섹션/공종 → 주석
    REFERENCES = "REFERENCES"                  # 섹션 → 섹션
    APPLIES_STANDARD = "APPLIES_STANDARD"      # 공종 → 기준
    HAS_CHILD = "HAS_CHILD"                    # 섹션 → 하위섹션


# ─── 엔티티 모델 ──────────────────────────────────────────────
class Entity(BaseModel):
    """추출된 엔티티 (그래프 노드)"""
    type: EntityType
    name: str = Field(description="엔티티 이름 (예: 콘크리트 타설, 특별인부)")
    normalized_name: str = Field(default="", description="정규화된 이름 (공백 제거 등)")
    code: Optional[str] = Field(None, description="품셈 코드 또는 섹션 ID")
    spec: Optional[str] = Field(None, description="규격/사양 (예: 0.6m³, D13)")
    unit: Optional[str] = Field(None, description="단위 (예: 인, m³, 대)")
    quantity: Optional[float] = Field(None, description="수량")
    properties: dict = Field(default_factory=dict, description="추가 속성")
    # Why: sub_section을 명시적 필드로 승격하여 properties dict의 키 분화 위험 방지
    #       프론트엔드 트리 필터링(재질→접합→관경)의 검색 단위(Facet)로 활용
    sub_section: Optional[str] = Field(None, description="소제목 분류 (예: 1. 전기아크용접(V형))")
    sub_section_no: Optional[str] = Field(None, description="소제목 번호 (예: 01)")
    confidence: float = Field(default=1.0, ge=0, le=1, description="추출 신뢰도")
    # 출처 추적
    source_chunk_id: str = Field(default="", description="추출 원본 청크 ID")
    source_section_id: str = Field(default="", description="추출 원본 섹션 ID")
    source_method: str = Field(default="", description="추출 방법 (table_rule|llm)")

    def model_post_init(self, __context) -> None:
        """normalized_name 자동 생성"""
        if not self.normalized_name:
            # Why: 품셈 원본에서 공백이 불규칙하게 삽입되어 있어 정규화 필요
            self.normalized_name = self.name.replace(" ", "").strip()


class Relationship(BaseModel):
    """엔티티 간 관계 (그래프 엣지)"""
    source: str = Field(description="출발 엔티티 이름")
    source_type: EntityType = Field(description="출발 엔티티 타입")
    target: str = Field(description="도착 엔티티 이름")
    target_type: EntityType = Field(description="도착 엔티티 타입")
    type: RelationType = Field(description="관계 유형")
    quantity: Optional[float] = Field(None, description="투입 수량")
    unit: Optional[str] = Field(None, description="투입 단위")
    per_unit: Optional[str] = Field(None, description="기준 단위 (예: 1m³당)")
    properties: dict = Field(default_factory=dict, description="추가 속성")
    # 출처 추적
    source_chunk_id: str = Field(default="", description="추출 원본 청크 ID")


# ─── 청크 추출 결과 모델 ──────────────────────────────────────
class ChunkExtraction(BaseModel):
    """단일 청크에서 추출된 전체 결과

    llm-structured-extraction 스킬의 ExtractionResult 패턴을 따른다.
    """
    chunk_id: str
    section_id: str
    department: str = ""
    chapter: str = ""
    title: str = ""
    entities: list[Entity] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)
    summary: str = Field(default="", description="청크 내용 1줄 요약")
    confidence: float = Field(default=1.0, ge=0, le=1, description="전체 추출 신뢰도")
    source_method: str = Field(default="", description="table_rule | llm | merged")
    warnings: list[str] = Field(default_factory=list, description="추출 시 경고 목록")


# ─── 배치 결과 모델 ──────────────────────────────────────────
class BatchResult(BaseModel):
    """전체 추출 결과 (파일 저장용)"""
    total_chunks: int = 0
    processed_chunks: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    entity_type_counts: dict[str, int] = Field(default_factory=dict)
    relationship_type_counts: dict[str, int] = Field(default_factory=dict)
    extractions: list[ChunkExtraction] = Field(default_factory=list)
    failed: list[dict] = Field(default_factory=list, description="실패 건 목록")
