# Walkthrough — 품셈 데이터 1·2·3차 분류 재구조화

> 완료일: 2026-02-15

## 변경 요약

품셈 데이터의 계층 구조(1차: 절 → 2차: 하위절/공법 → 3차: 규격)를 DB + Edge Function에 반영하여, 챗봇이 **Section → SubSection → WorkType** 3단계 drill-down을 제공하도록 개선.

---

## Phase 1: DB 마이그레이션

7개 섹션 **638개 WorkType**의 `properties.sub_section` 속성 추가. 미분류 0건.

| 섹션            | WT  | sub_section 분류                                  |
| --------------- | --- | ------------------------------------------------- |
| 13-2-3 강관용접 | 122 | 전기아크(121), TIG(1)                             |
| 13-2-4 강판용접 | 124 | V형(33), U형(15), U·H공통(48), X형(22), Fillet(6) |
| 13-1-5 Flange   | 180 | Screwed(30), Seal Welded(131), Slip-on(19)        |
| 13-1-3 밸브     | 52  | Screwed(51), Welder-Back(1)                       |
| 13-1-4 Fitting  | 34  | Screwed(25), Butt Welding(9)                      |
| 13-5-13 Pump    | 26  | Turbine driven(26)                                |
| 13-2-6 응력제거 | 100 | Induction(98), Gas Heating(1), 예열(1)            |

> 보고서: [Phase1_SubSection_DB마이그레이션_결과보고서.md](file:///g:/My%20Drive/Antigravity/docs/reports/20260215_Phase1_SubSection_DB마이그레이션_결과보고서.md)

---

## Phase 2: 코드 수정

### [clarify.ts](file:///g:/My%20Drive/Antigravity/edge-function/clarify.ts) — Step 2 sub_section drill-down

- `:sub=` 접미사를 `section_id`에 추가하여 sub_section 필터 전달
- distinct sub_section ≥ 2일 때 자동으로 sub_section 선택 단계 삽입
- sub_section 선택 후 해당 WT만 필터링하여 표시

render_diffs(file:///g:/My%20Drive/Antigravity/edge-function/clarify.ts)

### [graph.ts](file:///g:/My%20Drive/Antigravity/edge-function/graph.ts) — sub_section 그룹 라벨

- `expandSectionWorkTypes`에서 `[sub_section]` 접두사를 `work_type_name`에 주입
- context.ts 출력 시 자동으로 그룹별 라벨 표시

render_diffs(file:///g:/My%20Drive/Antigravity/edge-function/graph.ts)

---

## Phase 3: 검증 (E2E 테스트)

| #   | 테스트              | 입력                    | 결과                               |
| --- | ------------------- | ----------------------- | ---------------------------------- |
| 1   | 강관용접 drill-down | `section_id=13-2-3`     | ✅ 전기아크/TIG 선택지 표시         |
| 2   | 강판용접 drill-down | `section_id=13-2-4`     | ✅ V/U/H/X/Fillet 선택지 표시       |
| 3   | Flange drill-down   | `section_id=13-1-5`     | ✅ Screwed/Seal Welded/Slip-on 표시 |
| 4   | sub_section 필터    | `13-2-3:sub=2. TIG용접` | ✅ TIG 1건만 필터링                 |
