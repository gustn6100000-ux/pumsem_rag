# -*- coding: utf-8 -*-
"""Step 2.1: 테이블 기반 규칙 추출

A_품셈 테이블의 구조화된 headers/rows에서 규칙 기반으로
WorkType, Labor, Equipment, Material 엔티티와 관계를 추출한다.

graph-rag-builder 스킬: 온톨로지 + 추출 검증 패턴 적용
llm-structured-extraction 스킬: Pydantic 스키마 + 품질 검증 적용

추출 전략:
  A_품셈 테이블 → 헤더 패턴 매칭으로 열 역할 분류 → 행 순회하며 엔티티 생성
  B_규모기준 테이블 → 조건 엔티티(Note)로 변환
  D_기타 (매트릭스) → Case D: 구경×SCH 2차원 테이블에서 규칙 추출
  C_구분설명, D_기타(비매트릭스) → Step 2.2(LLM)에서 처리
"""
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from config import (
    CHUNKS_FILE, PHASE2_OUTPUT, TABLE_ENTITIES_FILE,
    HEADER_ENTITY_MAPPING, WORKTYPE_HEADER_KEYWORDS,
    SPEC_HEADER_KEYWORDS, UNIT_HEADER_KEYWORDS, QUANTITY_HEADER_KEYWORDS,
    UNIT_EXCLUDE_KEYWORDS,
    LABOR_NORMALIZE_MAP,
)
from schemas import (
    Entity, Relationship, ChunkExtraction, BatchResult,
    EntityType, RelationType,
)

sys.stdout.reconfigure(encoding="utf-8")


# ─── 헤더 역할 분류 ──────────────────────────────────────────

def classify_header_role(header: str) -> str:
    """헤더 문자열 → 역할 분류

    Returns:
        "name"     : 명칭/공종명/종목 (엔티티 이름)
        "spec"     : 규격/사양
        "unit"     : 단위
        "quantity" : 수량
        "labor"    : 노무 열 (값이 인력 수량)
        "equipment": 장비 열
        "material" : 자재 열
        "note"     : 비고/비 고
        "other"    : 기타
    """
    h = header.replace(" ", "")

    # Why: 품셈 테이블 헤더는 공백이 불규칙하므로 공백 제거 후 매칭
    for kw in WORKTYPE_HEADER_KEYWORDS:
        if kw.replace(" ", "") in h:
            return "name"

    for kw in SPEC_HEADER_KEYWORDS:
        if kw.replace(" ", "") in h:
            return "spec"

    for kw in UNIT_HEADER_KEYWORDS:
        if kw.replace(" ", "") in h:
            # Why: "규 격_소수자리" → 단위가 아니라 정밀도 정보이므로 제외
            if any(exc.replace(" ", "") in h for exc in UNIT_EXCLUDE_KEYWORDS):
                return "other"
            return "unit"

    for kw in QUANTITY_HEADER_KEYWORDS:
        if kw.replace(" ", "") in h:
            return "quantity"

    # 노무/장비/자재 판별: composite 헤더("인 원 수_1일당_특별인부")도 포함
    for kw in HEADER_ENTITY_MAPPING["labor_keywords"]:
        if kw.replace(" ", "") in h:
            return "labor"

    for kw in HEADER_ENTITY_MAPPING["equipment_keywords"]:
        if kw.replace(" ", "") in h:
            return "equipment"

    for kw in HEADER_ENTITY_MAPPING["material_keywords"]:
        if kw.replace(" ", "") in h:
            return "material"

    if "비고" in h or "비 고" in header:
        return "note"

    return "other"


def classify_headers(headers: list[str]) -> dict[str, list[tuple[int, str]]]:
    """전체 헤더 목록 → {역할: [(인덱스, 원본헤더), ...]} 매핑

    Why: 한 테이블에 노무 열이 여러 개 있을 수 있음 (특별인부, 보통인부 등)
    """
    result: dict[str, list[tuple[int, str]]] = {}
    for i, h in enumerate(headers):
        role = classify_header_role(h)
        result.setdefault(role, []).append((i, h))
    return result


# ─── 셀 값 파싱 ──────────────────────────────────────────────

def parse_cell_value(val) -> tuple[float | None, str]:
    """셀 값 → (수치, 원본문자열) 변환

    빈 값, "-", 문자열 등 비수치 → (None, "")
    """
    if val is None or val == "" or val == "-":
        return None, str(val) if val else ""

    if isinstance(val, (int, float)):
        return float(val), str(val)

    s = str(val).strip()

    # "(0.01)" 등 괄호 숫자 → 숫자로 변환 (괄호 의미 보존은 properties로)
    paren_match = re.match(r'^\(([0-9.,]+)\)$', s)
    if paren_match:
        try:
            num = float(paren_match.group(1).replace(",", ""))
            return num, s
        except ValueError:
            pass

    # 일반 숫자 (콤마 포함)
    num_match = re.match(r'^[0-9.,]+$', s.replace(",", ""))
    if num_match:
        try:
            num = float(s.replace(",", ""))
            return num, s
        except ValueError:
            pass

    return None, s


