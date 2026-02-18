"""
하이브리드 PDF 추출 스크립트 v3.3
- pdfplumber: 텍스트 추출 (무료)
- Gemini Vision: 테이블만 이미지로 변환 후 추출
- 목차 연동: 구조 정보(챕터/섹션) 자동 삽입

수정 내역 (v3.2 → v3.3):
  - 텍스트 줄바꿈: 문장 종결 패턴 감지 → 과도한 병합 방지
  - 테이블 크롭: 아래쪽 패딩 대폭 확대 (본문 잘림 방지)
  - bbox 검증: 비정상 작은 테이블 감지 → 전체 페이지 Gemini 폴백
  - Gemini 프롬프트: 잘린 테이블/복잡한 구조 대응 강화

필요 라이브러리:
pip install pdfplumber google-generativeai pdf2image pillow python-dotenv

사용법:
python step1_extract_gemini.py [옵션] <PDF파일경로>

옵션:
  --text-only, -t   텍스트 전용 모드 (빠름)
  --toc <파일>      목차 파일 경로 (구조 정보 삽입)
  --pages <지정>    처리할 페이지 (예: 10, 16-30, 1,3,5-10, 20-)
"""

import os
import sys
import time
import platform
import logging
from pathlib import Path
from dotenv import load_dotenv

# .env 파일 로드 (스크립트와 같은 폴더에서 찾음)
load_dotenv(Path(__file__).parent / ".env")
import pdfplumber
import google.generativeai as genai
from PIL import Image
from pdf2image import convert_from_path
import toc_parser

# --- 로깅 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- 설정 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")

if not GEMINI_API_KEY:
    raise ValueError(
        "❌ GEMINI_API_KEY 환경변수가 설정되지 않았습니다.\n"
        "   .env 파일에 GEMINI_API_KEY=your_key 형식으로 추가하거나\n"
        "   시스템 환경변수로 설정하세요."
    )

# Poppler 경로 (플랫폼별 자동 분기)
def _detect_poppler_path() -> str | None:
    """OS에 따라 Poppler 경로를 자동 감지"""
    if platform.system() == "Windows":
        candidates = [
            r"C:\poppler\poppler-24.08.0\Library\bin",
            r"C:\Program Files\poppler\Library\bin",
            r"C:\poppler\bin",
        ]
        env_path = os.environ.get("POPPLER_PATH")
        if env_path:
            candidates.insert(0, env_path)
        for path in candidates:
            if os.path.exists(path):
                return path
        logger.warning("Windows에서 Poppler 경로를 찾을 수 없습니다. POPPLER_PATH 환경변수를 설정하세요.")
        return None
    else:
        return None

POPPLER_PATH = _detect_poppler_path()

# 무료 티어 딜레이 (초) - 15 RPM 제한 고려
FREE_TIER_DELAY = 4

# 부문명 패턴 (확장)
DIVISION_NAMES = (
    "공통부문|토목부문|건축부문|기계설비부문|"
    "전기부문|통신부문|조경부문|소방부문|"
    "기계부문|설비부문|전기설비부문"
)

# --- 테이블 bbox 검증 설정 ---
# 페이지 높이 대비 이 비율 미만이면 "헤더만 잡힌" 비정상 테이블로 판단
TABLE_MIN_HEIGHT_RATIO = 0.08  # 8%
# 크롭 시 아래쪽 추가 패딩 (포인트 단위)
TABLE_BOTTOM_EXTRA_PADDING = 40

# -----------


class UsageTracker:
    """Gemini API 사용량 추적 클래스"""

    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.call_count = 0

    def add(self, input_tokens: int, output_tokens: int):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.call_count += 1

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def summary(self) -> str:
        if self.call_count == 0:
            return "Gemini 호출 없음"
        est_cost = (self.total_input_tokens / 1_000_000 * 0.50) + (self.total_output_tokens / 1_000_000 * 1.50)
        return (
            f"📈 Gemini 사용량 요약:\n"
            f"   - API 호출: {self.call_count}회\n"
            f"   - 입력 토큰: {self.total_input_tokens:,}\n"
            f"   - 출력 토큰: {self.total_output_tokens:,}\n"
            f"   - 총 토큰: {self.total_tokens:,}\n"
            f"   - 예상 비용 (유료 시): ${est_cost:.4f} (약 {int(est_cost * 1400)}원)"
        )


