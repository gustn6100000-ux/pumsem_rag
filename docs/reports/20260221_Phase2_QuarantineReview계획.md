# Phase 2: Quarantine Review (DLQ 재평가) 구현 계획 및 전략 보고서

**작성일**: 2026-02-21
**문서 내용**: Phase 1.5 Strict Validation에서 Mismatch로 격리(DLQ)된 393건의 데이터를 대상으로, 문맥적 의미를 파악하는 느슨한 검증(Relaxed Validation)을 수행하여 유효 데이터를 구제하고 완전한 환각(Hallucination) 데이터를 폐기하기 위한 파이프라인 구축 기획안.

---

## 1. 배경 및 DLQ Mismatch 원인 분석

Phase 1.5의 독립 교차 검증은 단어(Token) 수준의 1:1 매칭 알고리즘 기반으로 엄격하게 진행되었으며, 이 과정에서 393건의 데이터가 Dead Letter Queue(DLQ)로 편입되었습니다. 

DLQ에 적재된 실패 엔티티들의 최다 빈출 목록 및 원인을 스크립트로 상세 분석한 결과, 아래 두 가지 주요 케이스로 나뉘는 것을 확인했습니다.

### 1.1. 추론 성공 / 텍스트 부족 (False-Negative 확률 ⬆️)
- **대표 엔티티**: `특별인부(24건)`, `배관공(16건)`, `크러셔(이동식)(11건)` 등
- **발생 기전**: 원본 PDF의 표(Table) 병합 셀 구조상 텍스트 파편화가 발생하여 헤더나 속성명에서 해당 명사가 물리적으로 누락된 경우입니다. 그러나 LLM은 문맥(테이블 구조)을 올바르게 파악하여 장비/인력을 적절히 추론(Inference)해 냈습니다. 
- **결론**: 의미상 올바른 정답이므로 이를 엄격한 토큰 매칭으로 매몰시키지 않고 반드시 **구제(Recovery)** 해야 합니다.

### 1.2. 요약 및 환각 현상 (True-Positive DLQ 확률 ⬆️)
- **대표 엔티티**: `국토교통부장관 고시 측량용역대가기준(12건)`, `공공측량 작업규정(11건)` 등
- **발생 기전**: 청크 내 여러 문장에 흩어져 있는 단어들을 LLM이 결합하여 하나의 새로운 개념명으로 재창조하거나 길게 요약(Summarization/Fabrication)한 경우입니다.
- **결론**: 단어 융합형 환각 현상은 향후 Graph DB에 적재되었을 때 중복되거나 오염된 노드(Node Fragmentation)를 만들어 쿼리의 질을 하락시킵니다. 따라서 전문가 검토가 필요하거나 최종적으로 **폐기(Discard)** 해야 합니다.

단어 수준 매칭 알고리즘은 상기 "문맥적 추론" 과 "요약/재구성" 의 경계를 구분할 수 없기 때문에, AI의 인간 수준 추론 능력을 모방한 새로운 평가 체계가 요구됩니다.

---

## 2. 해결 방안 (LLM-as-a-Judge 모델 도입)

짧은 단어(예: "배관공")와 상대적으로 긴 텍스트 덩어리의 상관관계를 임베딩(Vector Similarity) 기반으로 수학적 유사도를 측정하는 것은 오탐률이 높을 수 있습니다.

따라서 **지시문 프롬프팅 기반의 LLM-as-a-Judge(LLM 평가자) 방식**이 가장 빠르고 정확한 판별 기준이 됩니다.

비용 효율과 빠른 처리 속도 강점을 지닌 모델(`gemini-2.5-flash` 혹은 `deepseek-chat`)을 활용하여, "이 엔티티가 원본 텍스트/표에서 논리적으로 도출될 수 있는 정당한 정보인지" 이진 분류(True/False 분류)를 즉각 수행합니다.

### 2.1. 신규 컴포넌트 개발: `step2_5_quarantine_review.py`
Phase 2와 3을 잇는 브릿지(Bridge) 스크립트로 아래 로직의 컴포넌트를 신규 개발합니다.

- **입력 (Input)**: `pipeline/phase1_5_validation/DLQ_entities.json` (393건)
- **동작 (Logic)**:
  1. 비동기(Async) 병렬 처리 기법으로 LLM Evaluator API(DeepSeek-V3 또는 Gemini)를 다중 호출하여 속도를 최적화합니다.
  2. **System Prompt**: "제공된 원본 텍스트 및 표 데이터를 바탕으로, 주어진 엔티티(이름, 규격, 수량)가 원본에서 논리적/문맥적으로 도출될 수 있는지 판별해라. 직접적인 단어가 없더라도 해당 도메인 표 구조상 필수적으로 유추되는 값이라면 True로 인정하라."
  3. **Output Format**: 구조화된 JSON 응답 요구 (`{"is_valid": true/false, "confidence": 0.0~1.0, "reason": "상세한 판별 사유..."}`)
- **출력 (Output 분기)**:
  - `phase1_5_validation/recovered_entities.json`: LLM 재평가 결과 `is_valid == true` 판정을 받은 합격 데이터 풀(Pool).
  - `phase1_5_validation/discarded_entities.json`: 재평가 결과 `is_valid == false` 판정을 받아 최종적으로 버려지는 폐기 데이터 풀.

---

## 3. 작동 검증 및 향후 진행 절차 파이프라인

### 3.1. 자동분석/수동검수 (Manual Verification Loop)
- 초기 스크립트 작성 완료 후 전수 조사를 피하고 부분 데이터(`--sample 10` 옵션)를 추출하여 터미널 환경에서 시범 구동합니다.
- 평가 프롬프트가 `is_valid` 를 어떻게 분류했는지 `reason` 필드를 개발자가 직접 출력 로그로 확인합니다.
- 인간(사용자/개발자)의 판단과 LLM-as-a-Judge의 판별 기준이 상호 일치하는지 비교하고, 필요시 System Prompt를 튜닝(Fine-Tuning)합니다.

### 3.2. 통합 및 Phase 3 진입
- 프롬프트의 정확성이 수동 검수로 확보되었다면, 393건 전체를 대상으로 파이프라인을 일괄 구동합니다.
- 평가를 통해 구제 처리된 `recovered_entities.json` 데이터 셋을 생성합니다.
- 즉각, 이전 단계에서 100% 매칭에 성공했던 기초 데이터 풀인 `validated_entities.json`(1,323건)과 `recovered_entities.json`을 단일 JSON으로 최종 병합(Merge)합니다.
- 이 완전 결합된 마스터 추출물(Master Extracts)을 Base로 하여, 최종 수량계산 및 그래프 적재 컴포넌트 (`step6_supabase_loader.py`)에 입력으로 주입하며 프로젝트 3막(Phase 3)을 개시합니다.