def normalize_entity_name(name: str) -> str:
    """엔티티 이름 정규화

    Why: 품셈 원본에서 "보 통 인 부" → "보통인부" 등 불필요한 공백 제거
    또한 "〃" (반복 부호) 같은 특수 케이스 처리
    Phase 4C-1(e): 숫자+단위 공백 정규화 추가, 연속 공백 제거 추가
    """
    # 기존 매핑 테이블 우선
    stripped = name.strip()
    if stripped in LABOR_NORMALIZE_MAP:
        return LABOR_NORMALIZE_MAP[stripped]

    # 한글 사이 단일 공백 제거: "보 통 인 부" → "보통인부"
    # 단, "콘크리트 타설" 같은 의미 있는 공백은 유지
    result = re.sub(r'(?<=[\uAC00-\uD7AF])\s(?=[\uAC00-\uD7AF])', '', stripped)

    # 숫자와 단위 사이 공백 정규화: "42 kg/cm2" → "42kg/cm2"
    # Why: 품셈 원본에서 단위 앞 공백이 불일치하여 동일 엔티티가 중복 생성됨
    result = re.sub(r'(\d+)\s+(kg|mm|cm|m|t|inch|℃|°)', r'\1\2', result)

    # 연속 공백 → 단일 공백
    result = re.sub(r'\s{2,}', ' ', result)

    return result


def extract_labor_name_from_header(header: str) -> str:
    """composite 헤더에서 노무 명칭 추출

    예: "인 원 수_1일당_지적 기사" → "지적기사"
        "인 원 수_합계_보통인부" → "보통인부"
        "특별인부" → "특별인부"
    """
    # composite 헤더: "_"로 분리된 마지막 부분이 실제 인력명
    if "_" in header:
        parts = header.split("_")
        # "합계" 포함 헤더는 수량 합산용이므로 스킵 대상
        if any("합계" in p.replace(" ", "") for p in parts):
            return ""
        # 마지막 파트가 인력명
        name = parts[-1].strip()
    else:
        name = header.strip()

    return normalize_entity_name(name)


# ─── SCH 화이트리스트 & 열 라벨 결정 (Phase 4C-1a) ────────────

# 정상 SCH 사용 섹션: 원본 PDF에서 열 헤더가 실제 배관 Schedule을 의미하는 섹션
_SCH_WHITELIST_SECTIONS = frozenset([
    '13-2-3',   # 강관용접
    '13-1-1',   # 플랜트 배관 설치
    '13-1-2',   # 관만곡 설치
    '13-2-1',   # 강관절단
])

_SCH_WHITELIST_KEYWORDS = frozenset([
    '강관용접', '배관설치', '배관 설치', '관만곡', '강관절단',
])


def _determine_column_label(
    column_header: str,
    section_id: str,
    section_title: str,
) -> str:
    """매트릭스 테이블 열 헤더의 실제 의미를 결정한다.

    Why: 매트릭스 테이블의 숫자 열 헤더는 섹션마다 의미가 다르다.
    - 강관/배관 섹션 → SCH (Schedule Number)  
    - 기타 섹션 → 그 숫자 자체가 의미를 가짐 (두께, 인원수, ID 등)

    설계: 화이트리스트 기반. SCH 정상 4개 섹션만 명시적으로 허용.
    나머지는 원본 열 헤더 값을 그대로 사용.
    """
    is_numeric = bool(re.match(r'^\d+$', str(column_header).strip()))

    # Case 1: 화이트리스트 섹션 → SCH 라벨
    if section_id in _SCH_WHITELIST_SECTIONS:
        if is_numeric:
            return f'SCH {column_header}'
        return str(column_header)

    # Case 2: 키워드 기반 보조 판별 (화이트리스트 누락 방어)
    title_normalized = section_title.replace(' ', '')
    if any(kw in title_normalized for kw in _SCH_WHITELIST_KEYWORDS):
        if is_numeric:
            return f'SCH {column_header}'
        return str(column_header)

    # Case 3: 비화이트리스트 → 원본 값 그대로 (SCH 붙이지 않음)
    return str(column_header)


# ─── 매트릭스 테이블 탐지 및 추출 (Case D) ─────────────────────

def is_matrix_table(headers: list, rows: list) -> bool:
    """D_기타 테이블이 매트릭스(구경×SCH) 패턴인지 판별

    2가지 패턴:
    D1: 헤더가 숫자(SCH), row[0]에 직종명 포함
    D2: 헤더가 복합(SCH_직종명) 형식
    """
    if len(headers) < 3 or not rows:
        return False

    # D2 패턴: 복합 헤더 (예: '20_플랜트 용접공 (인)')
    compound_count = sum(
        1 for h in headers[1:]
        if '_' in h and any(kw in h for kw in [
            '용접공', '인부', '용접봉', '배관공', '철근공', '비계공',
            '기사', '기능사', '기능공', '기술자', '전공',
        ])
    )
    if compound_count >= 2:
        return True

    # D1 패턴: 숫자 헤더 + 메타행
    numeric_headers = sum(
        1 for h in headers[1:]
        if re.match(r'^\d+$', str(h).strip())
    )
    if numeric_headers >= 3:
        # row[0]의 값에 직종 키워드가 있는지 확인
        if rows:
            first_row_vals = ' '.join(str(v) for v in rows[0].values())
            job_kws = ['용접공', '인부', '배관공', '용접봉', '기사', '기능사']
            if any(kw in first_row_vals for kw in job_kws):
                return True
            # 숫자 헤더만으로도 매트릭스 후보 (데이터 검증)
            data_rows_with_numbers = 0
            for row in rows[1:5]:  # 샘플 체크
                for h in headers[1:]:
                    val = str(row.get(h, '')).strip()
                    if re.match(r'^\d+\.?\d*$', val):
                        data_rows_with_numbers += 1
                        break
            if data_rows_with_numbers >= 2:
                return True

    return False