def parse_page_spec(spec: str, total_pages: int) -> list[int]:
    """
    페이지 지정 문자열을 파싱하여 0-indexed 페이지 인덱스 리스트 반환.
    
    지원 형식:
    - "10" → 1~10 페이지 (기존 호환)
    - "5-15" → 5~15 페이지
    - "20-" → 20~끝
    - "-10" → 1~10 페이지  
    - "1,3,5-10" → 1, 3, 5~10 페이지
    """
    spec = spec.strip()
    indices = set()
    
    parts = [p.strip() for p in spec.split(',') if p.strip()]
    
    for part in parts:
        if '-' in part:
            if part.startswith('-'):
                end = int(part[1:])
                start = 1
            elif part.endswith('-'):
                start = int(part[:-1])
                end = total_pages
            else:
                start_str, end_str = part.split('-', 1)
                start = int(start_str)
                end = int(end_str)
            
            for p in range(start, end + 1):
                if 1 <= p <= total_pages:
                    indices.add(p - 1)
        else:
            p = int(part)
            if len(parts) == 1 and ',' not in spec and '-' not in spec:
                for i in range(min(p, total_pages)):
                    indices.add(i)
            else:
                if 1 <= p <= total_pages:
                    indices.add(p - 1)
    
    return sorted(indices)


# 전역 트래커 인스턴스
tracker = UsageTracker()

# Gemini API 초기화
genai.configure(api_key=GEMINI_API_KEY)

# 안전 설정 (필터 완화)
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


def _parse_usage_metadata(response) -> tuple[int, int]:
    """응답에서 토큰 사용량 추출"""
    input_tokens = 0
    output_tokens = 0
    if hasattr(response, 'usage_metadata') and response.usage_metadata:
        input_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
        output_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
    return input_tokens, output_tokens


def extract_page_footer_metadata(text: str) -> dict:
    """페이지 하단 푸터에서 부문명과 장 정보 추출"""
    import re
    
    result = {"chapter": "", "section": "", "page_num": 0}
    
    if not text:
        return result
    
    match = re.search(r'(제\d+장\s*[가-힣]+(?:\s+[가-힣]+)*)\s+\|?\s*(\d+)(?:\s|$)', text)
    if match:
        result["section"] = match.group(1).strip()
        result["page_num"] = int(match.group(2))
    
    pattern = rf'(\d+)\s+({DIVISION_NAMES})'
    match = re.search(pattern, text)
    if match:
        page = int(match.group(1))
        chapter = match.group(2)
        if result["page_num"] == 0:
            result["page_num"] = page
        result["chapter"] = chapter
    
    return result


def detect_tables(page) -> list:
    """페이지에서 테이블 위치(bbox) 목록 반환"""
    try:
        tables = page.find_tables()
        return [table.bbox for table in tables]
    except Exception as e:
        logger.warning(f"테이블 감지 실패: {e}")
        return []


def validate_and_fix_table_bboxes(table_bboxes: list, page_height: float, page_width: float) -> tuple[list, bool]:
    """
    [개선3] 테이블 bbox 검증 및 보정
    
    - 비정상적으로 작은 bbox (헤더만 잡힌 경우) 감지
    - 아래쪽으로 bbox 확장 시도
    
    Returns:
        (보정된 bboxes, 전체페이지 폴백 필요 여부)
    """
    if not table_bboxes:
        return table_bboxes, False
    
    fixed_bboxes = []
    needs_fullpage_fallback = False
    
    for i, bbox in enumerate(table_bboxes):
        x0, y0, x1, y1 = bbox
        table_height = y1 - y0
        height_ratio = table_height / page_height
        
        if height_ratio < TABLE_MIN_HEIGHT_RATIO:
            # 비정상적으로 작은 테이블 — 헤더만 잡혔을 가능성
            logger.info(
                f"테이블 {i+1} bbox 높이 비정상 ({height_ratio:.1%}, "
                f"{table_height:.0f}pt / {page_height:.0f}pt)"
            )
            
            # 다음 테이블이 있으면 그 위까지, 없으면 페이지 하단 80%까지 확장
            if i + 1 < len(table_bboxes):
                next_top = table_bboxes[i + 1][1]
                new_y1 = next_top - 5  # 다음 테이블 직전까지
            else:
                new_y1 = min(page_height * 0.85, page_height - 30)
            
            new_height = new_y1 - y0
            new_ratio = new_height / page_height
            
            if new_ratio > 0.5:
                # 확장해도 페이지 절반 이상이면 전체 페이지 폴백
                logger.info(f"  → 확장 시 페이지 {new_ratio:.0%} 차지 → 전체 페이지 Gemini 처리")
                needs_fullpage_fallback = True
                break
            else:
                logger.info(f"  → bbox 아래로 확장: {table_height:.0f}pt → {new_height:.0f}pt")
                fixed_bboxes.append((x0, y0, x1, new_y1))
        else:
            fixed_bboxes.append(bbox)
    
    return fixed_bboxes, needs_fullpage_fallback


