# 품셈 RAG 프로젝트 폴더 구조

> 최종 업데이트: 2026-03-12
> 기준 문서: `pipeline/docs/execution_report_20260311.md` / `remediation_action_plan_20260312.md`

---

## 루트 (`/`)

```
PUMSEM/
├── .env                        # 환경변수 (Supabase URL/KEY 등)
├── .gitignore
├── deploy_api.py               # Edge Function 배포 스크립트
├── deploy_chat.bat             # 배포 실행 배치파일
├── deploy_payload.json         # 배포 페이로드 설정
├── deploy_payload.py           # 배포 페이로드 생성기
├── PROJECT_STRUCTURE.md        # ← 이 파일
│
├── _archive/                   # 불필요 파일 보관 (실행 금지)
├── db/                         # DB 마이그레이션
├── docs/                       # 프로젝트 문서
├── edge-function/              # (supabase/functions 미러)
├── frontend/                   # 프론트엔드
├── pipeline/                   # 데이터 처리 파이프라인
└── supabase/                   # Supabase Edge Functions
```

---

## `_archive/` — 보관 폴더 (40개 파일)

```
_archive/
├── root_files/       # 루트에서 정리된 테스트/임시 파일 (15개)
├── pipeline_files/   # pipeline/ 루트에서 정리된 파일 (4개)
└── scripts/          # 대체되거나 일회성으로 사용된 스크립트 (21개)
```

---

## `db/` — 데이터베이스

```
db/
└── migrations/       # Supabase 마이그레이션 SQL 파일
```

---

## `docs/` — 프로젝트 문서

```
docs/
├── INDEX.md                        # 문서 인덱스
├── algorithm-guide.html            # 알고리즘 가이드 (HTML)
├── audit_v2_issues_report.md       # 전수감사 v2 이슈 보고서 ★
├── data_quality_final_report.md    # 데이터 품질 최종 보고서 ★
│
├── archive/          # 완료된 구 문서 (37개)
├── plans/            # 구현 계획서 (100개+)
├── reports/          # 결과 보고서 (80개+)
├── reviews/          # 코드 리뷰/검토 문서 (4개)
└── test-data/        # 테스트용 JSON 샘플 (8개)
```

---

## `frontend/` — 프론트엔드

```
frontend/
├── index.html
├── app.js
└── style.css
```

---

## `supabase/` — Edge Functions (서빙 레이어)

```
supabase/
└── functions/
    └── rag-chat/               # RAG 챗봇 메인 Edge Function
        ├── index.ts            # 진입점, 요청 라우팅
        ├── graph.ts            # 그래프 탐색 파이프라인
        ├── search.ts           # 벡터/키워드 검색
        ├── clarify.ts          # 질의 명확화 처리
        ├── resolve.ts          # 의도 분석 및 쿼리 확장
        ├── llm.ts              # LLM 호출 (Claude/DeepSeek)
        ├── context.ts          # 컨텍스트 구성
        ├── embedding.ts        # 임베딩 생성
        ├── config.ts           # 설정값
        ├── types.ts            # 타입 정의
        └── seed_13_1_1.ts      # 특정 섹션 시드 데이터
```

> `edge-function/`은 위 `supabase/functions/rag-chat/`과 동일한 내용의 미러 폴더

---

## `pipeline/` — 데이터 처리 파이프라인

### 핵심 구조