def extract_from_matrix_table(
    table: dict,
    chunk_id: str,
    section_id: str,
    section_title: str,
) -> tuple[list[Entity], list[Relationship], list[str]]:
    """매트릭스(구경×SCH) 테이블에서 엔티티/관계 추출

    D1패턴 (메타행):
      헤더: [SCH No., 20, 30, 40, ...]
      row[0]: {SCH No.: '직종 구경', 20: '용접공', 40: '플랜트 용접공', ...}
      row[1+]: {SCH No.: 'φ 15', 40: 0.066, 80: 0.075, ...}

    D2패턴 (복합 헤더):
      헤더: [SCH No._직종, 20_플랜트 용접공 (인), 20_특별인부 (인), ...]
      row[n]: {SCH No._직종: 200, 20_플랜트 용접공 (인): 0.244, ...}
    """
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    warnings: list[str] = []

    headers = table.get('headers', [])
    rows = table.get('rows', [])
    if not headers or not rows:
        return entities, relationships, warnings

    # ── D2 패턴 감지: 복합 헤더 ──
    compound_headers = [
        h for h in headers[1:]
        if '_' in h and any(kw in h for kw in [
            '용접공', '인부', '용접봉', '배관공', '철근공', '비계공',
            '기사', '기능사', '기능공', '기술자', '전공',
        ])
    ]

    if compound_headers:
        return _extract_d2_compound(table, chunk_id, section_id, section_title)
    else:
        return _extract_d1_metarow(table, chunk_id, section_id, section_title)


def _extract_d1_metarow(
    table: dict,
    chunk_id: str,
    section_id: str,
    section_title: str,
) -> tuple[list[Entity], list[Relationship], list[str]]:
    """D1: 메타행(row[0])에서 직종 매핑, row[1+]에서 데이터 추출"""
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    warnings: list[str] = []

    headers = table.get('headers', [])
    rows = table.get('rows', [])
    first_header = headers[0]
    sch_headers = [h for h in headers[1:]]  # SCH 번호 or 숫자 열

    # row[0] = 메타행 (직종/자재 매핑)
    meta_row = rows[0]
    data_rows = rows[1:]

    # 메타행에서 각 SCH 열의 직종/자재 이름 추출
    sch_to_entity_name: dict[str, str] = {}
    for sch_h in sch_headers:
        meta_val = str(meta_row.get(sch_h, '')).strip()
        if meta_val:
            sch_to_entity_name[sch_h] = normalize_entity_name(meta_val)

    # 메타행 값이 없거나 모두 같으면 → 자재명일 수 있음
    # 직종인지 자재인지 판별
    sample_names = list(set(sch_to_entity_name.values()))
    is_material = False
    if sample_names:
        # '용접봉', '산소' 등 자재 키워드 포함?
        material_kws = ['용접봉', '산소', 'LPG', '아세틸렌', '전극봉', '가스']
        if all(any(mk in n for mk in material_kws) for n in sample_names):
            is_material = True

    # 데이터행 순회
    for row in data_rows:
        # 첫 열 = 구경 (φ 15, φ 200 등) 또는 기타 식별자
        pipe_size_raw = str(row.get(first_header, '')).strip()
        if not pipe_size_raw or pipe_size_raw in ('합계', '계', '소계'):
            continue

        # 구경 → spec 값
        pipe_size = normalize_entity_name(pipe_size_raw)

        for sch_h in sch_headers:
            entity_name = sch_to_entity_name.get(sch_h, '')
            if not entity_name:
                continue

            qty_val, raw_val = parse_cell_value(row.get(sch_h))
            if qty_val is None or qty_val == 0:
                continue

            # WorkType = 섹션 제목 + 규격
            # Phase 4C-1(a): SCH 조건부 적용 — 화이트리스트 기반
            col_label = _determine_column_label(sch_h, section_id, section_title)
            wt_name = f"{section_title} ({pipe_size}, {col_label})"

            wt_entity = Entity(
                type=EntityType.WORK_TYPE,
                name=wt_name,
                spec=f"{pipe_size} {col_label}",
                source_chunk_id=chunk_id,
                source_section_id=section_id,
                source_method="table_rule",
                confidence=1.0,
            )
            entities.append(wt_entity)

            if is_material:
                # 자재 엔티티
                mat_entity = Entity(
                    type=EntityType.MATERIAL,
                    name=entity_name,
                    unit='kg' if 'kg' in entity_name.lower() else '',
                    quantity=qty_val,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                    confidence=1.0,
                )
                entities.append(mat_entity)
                rel = Relationship(
                    source=wt_name,
                    source_type=EntityType.WORK_TYPE,
                    target=entity_name,
                    target_type=EntityType.MATERIAL,
                    type=RelationType.USES_MATERIAL,
                    quantity=qty_val,
                    source_chunk_id=chunk_id,
                )
                relationships.append(rel)
            else:
                # 노무 엔티티
                labor_entity = Entity(
                    type=EntityType.LABOR,
                    name=entity_name,
                    unit='인',
                    quantity=qty_val,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                    confidence=1.0,
                )
                entities.append(labor_entity)
                rel = Relationship(
                    source=wt_name,
                    source_type=EntityType.WORK_TYPE,
                    target=entity_name,
                    target_type=EntityType.LABOR,
                    type=RelationType.REQUIRES_LABOR,
                    quantity=qty_val,
                    unit='인',
                    source_chunk_id=chunk_id,
                )
                relationships.append(rel)

    return entities, relationships, warnings


