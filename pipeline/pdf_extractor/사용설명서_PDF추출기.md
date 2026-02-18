# 하이브리드 PDF 추출기 v3.2 사용설명서

## 개요

건설 관련 PDF 문서에서 텍스트와 테이블을 자동 추출하여 마크다운(.md) 파일로 변환하는 스크립트입니다.

- **텍스트**: pdfplumber로 무료 추출
- **테이블**: Gemini Vision API로 이미지 인식 → HTML 테이블 변환
- **목차 연동**: 챕터/섹션 구조 정보 자동 삽입

---

## 사전 준비

### 1. 필수 라이브러리 설치

Antigravity 터미널에서 실행:

```
pip install pdfplumber google-generativeai pdf2image pillow python-dotenv
```

### 2. Poppler 설치 (테이블 이미지 변환에 필요)

1. https://github.com/oschwartz10612/poppler-windows/releases 에서 최신 버전 다운로드
2. 압축 해제 → `C:\poppler\` 폴더에 배치
3. 시스템 환경변수 `POPPLER_PATH`에 bin 폴더 경로 등록 (선택)
   - 예: `C:\poppler\poppler-24.08.0\Library\bin`
   - 등록 안 해도 코드 내 기본 경로에서 자동 탐색함

### 3. API 키 설정

스크립트와 같은 폴더에 `.env` 파일 생성:

```
GEMINI_API_KEY=AIzaSy여기에_본인_키_입력
GEMINI_MODEL=gemini-3-flash-preview
```

---

## 기본 사용법

Antigravity 터미널에서 스크립트가 있는 폴더로 이동 후 실행합니다.

### 전체 페이지 추출 (하이브리드 모드)

```
python step1_extract_gemini.py "C:\작업폴더\건설기준.pdf"
```

테이블이 있는 페이지는 Gemini API를 호출하고, 없는 페이지는 텍스트만 추출합니다.

### 텍스트만 추출 (빠름, API 비용 없음)

```
python step1_extract_gemini.py -t "C:\작업폴더\건설기준.pdf"
```

테이블 인식 없이 텍스트만 빠르게 뽑을 때 사용합니다. Gemini API를 호출하지 않습니다.

---

## 페이지 지정 (`--pages`)

30페이지짜리 PDF를 나눠서 처리하거나, 특정 페이지만 골라서 처리할 수 있습니다.

| 명령어 | 처리 대상 |
|---|---|
| `--pages 15` | 1~15페이지 |
| `--pages 16-30` | 16~30페이지 |
| `--pages 1,3,5-10` | 1, 3, 5~10페이지 |
| `--pages 20-` | 20페이지~끝 |
| `--pages -10` | 1~10페이지 |

### 예시: 큰 PDF를 반으로 나눠서 처리

```
python step1_extract_gemini.py --pages 1-15 "건설기준.pdf"
python step1_extract_gemini.py --pages 16-30 "건설기준.pdf"
```

출력 파일이 자동으로 구분됩니다:
- `건설기준_p1-15_gemini.md`
- `건설기준_p16-30_gemini.md`

### 예시: 특정 페이지만 뽑기

```
python step1_extract_gemini.py --pages 3,7,12-15 "건설기준.pdf"
```

### 예시: 텍스트 전용 + 페이지 지정

```
python step1_extract_gemini.py -t --pages 1-50 "건설기준.pdf"
```

---

## 목차 연동 (`--toc`)

목차 파일을 지정하면 추출 결과에 챕터/섹션 구조 정보가 자동 삽입됩니다.

```
python step1_extract_gemini.py --toc "목차.json" "건설기준.pdf"
```

모든 옵션 조합 가능:

```
python step1_extract_gemini.py -t --toc "목차.json" --pages 16-30 "건설기준.pdf"
```

---

## 출력 결과

| 항목 | 설명 |
|---|---|
| 마크다운 파일 | `파일명_gemini.md` 또는 `파일명_p1-15_gemini.md` |
| 실행 로그 | `step1_log.txt` (스크립트 폴더에 생성) |

실행 완료 시 콘솔에 토큰 사용량과 예상 비용이 표시됩니다.

---

## 옵션 요약

```
python step1_extract_gemini.py [옵션] <PDF파일경로>

옵션:
  -t, --text-only     텍스트만 추출 (빠름, API 비용 없음)
  --toc <파일경로>     목차 파일 (.json 또는 .txt)
  --pages <지정>       처리할 페이지 (예: 16-30, 1,3,5-10)
```

---

## 참고사항

- Gemini 무료 티어는 분당 15회 호출 제한이 있어서 테이블이 많으면 시간이 걸립니다 (테이블당 약 4초 대기).
- 텍스트 전용 모드(`-t`)는 API를 사용하지 않으므로 빠르고 무료입니다. 테이블이 없는 문서라면 이 모드를 추천합니다.
- 할당량 초과(429 에러) 시 자동으로 60초 대기 후 재시도합니다.
- `Ctrl+C`로 중간에 중단할 수 있습니다.
