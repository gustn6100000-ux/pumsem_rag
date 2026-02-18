# 3차 분류 Sub Section Drill-Down 전체 테스트 결과

> 작성일: 2026-02-15 | Edge Function: v69 (110.4kB)

## 1. DB 섹션별 sub_section 현황

| section_id | Section 이름 (DB)     | sub 수 | WT 총 수 | 검색어 매칭      |
| ---------- | --------------------- | ------ | -------- | ---------------- |
| 13-2-3     | 강관용접              | 2      | 122      | "강관용접" ✅     |
| 13-2-4     | **강판 전기아크용접** | 5      | 124      | "강판용접" ❌     |
| 13-1-5     | Flange 취부           | 3      | 180      | "Flange 취부" ✅  |
| 13-1-3     | 밸브 취부             | 2      | 52       | "밸브 취부" ✅    |
| 13-1-4     | Fitting 취부          | 2      | 34       | "Fitting 취부" ⚠️ |
| 13-2-2     | 강판절단              | 0      | —        | 대상 외          |
| 13-1-2     | 관만곡(Pipe Bending)  | 0      | —        | 대상 외          |

## 2. API 테스트 결과 (section_id 없이 직접 검색)

### ✅ 강관용접 — 정상
```
SELECTOR: False / OPTIONS: 3
📋 강관용접 전체 내용 보기 [full_view]
📂 1. 전기아크용접 (121건) [section]
📂 2. TIG용접 (1건) [section]
```

### ❌ 강판용접 — 검색 실패
```
TYPE: clarify / OPTIONS: 0
→ "강판용접 Plate Welding" 관련 품셈 항목을 찾지 못했습니다.
```
**원인**: Section 이름이 "강판 **전기아크**용접"이라 ILIKE `%강판용접%` 패턴 불일치

### ⚠️ 강판 전기아크용접 — API는 정상 / 브라우저는 불안정
```
API 결과 (CLI):
📋 강판 전기아크용접 전체 내용 보기 [full_view]
📂 1. 전기아크용접(V형) (33건) [section]
📂 2. 전기아크용접(U형) (15건) [section]
📂 2/3. U형·H형(공통) (48건) [section]
📂 4. 전기아크용접(X형) (22건) [section]
📂 5. 전기아크용접(Fillet) (6건) [section]
```
**문제**: 브라우저에서는 flat WT chips(개별 WorkType 나열)가 표시됨.
intent 분석(DeepSeek)이 동일 쿼리에 다른 work_name/keywords를 반환하여 코드 경로가 달라지는 비결정적 이슈.

### ✅ Flange 취부 — 정상
```
SELECTOR: False / OPTIONS: 4
📋 Flange 취부 전체 내용 보기 [full_view]
📂 1. Screwed Type (37건) [section]
📂 2. Seal Welded Screwed Type (131건) [section]
📂 3. Slip-on Type (19건) [section]
```

### ⚠️ 밸브 취부 — 다른 섹션 sub 혼입
```
SELECTOR: False / OPTIONS: 5
📋 밸브 취부 전체 내용 보기 [full_view]
📂 1. Screwed Type (51건) [section]
📂 2. Welder-Back Screwed Type (1건) [section]
📂 2. Butt Welding Type (1건) ← 13-1-4 혼입?
📂 2. Seal Welded Screwed Type (2건) ← 13-1-5 혼입?
```

### ⚠️ Fitting 취부 — sub_section 미작동
```
SELECTOR: False / OPTIONS: 11
📋 Fitting 취부 전체 내용 보기 [full_view]
+ 13-1-1 배관 WorkType 10개 혼입 → sub drill-down 미작동
```
**원인**: "Fitting" 키워드로 13-1-1(배관 설치 WT)이 대량 혼입

## 3. 미해결 이슈

### 이슈 A: "강판용접" 검색 실패 (Critical)
- **원인**: Section `%강판용접%` ILIKE가 "강판 전기아크용접"과 불일치
- **해결 방안**: 
  - (1) Section의 `korean_alias`에 "강판용접" 추가
  - (2) 검색 전략에서 키워드 분리 매칭 추가 (`%강판%용접%`)
  - (3) graph_entities Section에 korean_alias 속성 활용

### 이슈 B: intent 분석 비결정성
- **원인**: DeepSeek가 동일 쿼리에 다른 work_name/keywords 반환
- **파급**: 같은 쿼리라도 코드 경로가 달라져 결과 불안정
- **해결 방안**: intent 분석 후 Section 매칭을 강제하는 후처리 로직

### 이슈 C: 다른 섹션 WT 혼입
- **해당**: 밸브 취부, Fitting 취부
- **원인**: Step 1 전략 2/3에서 공통 키워드("취부", "Screwed")로 다른 섹션 WT 매칭
- **해결 방안**: sub_section 체크 시 `source_section` 동일 여부로 필터링