def _extract_d2_compound(
    table: dict,
    chunk_id: str,
    section_id: str,
    section_title: str,
) -> tuple[list[Entity], list[Relationship], list[str]]:
    """D2: 복합 헤더(SCH_직종명) 패턴 추출

    헤더 예: '20_플랜트 용접공 (인)', '20_특별인부 (인)'
    → SCH=20, 직종=플랜트 용접공, 단위=인
    """
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    warnings: list[str] = []

    headers = table.get('headers', [])
    rows = table.get('rows', [])
    first_header = headers[0]

    # 복합 헤더 파싱: 'SCH_직종명 (단위)' → (sch, job_name, unit)
    parsed_headers: list[tuple[str, str, str, str]] = []  # (header, sch, job_name, unit)
    for h in headers[1:]:
        parts = h.split('_', 1)
        if len(parts) < 2:
            continue
        sch_part = parts[0].strip()
        job_part = parts[1].strip()

        # 단위 추출: '플랜트 용접공 (인)' → job='플랜트 용접공', unit='인'
        unit_match = re.search(r'[\(（]([^\)）]+)[\)）]', job_part)
        unit = unit_match.group(1) if unit_match else '인'
        job_name = re.sub(r'[\(（][^\)）]+[\)）]', '', job_part).strip()
        job_name = job_name.rstrip('_').strip()
        job_name = normalize_entity_name(job_name)

        if job_name:
            parsed_headers.append((h, sch_part, job_name, unit))

    # 행 순회
    for row in rows:
        # 첫 열 = 구경 (mm, φ값)
        pipe_raw = row.get(first_header, '')
        pipe_str = str(pipe_raw).strip()
        if not pipe_str or pipe_str in ('합계', '계', '소계', '구경 mm', '구경mm'):
            continue

        # 숫자면 mm 값, 문자면 그대로 (φ/ø 및 소수점 처리)
        pipe_clean = re.sub(r'^[φΦø∅ɸ]\s*', '', pipe_str)
        try:
            # Avoid omitting floating points, but remove .0
            pipe_num = float(pipe_clean)
            if pipe_num.is_integer():
                pipe_label = f'φ{int(pipe_num)}'
            else:
                pipe_label = f'φ{pipe_num}'
        except (ValueError, TypeError):
            pipe_label = normalize_entity_name(pipe_str)

        for orig_header, sch, job_name, unit in parsed_headers:
            qty_val, _ = parse_cell_value(row.get(orig_header))
            if qty_val is None or qty_val == 0:
                continue

            # Phase 4C-1(a): SCH 조건부 적용 — 화이트리스트 기반
            col_label = _determine_column_label(sch, section_id, section_title)
            wt_name = f"{section_title} ({pipe_label}, {col_label})"

            wt_entity = Entity(
                type=EntityType.WORK_TYPE,
                name=wt_name,
                spec=f"{pipe_label} {col_label}",
                source_chunk_id=chunk_id,
                source_section_id=section_id,
                source_method="table_rule",
                confidence=1.0,
            )
            entities.append(wt_entity)

            # 자재 vs 노무 판별
            material_kws = ['용접봉', '산소', 'LPG', '아세틸렌', '전극봉', '가스']
            if any(mk in job_name for mk in material_kws):
                mat_entity = Entity(
                    type=EntityType.MATERIAL,
                    name=job_name,
                    unit=unit,
                    quantity=qty_val,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                    confidence=1.0,
                )
                entities.append(mat_entity)
                rel = Relationship(
                    source=wt_name,
                    source_type=EntityType.WORK_TYPE,
                    target=job_name,
                    target_type=EntityType.MATERIAL,
                    type=RelationType.USES_MATERIAL,
                    quantity=qty_val,
                    unit=unit,
                    source_chunk_id=chunk_id,
                )
                relationships.append(rel)
            else:
                labor_entity = Entity(
                    type=EntityType.LABOR,
                    name=job_name,
                    unit=unit,
                    quantity=qty_val,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                    confidence=1.0,
                )
                entities.append(labor_entity)
                rel = Relationship(
                    source=wt_name,
                    source_type=EntityType.WORK_TYPE,
                    target=job_name,
                    target_type=EntityType.LABOR,
                    type=RelationType.REQUIRES_LABOR,
                    quantity=qty_val,
                    unit=unit,
                    source_chunk_id=chunk_id,
                )
                relationships.append(rel)

    return entities, relationships, warnings


# ─── 테이블 추출 메인 로직 ────────────────────────────────────

