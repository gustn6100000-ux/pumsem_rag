"""Phase 1 전처리 파이프라인 설정"""
import os
import re
from pathlib import Path

# ─── 경로 설정 ───────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
MD_DIR = BASE_DIR / "download_file"
TOC_FILE = BASE_DIR / "toc_parser" / "toc_parsed.json"
OUTPUT_DIR = BASE_DIR / "phase1_output"

# 출력 파일 경로
RAW_SECTIONS_FILE = OUTPUT_DIR / "raw_sections.json"
PARSED_TABLES_FILE = OUTPUT_DIR / "parsed_tables.json"
CLEANED_SECTIONS_FILE = OUTPUT_DIR / "cleaned_sections.json"
CHUNKS_FILE = OUTPUT_DIR / "chunks.json"
QUALITY_REPORT_FILE = OUTPUT_DIR / "quality_report.json"

# ─── 파일럿 파일 ─────────────────────────────────────────────
PILOT_FILES = [
    "20260208_170-199 OKOK.md",   # 제6장 철근콘크리트공사
    "20260207_84-113 OKOK.md",    # 제2장 가설공사
]

# ─── 정규표현식 패턴 ──────────────────────────────────────────
PATTERNS = {
    # 섹션/페이지 마커
    "section_marker": re.compile(
        r'<!-- SECTION: (\S+) \| (.+?) \| 부문:(.+?) \| 장:(.+?) -->'
    ),
    "page_marker": re.compile(
        r'<!-- PAGE (\d+) \| (.+?) -->'
    ),
    "context_marker": re.compile(
        r'<!-- CONTEXT: (.+?) -->'
    ),
    # CONTEXT 마커(SECTION과 동일 캡처그룹): 일부 섹션이 CONTEXT로 표기됨
    "context_section_marker": re.compile(
        r'<!-- CONTEXT: (\S+) \| (.+?) \| 부문:(.+?) \| 장:(.+?) -->'
    ),

    # 주석 블록
    "note_block_start": re.compile(r'^\[주\]\s*$', re.MULTILINE),
    "note_item": re.compile(
        r'([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮])\s*(.*?)(?=(?:[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮])|\Z)',
        re.DOTALL
    ),

    # 조건/할증
    "surcharge": re.compile(
        r'(.+?(?:경우|때|시))\s*(?:본\s*)?(?:품|시공량).*?(\d+)%.*?(가산|감산|감|증|할증)'
    ),

    # 교차참조
    "cross_ref": re.compile(
        r'(?:제(\d+)장\s+)?(\d+-\d+(?:-\d+)?)\s*(?:항?\s*)?(?:참조|준용|적용|따른다)'
    ),

    # 보완연도: ('24년 보완), ('02, '22년 보완)
    "revision": re.compile(
        r"\('?(\d{2,4})(?:,\s*'?(\d{2,4}))*년\s*보완\)"
    ),

    # 단위 기준: (일당), (m³당), (100m당)
    "unit_basis": re.compile(r'\(([^)]*당)\)'),

    # 장 제목 텍스트: "제6장 철근콘크리트공사"
    "chapter_title": re.compile(r'^(제\d+장\s+.+)$', re.MULTILINE),

    # 절/항 제목: "6-1 콘크리트('25년 보완)"
    "section_title": re.compile(
        r'^(\d+-\d+(?:-\d+)?)\s+(.+?)$', re.MULTILINE
    ),
}

# ─── 청킹 설정 ───────────────────────────────────────────────
MAX_CHUNK_TOKENS = 1500
MIN_CHUNK_TOKENS = 500
TOKEN_ENCODING = "cl100k_base"

# ─── 테이블 유형 판별 키워드 ──────────────────────────────────
TABLE_TYPE_KEYWORDS = {
    "A_품셈": ["수량", "단위", "인", "대", "수 량", "단 위"],
    "B_규모기준": ["억", "m²", "규모", "직접노무비"],
    "C_구분설명": ["구분", "내용", "구 분", "내 용"],
}

# ─── 품질 검증 기준 ───────────────────────────────────────────
QUALITY_THRESHOLDS = {
    "section_coverage_min": 0.95,
    "table_parse_success_min": 0.90,
    "empty_chunk_max_ratio": 0.05,
    "avg_token_min": 300,
    "avg_token_max": 800,
    "max_token_limit": 2000,
}

# ─── 심화 품질 검증 기준 (Tier 2) ─────────────────────────────
DEEP_CHECK_THRESHOLDS = {
    "text_fidelity_min": 0.7,         # D1: 원본 대비 Jaccard 유사도
    "table_cell_accuracy_min": 0.9,   # D2: 테이블 셀 일치율
    "section_contamination_max": 0,   # D3: 섹션 경계 혼입 건수
    "crossref_validity_min": 1.0,     # D4: 교차참조 유효율
    "notes_recall_min": 0.95,         # D5: 주석 추출 재현율
    "numeric_preservation_min": 0.93, # D6: 숫자 토큰 보존율 (extract_numbers 노이즈 감안)
}

# ─── 심화 검증 출력 파일 ──────────────────────────────────────
DEEP_CHECK_REPORT_FILE = OUTPUT_DIR / "deep_check_report.json"
LLM_AUDIT_REPORT_FILE = OUTPUT_DIR / "llm_quality_audit_report.json"
