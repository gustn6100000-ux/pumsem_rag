# 의도 분석 개선 — Phase 1+2 구현 완료

> 작성일: 2026-02-15  
> 범위: Phase 1(세션 상태 관리) + Phase 2(Intent 세분화)

## 변경 파일 요약

| 파일       | 주요 변경                                                       |
| ---------- | --------------------------------------------------------------- |
| types.ts   | `SessionContext`, `IntentAnalysis` 확장, `SourceInfo.entity_id` |
| index.ts   | 프롬프트 확장, session 주입, 3개 intent 분기                    |
| index.html | sessionContext 전역 상태 + 자동 갱신                            |

## Phase 1: 세션 상태 관리

**문제**: "아까 건", "그거 말고" 같은 맥락 참조 불가  
**해결**: `SessionContext` 도입 → 프론트엔드에서 매 응답 후 entity_id/work_name/spec 추적 → 서버에 전달

## Phase 2: Intent 세분화 (5→8)

| 새 Intent        | 트리거 예시                | 동작                           |
| ---------------- | -------------------------- | ------------------------------ |
| `cost_calculate` | "노무비 계산해줘"          | session의 entity로 노무비 산출 |
| `modify_request` | "50m로 바꿔", "TIG로 해줘" | 수량/공종/직종 변경 후 재산출  |
| `report_request` | "산출서 만들어줘"          | 정형 산출 내역서 생성          |