def extract_from_a_table(
    table: dict,
    chunk_id: str,
    section_id: str,
    section_title: str,
) -> tuple[list[Entity], list[Relationship], list[str]]:
    """A_품셈 테이블에서 규칙 기반 엔티티/관계 추출

    전략:
    1. 헤더를 역할별로 분류 (name, spec, unit, labor, equipment, ...)
    2. name 열이 있으면 → 각 행의 name 값이 WorkType/Material
    3. labor/equipment 열이 있으면 → 열 자체가 엔티티, 행의 값이 수량
    4. 행 순회하며 엔티티 + 관계 생성
    """
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    warnings: list[str] = []

    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not headers or not rows:
        return entities, relationships, warnings

    role_map = classify_headers(headers)

    # Case A: 헤더에 labor/equipment 열이 있는 품셈 테이블
    # 전형적 패턴: [명칭 | 규격 | 단위 | 수량 | 특별인부 | 보통인부 | ...]
    labor_cols = role_map.get("labor", [])
    equip_cols = role_map.get("equipment", [])
    name_cols = role_map.get("name", [])

    # 노무/장비 열이 있고, 행에 수량 값이 있는 패턴
    if labor_cols or equip_cols:
        # 섹션 전체의 공종명 = section_title (행 순회에서 재정의될 수 있음)
        work_type_name = section_title

        # name 열이 있으면 행별 공종명 사용
        for row in rows:
            # 합계 행 스킵
            row_values = list(row.values())
            first_val = str(row_values[0]).replace(" ", "") if row_values else ""
            if first_val in ("합계", "계", "소계", "총계"):
                continue

            # 공종명 결정
            if name_cols:
                _, name_header = name_cols[0]
                raw_name = str(row.get(name_header, "")).strip()
                if raw_name and raw_name != "〃" and raw_name != "-":
                    work_type_name = normalize_entity_name(raw_name)

            # 규격/단위 추출
            spec_val = None
            unit_val = None
            for _, h in role_map.get("spec", []):
                sv = str(row.get(h, "")).strip()
                if sv and sv != "-":
                    spec_val = sv
            for _, h in role_map.get("unit", []):
                uv = str(row.get(h, "")).strip()
                if uv and uv != "-":
                    unit_val = uv

            # WorkType 엔티티 (중복은 나중에 정규화)
            if work_type_name:
                wt_entity = Entity(
                    type=EntityType.WORK_TYPE,
                    name=work_type_name,
                    spec=spec_val,
                    unit=unit_val,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                )
                entities.append(wt_entity)

            # 노무 엔티티 + 관계
            for _, labor_header in labor_cols:
                labor_name = extract_labor_name_from_header(labor_header)
                if not labor_name:
                    continue

                qty_val, raw_val = parse_cell_value(row.get(labor_header))
                if qty_val is None or qty_val == 0:
                    continue

                labor_entity = Entity(
                    type=EntityType.LABOR,
                    name=labor_name,
                    unit="인",
                    quantity=qty_val,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                )
                entities.append(labor_entity)

                if work_type_name:
                    rel = Relationship(
                        source=work_type_name,
                        source_type=EntityType.WORK_TYPE,
                        target=labor_name,
                        target_type=EntityType.LABOR,
                        type=RelationType.REQUIRES_LABOR,
                        quantity=qty_val,
                        unit="인",
                        per_unit=unit_val,
                        source_chunk_id=chunk_id,
                    )
                    relationships.append(rel)

            # 장비 엔티티 + 관계
            for _, equip_header in equip_cols:
                equip_name = equip_header.split("_")[-1].strip() if "_" in equip_header else equip_header.strip()
                equip_name = normalize_entity_name(equip_name)

                qty_val, raw_val = parse_cell_value(row.get(equip_header))
                if qty_val is None or qty_val == 0:
                    continue

                equip_entity = Entity(
                    type=EntityType.EQUIPMENT,
                    name=equip_name,
                    unit="대",
                    quantity=qty_val,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                )
                entities.append(equip_entity)

                if work_type_name:
                    rel = Relationship(
                        source=work_type_name,
                        source_type=EntityType.WORK_TYPE,
                        target=equip_name,
                        target_type=EntityType.EQUIPMENT,
                        type=RelationType.REQUIRES_EQUIPMENT,
                        quantity=qty_val,
                        unit="대",
                        per_unit=unit_val,
                        source_chunk_id=chunk_id,
                    )
                    relationships.append(rel)

    # Case B: 이름-규격-단위-수량 패턴 (자재/종목 테이블)
    elif name_cols:
        for row in rows:
            _, name_header = name_cols[0]
            raw_name = str(row.get(name_header, "")).strip()
            if not raw_name or raw_name == "〃" or raw_name == "-":
                continue

            entity_name = normalize_entity_name(raw_name)

            # 규격/단위/수량
            spec_val = None
            unit_val = None
            qty = None
            for _, h in role_map.get("spec", []):
                sv = str(row.get(h, "")).strip()
                if sv and sv != "-":
                    spec_val = sv
            for _, h in role_map.get("unit", []):
                uv = str(row.get(h, "")).strip()
                if uv and uv != "-":
                    unit_val = uv
            for _, h in role_map.get("quantity", []):
                qty, _ = parse_cell_value(row.get(h))

            # 타입 추론: 노무 키워드 포함 → Labor, 장비 → Equipment, 기본 → Material
            entity_type = _infer_entity_type(entity_name)

            entity = Entity(
                type=entity_type,
                name=entity_name,
                spec=spec_val,
                unit=unit_val,
                quantity=qty,
                source_chunk_id=chunk_id,
                source_section_id=section_id,
                source_method="table_rule",
            )
            entities.append(entity)

    # Case C: 전치 테이블 (행=인력, 열=WorkType)
    # Why: 잡철물 제작 등 테이블에서 헤더가 [구분, 단위, 제품설치_일반, 제품설치_경량, ...]
    #      이고 행이 [철공, 인, 2.85, 3.71, ...] 패턴.
    #      이 경우 헤더의 other 열들이 실제로는 WorkType 이름이며,
    #      행의 첫 열(name)이 인력 명칭이고 나머지 열 값이 인력 수량이다.
    elif not labor_cols and not equip_cols and not name_cols:
        # 전치 테이블 판별: 행의 첫 열에 인력 키워드가 있는지 확인
        _LABOR_KW = [
            "인부", "철공", "용접공", "배관공", "기사", "기능공", "기능사",
            "조공", "내장공", "도장공", "미장공", "목공", "방수공",
        ]
        # name 역할 헤더가 없어도 "구분"/"구 분" 헤더로 첫 열 식별
        first_col_header = headers[0] if headers else ""
        first_col_norm = first_col_header.replace(" ", "")

        labor_rows = []
        for row in rows:
            first_val = str(row.get(first_col_header, "")).replace(" ", "")
            if any(kw in first_val for kw in _LABOR_KW):
                labor_rows.append(row)

        if len(labor_rows) >= 2:
            # 단위 열 식별
            unit_col_header = None
            for _, h in role_map.get("unit", []):
                unit_col_header = h
                break

            # WorkType 열 식별: name/unit/spec/note가 아닌 나머지 헤더들
            skip_roles = {"name", "unit", "spec", "note"}
            worktype_headers = []
            for i, h in enumerate(headers):
                role = classify_header_role(h)
                # 첫 열(구분 등)과 단위 열 제외
                if i == 0 or h == unit_col_header:
                    continue
                # 인력/장비/자재로 분류된 것도 건너뜀
                if role in ("labor", "equipment", "material"):
                    continue
                worktype_headers.append(h)

            for wt_header in worktype_headers:
                wt_name = normalize_entity_name(wt_header)
                if not wt_name:
                    continue

                # WorkType 엔티티 생성
                wt_entity = Entity(
                    type=EntityType.WORK_TYPE,
                    name=wt_name,
                    unit=None,
                    source_chunk_id=chunk_id,
                    source_section_id=section_id,
                    source_method="table_rule",
                )
                entities.append(wt_entity)

                # 각 인력 행에서 이 WorkType 열의 수량 추출
                for row in labor_rows:
                    labor_raw = str(row.get(first_col_header, "")).strip()
                    labor_name = normalize_entity_name(labor_raw)
                    if not labor_name:
                        continue

                    qty_val, _ = parse_cell_value(row.get(wt_header))
                    if qty_val is None or qty_val == 0:
                        continue

                    unit_val = str(row.get(unit_col_header, "인")).strip() if unit_col_header else "인"

                    # Labor 엔티티
                    labor_entity = Entity(
                        type=EntityType.LABOR,
                        name=labor_name,
                        unit=unit_val,
                        quantity=qty_val,
                        source_chunk_id=chunk_id,
                        source_section_id=section_id,
                        source_method="table_rule",
                    )
                    entities.append(labor_entity)

                    # REQUIRES_LABOR 관계
                    rel = Relationship(
                        source=wt_name,
                        source_type=EntityType.WORK_TYPE,
                        target=labor_name,
                        target_type=EntityType.LABOR,
                        type=RelationType.REQUIRES_LABOR,
                        quantity=qty_val,
                        unit=unit_val,
                        source_chunk_id=chunk_id,
                    )
                    relationships.append(rel)
        else:
            warnings.append(f"A_품셈 테이블이지만 인식 가능한 헤더 패턴 없음: {headers}")


    return entities, relationships, warnings