def extract_text_outside_tables(page, table_bboxes: list) -> str:
    """테이블 영역을 제외한 텍스트만 추출"""
    try:
        if table_bboxes:
            filtered_page = page
            failed_bboxes = []
            for bbox in table_bboxes:
                try:
                    filtered_page = filtered_page.outside_bbox(bbox)
                except Exception as e:
                    logger.warning(f"outside_bbox 실패 (bbox={bbox}): {e}")
                    failed_bboxes.append(bbox)
            
            if len(failed_bboxes) == len(table_bboxes):
                logger.warning("모든 테이블 영역 제외 실패, 전체 텍스트 사용")
                text = page.extract_text()
            else:
                text = filtered_page.extract_text()
        else:
            text = page.extract_text()
        
        return text.strip() if text else ""
    except Exception as e:
        logger.error(f"텍스트 추출 실패: {e}")
        return ""


def extract_text_regions_with_positions(page, table_bboxes: list) -> list[dict]:
    """
    텍스트를 테이블 기준으로 분할하여 각 영역의 y좌표와 함께 반환.
    """
    if not table_bboxes:
        text = page.extract_text()
        if text and text.strip():
            return [{"y": 0, "type": "text", "content": format_text_with_linebreaks(text.strip())}]
        return []
    
    sorted_bboxes = sorted(table_bboxes, key=lambda b: b[1])
    
    page_width = page.width
    page_height = page.height
    
    text_regions = []
    
    boundaries = []
    boundaries.append(0)
    for bbox in sorted_bboxes:
        boundaries.append(bbox[1])
        boundaries.append(bbox[3])
    boundaries.append(page_height)
    
    for i in range(0, len(boundaries) - 1, 2):
        top = boundaries[i]
        bottom = boundaries[i + 1] if i + 1 < len(boundaries) else page_height
        
        if bottom - top < 5:
            continue
        
        try:
            crop_bbox = (0, top, page_width, bottom)
            cropped = page.within_bbox(crop_bbox)
            text = cropped.extract_text()
            if text and text.strip():
                formatted = format_text_with_linebreaks(text.strip())
                if formatted:
                    text_regions.append({
                        "y": top,
                        "type": "text",
                        "content": formatted
                    })
        except Exception as e:
            logger.debug(f"텍스트 영역 추출 실패 (top={top:.0f}, bottom={bottom:.0f}): {e}")
    
    return text_regions


def _is_sentence_ending(line: str) -> bool:
    """
    [개선1] 한국어 문장 종결 패턴 감지
    
    줄이 문장 종결로 끝나면 True → 다음 줄을 이어붙이지 않음
    """
    import re
    
    line = line.rstrip()
    if not line:
        return False
    
    # 한국어 문장 종결 패턴
    # 다. 한다. 된다. 있다. 없다. 같다. 한다. 않는다. 이다. 
    # ~요. ~임. ~음. ~함. ~됨.
    # ~것 (종결 명사)
    # ) 또는 ] 로 끝나는 경우 (괄호 닫힘)
    # : 로 끝나는 경우 (항목 소개)
    ending_patterns = [
        r'다\.$',           # ~다.
        r'다\)$',           # ~다)
        r'다"$',           # ~다"
        r'[요임음함됨]\.$',  # ~요. ~임. ~음. ~함. ~됨.
        r'것$',            # ~것
        r'[\.]\s*$',       # . 으로 끝남
        r'\)$',            # ) 로 끝남
        r'\]$',            # ] 로 끝남
        r':$',             # : 로 끝남
    ]
    
    for pattern in ending_patterns:
        if re.search(pattern, line):
            return True
    
    return False


