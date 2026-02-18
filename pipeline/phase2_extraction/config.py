# -*- coding: utf-8 -*-
"""Phase 2 설정: 엔티티 & 관계 추출 파이프라인"""
from pathlib import Path

# ─── 경로 ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
PHASE1_OUTPUT = BASE_DIR / "phase1_output"
PHASE2_OUTPUT = BASE_DIR / "phase2_output"
TOC_FILE = BASE_DIR / "toc_parser" / "toc_parsed.json"

# Phase 1 입력 파일
CHUNKS_FILE = PHASE1_OUTPUT / "chunks.json"
RAW_SECTIONS_FILE = PHASE1_OUTPUT / "raw_sections.json"

# Phase 2 출력 파일
TABLE_ENTITIES_FILE = PHASE2_OUTPUT / "table_entities.json"
LLM_ENTITIES_FILE = PHASE2_OUTPUT / "llm_entities.json"
MERGED_ENTITIES_FILE = PHASE2_OUTPUT / "merged_entities.json"
RELATIONSHIPS_FILE = PHASE2_OUTPUT / "relationships.json"
EXTRACTION_REPORT_FILE = PHASE2_OUTPUT / "extraction_report.json"
FAILED_EXTRACTIONS_FILE = PHASE2_OUTPUT / "failed_extractions.json"

# ─── 테이블 추출 설정 ─────────────────────────────────────────
# A_품셈 테이블의 헤더에서 엔티티 유형을 판별하기 위한 키워드 매핑
HEADER_ENTITY_MAPPING = {
    # 노무(Labor) 판별 키워드: 헤더에 이 단어가 포함되면 해당 열은 Labor 관련
    "labor_keywords": [
        "인부", "인원", "인력", "노무",
        "특별인부", "보통인부",
        "콘크리트공", "철근공", "비계공", "형틀목공", "용접공",
        "배관공", "전공", "조적공", "미장공", "방수공",
        "도장공", "타일공", "내장공", "판금공", "석공",
        "건축목공", "측량사", "측량보조", "기능공",
        "기사", "산업기사", "기능사", "기술자",
        "지적기사", "지적산업기사", "지적기능사",
        "특수용접공", "취부공",
    ],

    # 장비(Equipment) 판별 키워드
    "equipment_keywords": [
        "장비", "기계", "크레인", "굴착기", "굴삭기",
        "레미콘", "덤프트럭", "펌프카", "진동롤러",
        "로울러", "살수차", "콤프레셔", "발전기",
        "항타기", "천공기", "보링기", "그라우팅",
        "컷터기", "절단기", "다짐기", "다짐장비",
        "용접기", "브레이커",
    ],

    # 자재(Material) 판별 키워드
    "material_keywords": [
        "재료", "자재", "재료비",
        "시멘트", "골재", "모래", "자갈", "철근",
        "콘크리트", "레미콘", "아스팔트", "아스콘",
        "합판", "거푸집", "비계", "동바리",
        "방수재", "접착제", "실링재", "도료",
        "관", "파이프", "밸브", "이음쇠",
    ],
}

# 헤더에서 공종명(WorkType)을 식별하는 키워드
WORKTYPE_HEADER_KEYWORDS = [
    "명칭", "공종명", "종목", "종 목", "공종", "항목",
    "작업명", "작업별", "구분", "종별", "종 별", "품명",
]

# 규격·단위 관련 헤더 키워드
SPEC_HEADER_KEYWORDS = ["규격", "규 격", "사양", "사 양", "치수"]
UNIT_HEADER_KEYWORDS = ["단위", "단 위"]
# Why: "규 격_소수자리" 같은 헤더에서 '단위'가 매칭되어 소수자리 숫자가 unit으로 잘못 추출됨
UNIT_EXCLUDE_KEYWORDS = ["소수자리", "소수 자리"]
QUANTITY_HEADER_KEYWORDS = ["수량", "수 량", "소요량"]

# ─── 엔티티 정규화 ────────────────────────────────────────────
# 공백 정규화 대상 (인명 내 공백 제거)
LABOR_NORMALIZE_MAP = {
    "특 별 인 부": "특별인부",
    "보 통 인 부": "보통인부",
    "콘 크 리 트 공": "콘크리트공",
    "철 근 공": "철근공",
    "비 계 공": "비계공",
    "형 틀 목 공": "형틀목공",
    "용 접 공": "용접공",
    "배 관 공": "배관공",
    "조 적 공": "조적공",
    "미 장 공": "미장공",
    "방 수 공": "방수공",
    "도 장 공": "도장공",
    "타 일 공": "타일공",
    "판 금 공": "판금공",
    "석 공": "석공",
    "지 적 기 사": "지적기사",
    "지 적 산 업 기 사": "지적산업기사",
    "지 적 기 능 사": "지적기능사",
}

# ─── 품질 검증 임계값 ─────────────────────────────────────────
EXTRACTION_THRESHOLDS = {
    "entity_coverage_min": 0.90,      # E1: ≥90% 청크에서 1+ 엔티티
    "orphan_node_max": 0.05,          # E4: 관계 없는 엔티티 ≤5%
    "sample_accuracy_min": 0.90,      # E5: 수동 검증 정확도 ≥90%
    "hallucination_max": 0.02,        # E6: 원본에 없는 엔티티 ≤2%
}

# ─── LLM 설정 ─────────────────────────────────────────────────
LLM_MODEL = "deepseek-chat"  # DeepSeek-V3
LLM_TEMPERATURE = 0.1
LLM_CONCURRENCY = 10
LLM_RETRY_COUNT = 3