def _infer_entity_type(name: str) -> EntityType:
    """엔티티 이름에서 타입 추론"""
    n = name.replace(" ", "")
    for kw in HEADER_ENTITY_MAPPING["labor_keywords"]:
        if kw.replace(" ", "") in n:
            return EntityType.LABOR
    for kw in HEADER_ENTITY_MAPPING["equipment_keywords"]:
        if kw.replace(" ", "") in n:
            return EntityType.EQUIPMENT
    # 기본값: Material (품셈 테이블의 종목은 대부분 자재/항목)
    return EntityType.MATERIAL


def extract_from_b_table(
    table: dict,
    chunk_id: str,
    section_id: str,
    section_title: str,
) -> tuple[list[Entity], list[Relationship], list[str]]:
    """B_규모기준 테이블 → 조건(Note) 엔티티로 변환

    Why: 규모기준 테이블은 "직접노무비 3억 이상" 같은 조건을 기술하므로
    Note 엔티티로 캡처하여 공종과 연결한다.
    """
    entities: list[Entity] = []
    relationships: list[Relationship] = []
    warnings: list[str] = []

    headers = table.get("headers", [])
    rows = table.get("rows", [])
    if not rows:
        return entities, relationships, warnings

    # 전체 테이블을 하나의 Note 엔티티로 캡처
    table_text_parts = []
    for row in rows:
        row_text = " | ".join(str(v) for v in row.values() if v and str(v) != "-")
        if row_text.strip():
            table_text_parts.append(row_text)

    if table_text_parts:
        note_content = "; ".join(table_text_parts)
        note_entity = Entity(
            type=EntityType.NOTE,
            name=f"규모기준_{section_id}",
            properties={
                "content": note_content[:500],  # 길이 제한
                "note_type": "규모기준",
                "headers": headers,
            },
            source_chunk_id=chunk_id,
            source_section_id=section_id,
            source_method="table_rule",
        )
        entities.append(note_entity)

    return entities, relationships, warnings