def format_text_with_linebreaks(text: str) -> str:
    """
    텍스트 후처리 - PDF 줄바꿈 병합 및 정리
    
    [개선1] 문장 종결 패턴 감지 + 줄 길이 제한으로 과도한 병합 방지
    """
    import re
    
    if not text:
        return ""
    
    # 0. 섹션 제목 패턴 앞에 줄바꿈 삽입 (병합 전 처리)
    text = re.sub(r'(?<=[^\n])(\d+-\d+-\d+\s+)', r'\n\n\1', text)
    text = re.sub(r'(?<=[다\.\)\]]) (\d+\.\s+)', r'\n\1', text)
    text = re.sub(r'(?<=[다\.\)\]]) ([가나다라마바사아자차카타파하]\.\s+)', r'\n\1', text)
    text = re.sub(r'(?<=[^\n])(\[주\])', r'\n\n\1', text)
    text = re.sub(r'(?<=[다\.\)\]]) ([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])', r'\n\1', text)
    
    # 부문명 패턴 분리 (섹션 ID X-Y-Z의 마지막 숫자는 제외)
    text = re.sub(rf'(?<![-\d])(\d+\s*(?:{DIVISION_NAMES}|적용기준|제\d+장))', r'\n\1', text)
    
    # 1. PDF 줄바꿈으로 끊긴 문장 병합
    text = re.sub(r'([가-힣])\n([가-힣]{0,2}다[\.\\, ])', r'\1\2', text)
    text = re.sub(r'([가-힣])\n(다)$', r'\1\2', text, flags=re.MULTILINE)
    
    # 2. 단일 줄바꿈 → 공백 변환 (개선: 문장 종결/줄 길이 감지)
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append('')
            continue
        
        # 번호/기호로 시작하면 항상 새 줄 유지
        if re.match(
            rf'^(\d+[-.]|[가-하]\.|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]|\[주\]|\d+-\d+-\d+|\d+\s*(?:{DIVISION_NAMES}|적용기준|제\d+장))',
            stripped
        ):
            result.append(stripped)
        elif result and result[-1]:
            prev_line = result[-1]
            
            # [개선1] 이전 줄이 문장 종결이면 이어붙이지 않음
            if _is_sentence_ending(prev_line):
                result.append(stripped)
            # [개선1] 이전 줄이 이미 80자 이상이면 이어붙이지 않음
            elif len(prev_line) >= 80:
                result.append(stripped)
            else:
                result[-1] = prev_line + ' ' + stripped
        else:
            result.append(stripped)
    
    text = '\n'.join(result)
    
    # 3. 연속 줄바꿈 정리 (3개 이상 → 2개로)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # 4. 연속 공백 정리
    text = re.sub(r' {2,}', ' ', text)
    
    return text.strip()


def crop_table_image(
    page_image: Image.Image, 
    bbox: tuple, 
    page_height: float, 
    page_width: float,
    extended: bool = False
) -> Image.Image:
    """
    테이블 영역을 이미지로 크롭
    
    [개선2] extended=True 시 아래쪽 패딩을 대폭 확대하여 본문 잘림 방지
    """
    x0, y0, x1, y1 = bbox
    
    scale_x = page_image.width / page_width
    scale_y = page_image.height / page_height
    
    img_x0 = int(x0 * scale_x)
    img_y0 = int(y0 * scale_y)
    img_x1 = int(x1 * scale_x)
    img_y1 = int(y1 * scale_y)
    
    # 기본 패딩
    padding_x = 10
    padding_top = 10
    
    # [개선2] 아래쪽 패딩: 기본 10 → 확장 시 TABLE_BOTTOM_EXTRA_PADDING 추가
    if extended:
        padding_bottom = int(TABLE_BOTTOM_EXTRA_PADDING * scale_y)
    else:
        padding_bottom = 10
    
    img_x0 = max(0, img_x0 - padding_x)
    img_y0 = max(0, img_y0 - padding_top)
    img_x1 = min(page_image.width, img_x1 + padding_x)
    img_y1 = min(page_image.height, img_y1 + padding_bottom)
    
    return page_image.crop((img_x0, img_y0, img_x1, img_y1))


def extract_table_with_gemini(image: Image.Image, table_num: int) -> tuple[str, int, int]:
    """
    테이블 이미지를 Gemini Vision으로 파싱
    
    [개선4] 프롬프트 강화: 잘린 테이블/복잡한 구조 대응
    """
    model = genai.GenerativeModel(GEMINI_MODEL)
    
    # [개선4] 프롬프트 강화
    prompt = """이 건설 관련 테이블 이미지를 HTML 형식으로 정확히 변환해주세요.

규칙:
1. 반드시 <table>, <thead>, <tbody> 태그를 모두 사용
2. 병합된 셀은 rowspan/colspan 정확히 표현
3. 헤더가 여러 줄이면 <thead>에 모두 포함
4. <tbody>에 모든 데이터 행을 빠짐없이 포함 — 본문 행을 절대 생략하지 마세요
5. 숫자, 단위, 규격은 원본 그대로 정확히 추출
6. 이미지 하단이 잘려 보여도, 보이는 모든 행을 끝까지 추출
7. 설명이나 코드블록 없이 <table>...</table> HTML만 출력
"""
    
    time.sleep(FREE_TIER_DELAY)
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            response = model.generate_content([prompt, image], safety_settings=SAFETY_SETTINGS)
            
            input_tokens, output_tokens = _parse_usage_metadata(response)
            tracker.add(input_tokens, output_tokens)
            
            result = response.text.strip()
            
            # 코드 블록 제거
            if result.startswith("```"):
                lines = result.split("\n")
                if lines[-1].strip() == "```":
                    result = "\n".join(lines[1:-1])
                else:
                    result = "\n".join(lines[1:])
            
            print(f"      ✅ 테이블 {table_num} 완료 (토큰: {input_tokens}+{output_tokens})")
            return result, input_tokens, output_tokens
        
        except Exception as e:
            error_str = str(e)
            
            if "429" in error_str and attempt < max_retries - 1:
                print(f"      ⚠️ 할당량 초과! 60초 대기 후 재시도 ({attempt + 1}/{max_retries})...")
                time.sleep(60)
                continue
            
            logger.error(f"테이블 {table_num} 추출 실패 (시도 {attempt + 1}): {e}")
            return f"<!-- 테이블 {table_num} 추출 실패: {error_str[:100]} -->", 0, 0
    
    return f"<!-- 테이블 {table_num} 추출 실패 -->", 0, 0


