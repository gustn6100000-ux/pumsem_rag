# Track A: Matrix Unroll 구현 및 검증 결과

> **일시**: 2026-02-19 20:27 KST  
> **대상 파일**: `pipeline/phase2_extraction/step2_llm_extractor.py`  
> **관련 기술서**: [20260219_TrackA_MatrixUnroll_구현기술서.md](file:///G:/My%20Drive/Antigravity/docs/plans/20260219_TrackA_MatrixUnroll_구현기술서.md)

---

## 1. 구현 Patch 요약

| Patch | 라인     | 내용                                                                                            | 상태 |
| ----- | -------- | ----------------------------------------------------------------------------------------------- | ---- |
| 1     | L67~92   | `LLMRelationship.properties: Optional[dict]` + `LLMExtractionResult.matrix_analysis_scratchpad` | ✅    |
| 2     | L114~199 | 매트릭스 전개 규칙 7~9 + JSON 스키마 보강 + PE관 Few-shot 예시 추가                             | ✅    |
| 3     | L290     | `max_tokens=8192` (DeepSeek API 허용 최대 상한)                                                 | ✅    |
| 4     | L340     | `properties=lr.properties if lr.properties else {}` — Silent Drop 방지                          | ✅    |

---

## 2. 기술서 수정 (DeepSeek API 공식 문서 검증)

DeepSeek-V3 공식 문서([Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing)) 검증 결과:

| 항목           | 공식 스펙             |
| -------------- | --------------------- |
| Context Length | 128K                  |
| Default Output | **4,096**             |
| Max Output     | **8,192** (절대 상한) |

기술서 수정 4건:
- §1.2: API 최대 상한 8,192 명시
- §6.3: `65,536` → `8,192` + 청크 분할 전략 추가
- §8.2: `"총 9개 지점"` → `"step3(5개) + step4(4개) = 총 9개 지점"` 명확화
- §10.1: `max_tokens=16384 상향` → 불가능 → 청크 규격 분할 2회 호출

---

## 3. 테스트 결과: 3/3 PASS ✅

### 🅰️ Test A: Pydantic 스키마 호환성 ✅
- 새 필드(properties, scratchpad) 포함 JSON 파싱 성공 (2/2)

### 🅱️ Test B: 하위 호환성 ✅
- 기존 형식(properties/scratchpad 없음) 파싱 + `schemas.py` 연동 정상 (5/5)

### 🅲 Test C: LLM API 실제 호출 ✅

PE관 매트릭스(5규격 × 2직종) DeepSeek-V3 실제 호출:

| 검증 항목          | 기대                 | 실제                                            | 결과 |
| ------------------ | -------------------- | ----------------------------------------------- | ---- |
| CoT 발동           | scratchpad 기록      | `"5개 규격 × 2개 직종 = 10개 관계. 모두 전개."` | ✅    |
| 200mm 생존         | 배관공/특별인부 존재 | 배관공 0.521 + 특별인부 0.113                   | ✅    |
| source_spec 무결성 | 10개 모두 spec 보유  | 전원 보유                                       | ✅    |
| 총 관계 수         | 10                   | **10**                                          | ✅    |
| 수량 정확성        | 10/10                | **10/10**                                       | ✅    |

LLM 원시 응답 (발췌):
```json
{
  "matrix_analysis_scratchpad": "5개 규격(63mm, 75mm, 100mm, 150mm, 200mm) × 2개 직종(배관공, 특별인부) = 10개 관계. 모두 전개.",
  "relationships": [
    {"source": "PE관 접합 및 부설", "target": "배관공", "quantity": 0.184, "properties": {"source_spec": "63mm"}},
    {"source": "PE관 접합 및 부설", "target": "배관공", "quantity": 0.521, "properties": {"source_spec": "200mm"}},
    {"source": "PE관 접합 및 부설", "target": "특별인부", "quantity": 0.052, "properties": {"source_spec": "63mm"}},
    {"source": "PE관 접합 및 부설", "target": "특별인부", "quantity": 0.113, "properties": {"source_spec": "200mm"}}
  ],
  "confidence": 0.95
}
```

> **전체 원시 JSON**: `C:\Users\lhs\test_track_a_result.json`