# ─── 소제목 추출 (Phase 4C-1c) ────────────────────────────────

def _extract_sub_title(chunk_text: str) -> str | None:
    """chunk 텍스트에서 소제목(X형) 추출.

    예: '1. 전기아크용접(V형)' → 'V형'
        '2. 전기아크용접(U형)' → 'U형'

    Why: 하나의 섹션 안에 여러 하위 분류(V형, U형 등)가 있을 때,
    각 테이블의 데이터를 구분하기 위한 접미어를 추출한다.
    4C-0 스캔 결과 13-2-4 섹션만 해당.
    """
    if not chunk_text:
        return None
    pattern = r'\d+\.\s*\S+\(([^)]+형)\)'
    match = re.search(pattern, chunk_text)
    if match:
        return match.group(1)
    return None


# ─── 청크 단위 추출 ──────────────────────────────────────────

def extract_from_chunk(chunk: dict) -> ChunkExtraction:
    """단일 청크의 모든 테이블에서 엔티티/관계 추출

    Phase 4C-1 수정 사항:
    - (b) source_chunk → properties에 chunk_id 기록
    - (c) 소제목(V형/U형) 자동 반영
    - (d) 빈 텍스트 chunk에 테이블 요약 자동 생성
    """
    chunk_id = chunk["chunk_id"]
    section_id = chunk["section_id"]
    department = chunk.get("department", "")
    chapter = chunk.get("chapter", "")
    title = chunk.get("title", "")
    chunk_text = chunk.get("text", "")

    # Phase 4C-1(c): 소제목 추출 (V형, U형 등)
    sub_title = _extract_sub_title(chunk_text)

    all_entities: list[Entity] = []
    all_relationships: list[Relationship] = []
    all_warnings: list[str] = []

    tables = chunk.get("tables", [])
    if not tables:
        return ChunkExtraction(
            chunk_id=chunk_id,
            section_id=section_id,
            department=department,
            chapter=chapter,
            title=title,
            source_method="table_rule",
            warnings=["테이블 없음 — Step 2.2(LLM)에서 처리 필요"],
        )

    for table in tables:
        table_type = table.get("type", "")

        if table_type == "A_품셈":
            ents, rels, warns = extract_from_a_table(
                table, chunk_id, section_id, title
            )
        elif table_type == "B_규모기준":
            ents, rels, warns = extract_from_b_table(
                table, chunk_id, section_id, title
            )
        elif table_type in ("D_기타", "C_구분설명"):
            # Case D: 매트릭스 테이블 감지 → 규칙 추출 시도
            t_headers = table.get("headers", [])
            t_rows = table.get("rows", [])
            if is_matrix_table(t_headers, t_rows):
                ents, rels, warns = extract_from_matrix_table(
                    table, chunk_id, section_id, title
                )
            else:
                # 매트릭스 아님 → LLM 처리 대상
                ents, rels, warns = [], [], []
        else:
            ents, rels, warns = [], [], []

        all_entities.extend(ents)
        all_relationships.extend(rels)
        all_warnings.extend(warns)

    # Section 엔티티 자동 생성
    section_entity = Entity(
        type=EntityType.SECTION,
        name=title,
        code=section_id,
        properties={"department": department, "chapter": chapter},
        source_chunk_id=chunk_id,
        source_section_id=section_id,
        source_method="table_rule",
        confidence=1.0,
    )
    all_entities.append(section_entity)

    # BELONGS_TO 관계: WorkType → Section
    work_types = {e.name for e in all_entities if e.type == EntityType.WORK_TYPE}
    for wt_name in work_types:
        rel = Relationship(
            source=wt_name,
            source_type=EntityType.WORK_TYPE,
            target=title,
            target_type=EntityType.SECTION,
            type=RelationType.BELONGS_TO,
            source_chunk_id=chunk_id,
        )
        all_relationships.append(rel)

    # NOTE 관계: chunk의 notes 필드
    for note_text in chunk.get("notes", []):
        if isinstance(note_text, str) and note_text.strip():
            note_entity = Entity(
                type=EntityType.NOTE,
                name=f"note_{section_id}_{len(all_entities)}",
                properties={"content": note_text[:500]},
                source_chunk_id=chunk_id,
                source_section_id=section_id,
                source_method="table_rule",
            )
            all_entities.append(note_entity)
            all_relationships.append(Relationship(
                source=title,
                source_type=EntityType.SECTION,
                target=note_entity.name,
                target_type=EntityType.NOTE,
                type=RelationType.HAS_NOTE,
                source_chunk_id=chunk_id,
            ))

    # unit_basis → properties
    unit_basis = chunk.get("unit_basis", "")
    if unit_basis:
        for e in all_entities:
            if e.type == EntityType.WORK_TYPE:
                e.properties["unit_basis"] = unit_basis

    # Phase 4C-1(b): source_chunk → properties (전체 WorkType 엔티티)
    # Why: 이전에는 source_chunk가 null이었음 (4,733건 전부). 디버깅/추적용
    for e in all_entities:
        if e.type == EntityType.WORK_TYPE:
            e.properties["source_chunk"] = chunk_id

    # Phase 4C-1(c): 소제목 접미어 적용 (V형/U형 등)
    # Why: 13-2-4 강판 전기아크용접에서 V형/U형 테이블을 이름으로 구분
    if sub_title:
        for e in all_entities:
            if e.type == EntityType.WORK_TYPE and title in e.name:
                e.name = e.name.replace(
                    f"{title}(",
                    f"{title}-{sub_title}(",
                    1,
                )
                e.properties["sub_title"] = sub_title
        # 관계의 source도 갱신
        for r in all_relationships:
            if r.source_type == EntityType.WORK_TYPE and title in r.source:
                r.source = r.source.replace(
                    f"{title}(",
                    f"{title}-{sub_title}(",
                    1,
                )

    confidence = 1.0 if all_entities else 0.0

    # Phase 4C-1(d): 빈 텍스트 chunk에 테이블 요약 자동 생성
    # Why: 895/2,105 chunk(42.5%)의 텍스트가 비어있어 벡터 검색에서 누락됨
    chunk_text_out = chunk_text
    if not chunk_text_out and tables:
        summaries = []
        for t in tables:
            t_headers = t.get("headers", [])
            t_rows = t.get("rows", [])
            header_str = ", ".join(h for h in t_headers[:5] if h)
            summaries.append(
                f"{title} / {t.get('type', '테이블')}. 열: {header_str}. {len(t_rows)}행."
            )
        chunk_text_out = " | ".join(summaries)

    return ChunkExtraction(
        chunk_id=chunk_id,
        section_id=section_id,
        department=department,
        chapter=chapter,
        title=title,
        entities=all_entities,
        relationships=all_relationships,
        confidence=confidence,
        source_method="table_rule",
        warnings=all_warnings,
        summary=chunk_text_out,  # Phase 4C-1(d): 빈 텍스트 보충
    )