def extract_full_page_with_gemini(image: Image.Image, page_num: int) -> tuple[str, int, int]:
    """
    페이지 전체를 Gemini로 파싱
    
    [개선3] bbox 검증 실패 시 폴백으로 사용
    [개선4] 프롬프트 강화
    """
    model = genai.GenerativeModel(GEMINI_MODEL)
    
    # [개선4] 전체 페이지 프롬프트 강화
    prompt = """이 건설 관련 문서 이미지를 분석하여 마크다운 + HTML 형식으로 변환해주세요.

규칙:
1. 테이블은 반드시 HTML <table> 형식으로 변환
   - <thead>와 <tbody>를 반드시 구분
   - 모든 데이터 행을 빠짐없이 <tbody>에 포함
   - 병합 셀은 rowspan/colspan 사용
2. 일반 텍스트는 마크다운 형식
3. 숫자, 단위, 규격은 원본 그대로 정확히 추출
4. 테이블 앞뒤 텍스트도 모두 포함
5. 설명 없이 변환 결과만 출력
"""
    
    time.sleep(FREE_TIER_DELAY)
    
    try:
        response = model.generate_content([prompt, image], safety_settings=SAFETY_SETTINGS)
        
        input_tokens, output_tokens = _parse_usage_metadata(response)
        tracker.add(input_tokens, output_tokens)
        
        print(f"    ✅ 전체 페이지 {page_num} Gemini 완료 (토큰: {input_tokens}+{output_tokens})")
        return response.text.strip(), input_tokens, output_tokens
        
    except Exception as e:
        logger.error(f"전체 페이지 {page_num} 오류: {e}")
        return "", 0, 0


def _build_section_markers(page_sections: list) -> str:
    """섹션 마커 문자열 생성"""
    if not page_sections:
        return ""
    markers = ""
    for sec in page_sections:
        markers += f"<!-- SECTION: {sec['id']} | {sec['title']} | 부문:{sec['chapter']} | 장:{sec['section']} -->\n"
    markers += "\n"
    return markers


def _build_page_marker(page_num: int, current_context: dict) -> str:
    """페이지 마커 문자열 생성"""
    context_str = ""
    if current_context.get("chapter") or current_context.get("section"):
        parts = [p for p in [current_context.get("chapter", ""), current_context.get("section", "")] if p]
        context_str = f" | {' > '.join(parts)}" if parts else ""
    return f"<!-- PAGE {page_num}{context_str} -->\n\n"


def _build_context_marker(active_section: dict) -> str:
    """현재 활성 섹션에 대한 CONTEXT 마커 생성 (섹션이 계속되는 페이지용)"""
    if not active_section:
        return ""
    return f"<!-- CONTEXT: {active_section['id']} | {active_section['title']} | 부문:{active_section['chapter']} | 장:{active_section['section']} -->\n\n"


def _process_toc_context(
    full_text: str,
    page_map: dict,
    current_context: dict
) -> tuple[dict, list, int]:
    """푸터/목차 기반 컨텍스트 업데이트"""
    footer_meta = extract_page_footer_metadata(full_text)
    pdf_page_num = footer_meta.get("page_num", 0)
    
    if footer_meta.get("chapter"):
        current_context["chapter"] = footer_meta["chapter"]
    if footer_meta.get("section"):
        current_context["section"] = footer_meta["section"]
    
    page_sections = []
    if pdf_page_num > 0 and page_map:
        current_context = toc_parser.get_current_context(pdf_page_num, page_map, current_context)
        page_sections = current_context.get("sections", [])
    
    return current_context, page_sections, pdf_page_num