```
pipeline/
├── .env                        # 파이프라인 환경변수
├── key.json                    # API 키 파일
│
├── docs/                       # ★ 최신 작업 문서
│   ├── execution_report_20260311.md          # 전수 교차검증 실행 보고서
│   └── remediation_action_plan_20260312.md   # 향후 개선 실행 계획서
│
├── download_file/              # 원본 품셈 MD 파일 (43개)
├── logs/                       # 파이프라인 실행 로그
│
├── pdf_extractor/              # PDF → MD 추출 도구
│   ├── step1_extract_gemini_v33.py
│   ├── step1_gui.py
│   └── 사용설명서_PDF추출기.md
│
├── toc_parser/                 # 목차 파싱
│   ├── toc_parser.py
│   ├── reparse_toc.py
│   ├── analyze_toc.py
│   ├── toc_parsed.json
│   └── 목차_gemini.md
│
├── analysis/                   # 파이프라인 분석 유틸
│   ├── analyze_chunks.py
│   ├── sampling_check.py
│   ├── analyze_result.txt
│   ├── pipeline_run_log.txt
│   └── sampling_result.txt
│
├── sql/                        # DB 구축/검색함수 SQL
│   ├── step2.6_create_graph_rag_tables.sql
│   ├── step2.6_insert_ilwi_items.sql
│   ├── step2.6_verify_counts.sql
│   ├── step2.7_bulk_update_embeddings.sql
│   ├── step2.7_create_search_functions.sql
│   └── step2.7_verification_queries.sql
│
├── phase1_output/              # Phase 1 처리 결과물
│   ├── raw_sections.json       # 섹션 분할 결과 (정본)
│   ├── chunks.json             # 청킹 결과 ★ DB 복구 안전망
│   ├── cleaned_sections.json
│   ├── parsed_tables.json
│   ├── quality_report.json
│   └── deep_check_report.json
│
├── phase1_5_validation/        # Phase 1.5 엔티티 검증
│   ├── validated_entities.json
│   ├── discarded_entities.json
│   ├── recovered_entities.json
│   ├── DLQ_entities.json
│   └── validation_report.json
│
├── phase1_preprocessing/       # Phase 1 전처리 스크립트
│   ├── run_pipeline.py         # 파이프라인 실행 진입점
│   ├── step1_section_splitter.py
│   ├── step2_table_parser.py
│   ├── step3_text_cleaner.py
│   ├── step4_chunker.py        # → chunks.json 생성 (백업 로직 추가 예정)
│   ├── step5_validator.py
│   ├── config.py
│   └── utils/
│       ├── html_utils.py
│       └── token_counter.py
│
├── phase2_extraction/          # Phase 2 엔티티·관계 추출 스크립트
│   ├── step1_table_extractor.py
│   ├── step2_llm_extractor.py  # LLM 기반 추출
│   ├── step2_5_quarantine_review.py
│   ├── step2_8_merge_master.py
│   ├── step3_relation_builder.py
│   ├── step4_normalizer.py
│   ├── step5_extraction_validator.py
│   ├── step6_supabase_loader.py  # DB 적재 → 이후 validate_chunks_v2 실행 예정
│   ├── step7_embedding_generator.py
│   ├── schemas.py
│   ├── config.py
│   ├── check_sub.py
│   ├── prompts/
│   └── _analysis/              # 분석용 일회성 스크립트 (25개)
│
├── phase2_output/              # Phase 2 처리 결과물
│   ├── llm_entities_master.json  # 마스터 엔티티 (정본)
│   ├── llm_entities.json
│   ├── merged_entities.json
│   ├── normalized_entities.json
│   ├── table_entities.json
│   ├── extraction_report.json
│   ├── logs/                   # step6(적재), step7(임베딩) 실행 로그 (50개+)
│   └── backup_YYYYMMDD_HHMM/   # 버전별 백업 (5개)
│
└── scripts/                    # ★ 현재 진행 중인 데이터 정합성 스크립트
    ├── validate_chunks.py        # 전수감사 v1 (section_id 기준)
    ├── validate_chunks_v2.py     # 전수감사 v2 (base_id 정규화) ← 자동화 연동 예정
    ├── restore_dedup_tables.py   # dedup 오삭제 복구 (383 청크 복구 완료)
    ├── fix_missing_tables.py     # Phase1 누락 테이블 보충 (잔여 100건 대응용)
    ├── dedup_tables.py           # ⚠️ 실행 금지 — SHA-256 로직으로 재작성 예정
    └── output/                   # 감사 결과
        ├── audit_report.json       # v1 감사 결과
        ├── audit_v2_report.json    # v2 감사 결과 (최신)
        ├── audit_details.csv       # v1 상세 데이터
        ├── audit_v2_details.csv    # v2 상세 데이터 (최신)
        ├── fix_results.json        # fix_missing_tables 실행 결과
        └── issues_tables.md        # 이슈 테이블 목록
```

---

## 현재 진행 상태 (2026-03-12 기준)

| 항목 | 상태 | 담당 파일 |
|------|------|-----------|
| DB 데이터 복구 (383 청크) | ✅ 완료 | `restore_dedup_tables.py` |
| Phase1 누락 보충 (38건) | ✅ 완료 | `fix_missing_tables.py` |
| 데이터 커버리지 | 96.8% (잔여 100건) | `audit_v2_report.json` |
| `dedup_tables.py` 재작성 | 🔲 예정 | SHA-256 시그니처 로직 |
| `chunks.json` 자동 백업 | 🔲 예정 | `step4_chunker.py` 수정 |
| 잔여 100건 파싱 복구 | 🔲 예정 | 섹션별 엣지케이스 파서 |
| 정기 감사 자동화 | 🔲 예정 | `step6` 후 `validate_chunks_v2` 연동 |