# ─── 메인 실행 ────────────────────────────────────────────────

def run_step1(sample: bool = False) -> BatchResult:
    """Step 2.1 실행: 전체 청크에 대해 테이블 기반 추출 실행"""
    print("\n  Step 2.1: 테이블 기반 규칙 추출")
    print("  " + "=" * 50)

    # 데이터 로드
    data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = data["chunks"]

    if sample:
        chunks = chunks[:50]
        print(f"  [샘플 모드] {len(chunks)}개 청크만 처리")
    else:
        print(f"  대상: {len(chunks)}개 청크")

    # 추출 실행
    result = BatchResult(total_chunks=len(chunks))
    entity_type_counter = Counter()
    rel_type_counter = Counter()

    for chunk in chunks:
        extraction = extract_from_chunk(chunk)
        result.extractions.append(extraction)
        result.processed_chunks += 1

        for e in extraction.entities:
            entity_type_counter[e.type.value] += 1
        for r in extraction.relationships:
            rel_type_counter[r.type.value] += 1

    result.total_entities = sum(entity_type_counter.values())
    result.total_relationships = sum(rel_type_counter.values())
    result.entity_type_counts = dict(entity_type_counter)
    result.relationship_type_counts = dict(rel_type_counter)

    # 저장
    PHASE2_OUTPUT.mkdir(parents=True, exist_ok=True)
    TABLE_ENTITIES_FILE.write_text(
        result.model_dump_json(indent=2),
        encoding="utf-8",
    )

    # 리포트 출력
    print(f"\n  결과:")
    print(f"    처리 청크: {result.processed_chunks}/{result.total_chunks}")
    print(f"    총 엔티티: {result.total_entities}")
    for etype, cnt in sorted(entity_type_counter.items(), key=lambda x: -x[1]):
        print(f"      {etype}: {cnt}")
    print(f"    총 관계: {result.total_relationships}")
    for rtype, cnt in sorted(rel_type_counter.items(), key=lambda x: -x[1]):
        print(f"      {rtype}: {cnt}")

    # 커버리지 통계
    chunks_with_entities = sum(
        1 for e in result.extractions if e.entities
    )
    coverage = chunks_with_entities / result.total_chunks if result.total_chunks > 0 else 0
    print(f"\n    엔티티 커버리지: {coverage:.1%} ({chunks_with_entities}/{result.total_chunks})")

    warnings_count = sum(len(e.warnings) for e in result.extractions)
    print(f"    경고: {warnings_count}건")

    print(f"\n  저장: {TABLE_ENTITIES_FILE}")
    return result


if __name__ == "__main__":
    sample_mode = "--sample" in sys.argv
    run_step1(sample=sample_mode)