def process_pdf_text_only(pdf_path: str, section_map: dict = None, page_indices: list[int] = None) -> str:
    """PDF를 텍스트 전용 모드로 처리"""
    print(f"📄 텍스트 전용 모드로 PDF 처리 중: {pdf_path}")
    
    page_map = {}
    if section_map:
        page_map = toc_parser.build_page_to_sections_map(section_map)
        print(f"    📚 페이지 기반 목차 매핑: {len(page_map)}개 페이지")
    
    current_context = {"chapter": "", "section": "", "sections": []}
    markdown_output = ""
    
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
        
        if page_indices is None:
            indices_to_process = list(range(total_pages))
        else:
            indices_to_process = [i for i in page_indices if i < total_pages]
        
        print(f"📄 총 {total_pages}페이지 중 {len(indices_to_process)}페이지 처리 예정")
        
        for idx, i in enumerate(indices_to_process):
            page = pdf.pages[i]
            page_num = i + 1
            print(f"\n🔄 페이지 {page_num} ({idx+1}/{len(indices_to_process)}) 처리 중...")
            
            text = page.extract_text() or ""
            
            if section_map:
                current_context, page_sections, pdf_page_num = _process_toc_context(
                    text, page_map, current_context
                )
                if page_sections:
                    print(f"    📖 목차 매핑: {len(page_sections)}개 섹션 (PDF 페이지 {pdf_page_num})")
            else:
                page_sections = []
                pdf_page_num = 0
            
            markdown_output += _build_page_marker(page_num, current_context)
            
            if page_sections:
                # 새 섹션 시작 → SECTION 마커
                markdown_output += _build_section_markers(page_sections)
            elif section_map and pdf_page_num > 0:
                # 섹션 계속 → CONTEXT 마커
                active_section = toc_parser.get_active_section(pdf_page_num, section_map)
                if active_section:
                    markdown_output += _build_context_marker(active_section)
                    print(f"    📖 컨텍스트 유지: {active_section['id']} (PDF 페이지 {pdf_page_num})")
            
            if text:
                formatted_text = format_text_with_linebreaks(text)
                markdown_output += formatted_text + "\n\n"
                print(f"    ✅ 텍스트 추출 완료 ({len(text):,} chars)")
            else:
                print(f"    ⚠️ 텍스트 없음")
    
    return markdown_output


def process_pdf(pdf_path: str, section_map: dict = None, page_indices: list[int] = None) -> str:
    """
    PDF를 하이브리드 방식으로 처리
    
    [개선2] 테이블 크롭 시 아래쪽 패딩 확대
    [개선3] bbox 검증 → 비정상 시 전체 페이지 Gemini 폴백
    """
    print(f"📄 하이브리드 모드 PDF 처리 중: {pdf_path}")

    markdown_output = ""

    page_map = {}
    if section_map:
        page_map = toc_parser.build_page_to_sections_map(section_map)
        print(f"    📚 페이지 기반 목차 매핑: {len(page_map)}개 페이지")

    current_context = {"chapter": "", "section": "", "sections": []}

    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)

        if page_indices is None:
            indices_to_process = list(range(total_pages))
        else:
            indices_to_process = [i for i in page_indices if i < total_pages]

        print(f"📄 총 {total_pages}페이지 중 {len(indices_to_process)}페이지 처리 예정")

        for idx, i in enumerate(indices_to_process):
            plumber_page = pdf.pages[i]
            page_num = i + 1
            print(f"\n🔄 페이지 {page_num} ({idx+1}/{len(indices_to_process)}) 처리 중...")
            
            full_text = plumber_page.extract_text() or ""
            
            current_context, page_sections, pdf_page_num = _process_toc_context(
                full_text, page_map, current_context
            )
            if page_sections:
                print(f"    📖 목차 매핑: {len(page_sections)}개 섹션 (PDF 페이지 {pdf_page_num})")
            
            markdown_output += _build_page_marker(page_num, current_context)
            
            if page_sections:
                # 새 섹션 시작 → SECTION 마커
                markdown_output += _build_section_markers(page_sections)
            elif section_map and pdf_page_num > 0:
                # 섹션 계속 → CONTEXT 마커
                active_section = toc_parser.get_active_section(pdf_page_num, section_map)
                if active_section:
                    markdown_output += _build_context_marker(active_section)
                    print(f"    📖 컨텍스트 유지: {active_section['id']} (PDF 페이지 {pdf_page_num})")

            
            # 1. 테이블 감지
            table_bboxes = detect_tables(plumber_page)
            print(f"    📊 테이블 {len(table_bboxes)}개 감지")
            
            # 2. 테이블 미감지 → 텍스트만 (이미지 변환 불필요)
            if len(table_bboxes) == 0:
                text = plumber_page.extract_text()
                if text:
                    formatted_text = format_text_with_linebreaks(text)
                    markdown_output += formatted_text + "\n\n"
                continue

            # 3. 테이블이 있으므로 해당 페이지만 이미지로 변환
            try:
                convert_kwargs = {"pdf_path": pdf_path, "first_page": page_num, "last_page": page_num}
                if POPPLER_PATH:
                    convert_kwargs["poppler_path"] = POPPLER_PATH
                page_image = convert_from_path(**convert_kwargs)[0]
            except Exception as e:
                logger.error(f"페이지 {page_num} 이미지 변환 실패: {e}")
                print(f"    ⚠️ 이미지 변환 실패 → 텍스트만 추출")
                text = plumber_page.extract_text()
                if text:
                    formatted_text = format_text_with_linebreaks(text)
                    markdown_output += formatted_text + "\n\n"
                continue

            # [개선3] bbox 검증 및 보정
            fixed_bboxes, needs_fallback = validate_and_fix_table_bboxes(
                table_bboxes, plumber_page.height, plumber_page.width
            )
            
            # [개선3] 전체 페이지 폴백
            if needs_fallback:
                print(f"    🔄 비정상 테이블 감지 → 전체 페이지 Gemini 처리로 전환")
                page_content, _, _ = extract_full_page_with_gemini(page_image, page_num)
                if page_content:
                    markdown_output += page_content + "\n\n"
                continue
            
            if fixed_bboxes != table_bboxes:
                print(f"    🔧 테이블 bbox 보정됨: {len(table_bboxes)}개 → {len(fixed_bboxes)}개")
            
            # 3. 텍스트 영역 분할 추출 (보정된 bbox 사용)
            elements = extract_text_regions_with_positions(plumber_page, fixed_bboxes)
            
            # 4. 테이블 처리 (Gemini Vision)
            for j, bbox in enumerate(fixed_bboxes):
                table_num = j + 1
                print(f"    🖼️ 테이블 {table_num} 크롭 및 Gemini 전송...")
                
                # [개선2] 확장 패딩으로 크롭
                table_img = crop_table_image(
                    page_image,
                    bbox,
                    plumber_page.height,
                    plumber_page.width,
                    extended=True  # 아래쪽 패딩 확대
                )
                
                table_html, _, _ = extract_table_with_gemini(table_img, table_num)
                
                if table_html:
                    elements.append({'y': bbox[1], 'type': 'table', 'content': table_html})
            
            # 5. y좌표 기준 정렬 후 출력
            elements.sort(key=lambda x: x['y'])
            
            for elem in elements:
                markdown_output += elem['content'] + "\n\n"
    
    return markdown_output


