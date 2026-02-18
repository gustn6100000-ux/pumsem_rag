# python_code 폴더 정리 결과

> 작성일: 2026-02-10
> 목적: 루트에 뒤섞여 있던 15개 파일을 성격별 6개 폴더로 분류

---

## 변경 요약

| 작업      | 내용                                                         |
| --------- | ------------------------------------------------------------ |
| 폴더 생성 | `pdf_extractor`, `toc_parser`, `analysis`, `docs`, `logs`    |
| 파일 이동 | 20개 파일을 성격별 폴더로 분류                               |
| 삭제      | `step1_extract_gemini_v33 copy.py`, `이전md파일/` 폴더       |
| 루트 파일 | **15개 → 2개** (`.env`, `key.json`만 잔류)                   |
| 경로 수정 | `config.py`의 `TOC_FILE` 경로 → `toc_parser/toc_parsed.json` |

---

## 정리 후 최종 구조

```
python_code/
├── .env                          # API 키 설정
├── key.json                      # Google 인증키
│
├── pdf_extractor/                # PDF → MD 추출 도구 (3개)
│   ├── step1_extract_gemini_v33.py
│   ├── step1_gui.py
│   └── 사용설명서_PDF추출기.md
│
├── toc_parser/                   # 목차(TOC) 파싱 도구 (5개)
│   ├── toc_parser.py
│   ├── reparse_toc.py
│   ├── analyze_toc.py
│   ├── toc_parsed.json           # 1,284개 섹션 매핑 데이터
│   └── 목차_gemini.md            # 원본 목차 (274KB)
│
├── phase1_preprocessing/         # 전처리 파이프라인 코드 (10개)
│   ├── config.py
│   ├── run_pipeline.py
│   ├── step1_section_splitter.py
│   ├── step2_table_parser.py
│   ├── step3_text_cleaner.py
│   ├── step4_chunker.py
│   ├── step5_validator.py
│   ├── __init__.py
│   └── utils/
│       ├── html_utils.py
│       └── token_counter.py
│
├── phase1_output/                # 파이프라인 출력물 (5개)
│   ├── raw_sections.json
│   ├── parsed_tables.json
│   ├── cleaned_sections.json
│   ├── chunks.json               # 최종 청크 (1,922개)
│   └── quality_report.json
│
├── download_file/                # MD 원본 파일 (41개)
│
├── analysis/                     # 분석/검증 스크립트 (5개)
│   ├── analyze_chunks.py
│   ├── sampling_check.py
│   ├── analyze_result.txt
│   ├── sampling_result.txt
│   └── pipeline_run_log.txt
│
├── docs/                         # 프로젝트 문서 (4개→5개)
│   ├── 20260209_Phase1_전처리파이프라인_구현계획.md
│   ├── 20260210_Phase1_남은이슈_분석보고서.md
│   ├── 20260210_python_code_폴더정리_결과.md  ← 본 문서
│   ├── GraphRAG_검토보고서.md
│   └── 명령어_레퍼런스.md
│
├── logs/                         # 실행 로그 (3개)
│   ├── debug_gemini_log.txt
│   ├── pdf_gui_log_20260208_135703.txt
│   └── step1_log.txt
│
└── __pycache__/                  # Python 캐시 (보관)
```

---

## 폴더별 역할

| 폴더                    | 역할                                       | 파일 수 |
| ----------------------- | ------------------------------------------ | ------- |
| `pdf_extractor/`        | PDF를 MD로 추출하는 Gemini 기반 도구       | 3       |
| `toc_parser/`           | 목차(TOC)를 파싱하여 섹션 매핑 데이터 생성 | 5       |
| `phase1_preprocessing/` | 전처리 파이프라인 (Step 1~5)               | 10      |
| `phase1_output/`        | 파이프라인 실행 결과물 (JSON)              | 5       |
| `download_file/`        | PDF에서 추출한 MD 원본 파일                | 41      |
| `analysis/`             | 청크 분석 및 샘플링 검증 스크립트          | 5       |
| `docs/`                 | 구현계획서, 보고서, 레퍼런스 문서          | 5       |
| `logs/`                 | PDF 추출/파이프라인 실행 로그              | 3       |

---

## 삭제된 항목

| 항목                               | 사유                                                                                            |
| ---------------------------------- | ----------------------------------------------------------------------------------------------- |
| `step1_extract_gemini_v33 copy.py` | 원본과 동일한 복사본                                                                            |
| `이전md파일/`                      | 내용물(`사용설명서`, `명령어_레퍼런스`)을 각각 `pdf_extractor/`, `docs/`로 이동 후 빈 폴더 삭제 |
| `구현계획서파일/`                  | 내용물을 `docs/`로 이동 후 빈 폴더 삭제                                                         |
| `log파일/`                         | 내용물을 `logs/`로 이동 후 빈 폴더 삭제                                                         |