def main():
    pdf_path = None
    text_only_mode = False
    toc_path = None
    page_spec = None
    log_file = Path(__file__).parent / "step1_log.txt"
    
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('--text-only', '-t'):
            text_only_mode = True
        elif arg == '--toc' and i + 1 < len(args):
            i += 1
            toc_path = args[i]
        elif arg == '--pages' and i + 1 < len(args):
            i += 1
            page_spec = args[i]
        elif not arg.startswith('-'):
            pdf_path = arg
        i += 1
    
    if not pdf_path:
        print("=" * 50)
        print("하이브리드 PDF 추출기 v3.3")
        print("Python(텍스트) + Gemini(테이블) + 목차 연동")
        print("=" * 50)
        print()
        print("사용법: py step1_extract_gemini.py [옵션] <PDF파일경로>")
        print()
        print("옵션:")
        print("  --text-only, -t   텍스트 전용 모드 (테이블 없는 문서용, 빠름)")
        print("  --toc <파일>      목차 파일 경로 (구조 정보 삽입)")
        print("  --pages <지정>    처리할 페이지 (예: 10, 16-30, 1,3,5-10, 20-)")
        print()
        print("페이지 지정 예시:")
        print("  --pages 15        → 1~15페이지")
        print("  --pages 16-30     → 16~30페이지")
        print("  --pages 1,3,5-10  → 1, 3, 5~10페이지")
        print("  --pages 20-       → 20페이지~끝")
        print()
        print("설정:")
        print(f"  - Gemini Model: {GEMINI_MODEL}")
        print(f"  - API Key: {'설정됨 ✅' if GEMINI_API_KEY else '미설정 ❌'}")
        print(f"  - Poppler Path: {POPPLER_PATH or '시스템 기본'}")
        print(f"  - 딜레이: {FREE_TIER_DELAY}초 (무료 티어)")
        print(f"  - 플랫폼: {platform.system()}")
        print()
        print("v3.3 개선사항:")
        print("  - 텍스트 줄바꿈: 문장 종결 감지로 과도한 병합 방지")
        print("  - 테이블 크롭: 아래쪽 패딩 확대 (본문 잘림 방지)")
        print("  - bbox 검증: 비정상 테이블 → 전체 페이지 Gemini 폴백")
        print("  - Gemini 프롬프트: thead/tbody 필수, 행 생략 금지")
        sys.exit(1)
    
    if not os.path.exists(pdf_path):
        print(f"❌ 파일을 찾을 수 없습니다: {pdf_path}")
        sys.exit(1)
    
    with pdfplumber.open(pdf_path) as pdf:
        total_pages = len(pdf.pages)
    
    page_indices = None
    if page_spec:
        page_indices = parse_page_spec(page_spec, total_pages)
        if not page_indices:
            print(f"❌ 유효한 페이지가 없습니다: {page_spec} (총 {total_pages}페이지)")
            sys.exit(1)
        print(f"📋 페이지 지정: {page_spec} → {len(page_indices)}페이지 처리 예정")
    
    section_map = None
    if toc_path:
        if not os.path.exists(toc_path):
            print(f"❌ 목차 파일을 찾을 수 없습니다: {toc_path}")
            sys.exit(1)
        
        if toc_path.endswith('.json'):
            import json
            print(f"📖 목차 JSON 파일 로드 중: {toc_path}")
            with open(toc_path, 'r', encoding='utf-8') as f:
                toc_data = json.load(f)
            section_map = toc_data.get('section_map', {})
            print(f"    ✅ JSON에서 {len(section_map)}개 섹션 정보 로드 완료")
        else:
            print(f"📖 목차 파일 파싱 중: {toc_path}")
            section_map = toc_parser.parse_toc_file(toc_path)
            print(f"    ✅ {len(section_map)}개 페이지에 대한 목차 정보 파싱 완료")

    class Tee:
        def __init__(self, *files):
            self.files = files
        def write(self, obj):
            for f in self.files:
                f.write(obj)
                f.flush()
        def flush(self):
            for f in self.files:
                f.flush()
    
    with open(log_file, "w", encoding="utf-8") as log:
        original_stdout = sys.stdout
        sys.stdout = Tee(sys.stdout, log)
        
        try:
            if text_only_mode:
                print("🚀 텍스트 전용 PDF 추출 시작")
                print(f"   파일: {pdf_path}")
                print(f"   방식: pdfplumber (텍스트 전용)")
                if page_indices:
                    print(f"   페이지: {len(page_indices)}페이지 선택됨")
                if section_map:
                    print(f"   목차: {len(section_map)}개 섹션 매핑")
                print()
                
                md = process_pdf_text_only(pdf_path, section_map=section_map, page_indices=page_indices)
            else:
                print("🚀 하이브리드 PDF 추출 시작")
                print(f"   파일: {pdf_path}")
                print(f"   모델: {GEMINI_MODEL}")
                print(f"   방식: Python(텍스트) + Gemini(테이블)")
                if page_indices:
                    print(f"   페이지: {len(page_indices)}페이지 선택됨")
                if section_map:
                    print(f"   목차: {len(section_map)}개 섹션 매핑")
                print()
                
                md = process_pdf(pdf_path, section_map=section_map, page_indices=page_indices)
            
            if md:
                # 출력 경로 생성: download_file/날짜_원본파일명_페이지범위.md
                from datetime import datetime
                
                pdf_stem = Path(pdf_path).stem
                date_str = datetime.now().strftime("%Y%m%d")
                
                # 페이지 범위 문자열 생성
                if page_indices:
                    page_range_str = f"_p{min(page_indices)+1}-{max(page_indices)+1}"
                else:
                    page_range_str = ""
                
                # download_file 폴더 경로 (python_code 폴더 내)
                script_dir = Path(__file__).parent
                output_dir = script_dir / "download_file"
                output_dir.mkdir(parents=True, exist_ok=True)
                
                # 기본 파일명
                base_name = f"{date_str}_{pdf_stem}{page_range_str}"
                output_path = output_dir / f"{base_name}.md"
                
                # 중복 파일명 처리
                counter = 1
                while output_path.exists():
                    output_path = output_dir / f"{base_name}_{counter}.md"
                    counter += 1
                
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(md)
                
                print()
                print("=" * 50)
                print("✅ 추출 완료!")
                print("=" * 50)
                print(f"📄 출력 파일: {output_path}")
                print(f"📊 파일 크기: {len(md):,} bytes")
                print()
                
                if tracker.call_count > 0:
                    print(tracker.summary())
            else:
                print("❌ 추출 결과가 없습니다.")
                
        except KeyboardInterrupt:
            print("\n⚠️ 사용자에 의해 중단됨")
        except Exception as e:
            logger.error(f"오류 발생: {e}")
            import traceback
            traceback.print_exc()
        finally:
            sys.stdout = original_stdout


if __name__ == "__main__":
    main()
