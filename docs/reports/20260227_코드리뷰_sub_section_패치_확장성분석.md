# [코드리뷰] 13-2-4 강판 전기아크용접 sub_section 주입 — 방법론 및 확장성 분석

> **작성일**: 2026-02-27  
> **대상**: `graph_entities` 테이블 WorkType 엔티티 130건  
> **결과**: null 130건 → 5개 분류 완전 매핑 (null 0건)

---

## 1. 방법론 상세 분석

### Step ①-a: 청크 원문 역추적 (읽기 전용 조사)

**실행한 쿼리**:
```sql
SELECT id, LEFT(text, 200) AS text_preview,
       tables->0->>'table_id' AS table_id,
       tables->0->'headers' AS headers
FROM graph_chunks WHERE section_id = '13-2-4';
```

**핵심 발견**: `table_id` 컬럼에 **소제목 번호가 인코딩**되어 있었음

| table_id 패턴 | 의미 |
|---|---|
| `T-13-2-4-**01**-1` ~ `01-4` | **1번** 소제목 = V형 |
| `T-13-2-4-**02**-2` ~ `02-3` | **2번** 소제목 = U형 |
| `T-13-2-4-**03**-1` ~ `03-3` | **3번** 소제목 = H형 |
| `T-13-2-4-**04**-1` ~ `04-2` | **4번** 소제목 = X형 |
| `T-13-2-4-**05**-1` ~ `05-3` | **5번** 소제목 = Fillet |

이 번호 체계는 파이프라인 Step 1(`step1_table_extractor.py`)에서 PDF 파싱 시 자동 부여한 것으로, **소제목 순서와 1:1 대응**합니다.

또한, **표 헤더 패턴**으로도 교차 검증 가능:

| 용접형 | 헤더 구분 | 특성 |
|---|---|---|
| V형 | `용접봉사용량(kg)` + `인 력(인)` + `소요전력(kWh)` | 하향/횡향/입향 **3방향** |
| U형 | `용접봉소비량(kg)` + `하향한면용접(인)` + `하향양면용접(인)` | **한면/양면** 2분류 |
| H형 | U형과 동일 패턴 | 두께 범위로 구분 |
| X형 | `용접봉소비량(kg)` + `인력(인)` + `전력소비량(kWh)` | **3방향** (V형과 컬럼명 미세 차이) |
| Fillet | `소요전력(kWh)` | **4방향** (상향 포함) |

---

### Step ①-b: 파이프라인 실패 원인 특정

**결론**: Codex가 분석한 **실패 원인 B+C 복합** 확정

```
파이프라인 흐름:
  PDF → step1 (표 추출) → step2 (LLM 엔티티 추출) → step3 (관계 구축) → DB

  step1: table_id에 소제목 번호 정상 인코딩 ✅
  step2: LLM이 WorkType name을 "강판 전기아크용접(두께, SCH 항목)" 형태로만 생성
         → "이 표가 어느 소제목(V형/U형) 아래인지" 메타를 name에 태깅하는 로직 미구현 ❌
  step3: properties.sub_section 필드에 값을 write하는 코드 자체가 없음 ❌
```

**근본 원인**: `step2_entity_extractor.py`의 LLM 프롬프트가 표의 **데이터(행/열)만 추출**하도록 설계되어 있고, 표가 속한 **소제목 컨텍스트**를 프롬프트에 주입하지 않음.

---

### Step ①-c: SQL 패치 전략 (핵심)

**사용한 매핑 키**: `graph_relationships.source_chunk_id`

```sql
-- 전략: chunk_id → 용접형 분류 → entity sub_section UPDATE
-- chunk A~D  = 01-* = V형
-- chunk E~F  = 02-* = U형
-- chunk G~I  = 03-* = H형
-- chunk J~K  = 04-* = X형
-- chunk L~N  = 05-* = Fillet

UPDATE graph_entities
SET properties = properties || '{"sub_section": "1. 전기아크용접(V형)", "sub_section_no": "1"}'
WHERE id IN (
    SELECT DISTINCT e.id
    FROM graph_relationships r
    JOIN graph_entities e ON r.source_id = e.id
    WHERE e.source_section = '13-2-4' AND e.type = 'WorkType'
      AND r.source_chunk_id IN ('C-0956-A','C-0956-B','C-0956-C')
      AND e.name LIKE '%강판 전기아크용접(%'  -- 제네릭("강판 전기아크용접") 제외
);
```

**핵심 조건**:
1. `e.name LIKE '%(%'` — 괄호가 있는 규격별 엔티티만 대상 (제네릭 7건 제외)
2. `(properties->>'sub_section') IS NULL` — 이미 매핑된 건 중복 UPDATE 방지
3. 제네릭 엔티티 6건은 `properties.spec` 필드(V형/U형/H형/X형/Fillet) 기반 개별 UPDATE
4. D 청크는 2개 표(V형 마지막 + U형 시작)를 포함하므로, 헤더 패턴 기반 분리 처리

---

## 2. 다른 섹션 적용 가능성 분석

### DB 전체 현황: sub_section null 문제의 규모

| source_section | 제목 | WorkType 수 | null% | 청크 수 |
|---|---|---|---|---|
| **13-2-3** | 강관용접 | 122건 | 100% | 11 |
| **9-5-4** | 수치지도 작성 | 113건 | 100% | 68 |
| **13-1-1** | 플랜트 배관 설치 | 108건 | 100% | 18 |
| **13-2-6** | 응력제거 | 98건 | 100% | 6 |
| **13-1-2** | 관만곡 설치 | 88건 | 100% | 17 |
| **8-2-12** | 크러셔 | 87건 | 100% | 21 |
| **기타 14개** | ... | 25~75건 | 100% | 3~22 |

> [!WARNING]
> **10건 이상 WorkType을 보유한 상위 20개 섹션 전부** `sub_section: null 100%`입니다.
> 이것은 13-2-4만의 문제가 아니라 **파이프라인 전체의 시스템적 결함**입니다.

### 적용 가능성 판정

| 조건 | 상태 | 판정 |
|---|---|---|
| `graph_relationships.source_chunk_id` 존재율 | **100%** (30,055건 전부) | ✅ 전체 적용 가능 |
| `table_id` 소제목 번호 인코딩 패턴 | 13-2-3(강관용접)에서도 동일 확인 (`T-13-2-3-01`, `02`, ..., `07`) | ✅ 동일 패턴 |
| 청크 text에 소제목 텍스트 존재 | 13-2-3: `"1. 전기아크용접 (개소당)"` 확인 | ✅ 파싱 가능 |
| 청크 → WorkType 역추적 가능 | relationship JOIN으로 100% 추적 가능 | ✅ |

### 자동화 가능한 범용 패치 스크립트 설계

```
[자동 패치 알고리즘]

1. 대상 섹션 선별:
   SELECT source_section FROM graph_entities
   WHERE type='WorkType' AND (properties->>'sub_section') IS NULL
   GROUP BY 1 HAVING COUNT(*) >= 10

2. 각 섹션에 대해:
   a) graph_chunks에서 table_id 패턴 추출
      → "T-{section}-{소제목번호}-{분할번호}" 분해
   b) 소제목 번호별로 chunk_id 그룹핑
   c) 첫 번째 청크의 text에서 소제목 텍스트 자동 파싱
      → 정규식: /\d+\.\s*(.+?)[\n(]/ 매칭
   d) source_chunk_id 기반 UPDATE 실행

3. 파싱 실패 시 (소제목 텍스트 미발견):
   → sub_section = "소제목 {번호}" (플레이스홀더) 주입
   → 수동 확인 대상으로 플래그
```

### 자동화 주의사항

| 리스크 | 대응 |
|---|---|
| **D 청크 문제**: 하나의 청크가 2개 소제목의 표를 포함하는 경우 | table_id 번호가 다르면 분리 가능. 같으면 헤더 패턴 비교 필요 |
| **제네릭 엔티티**: 규격 정보 없는 상위 엔티티 | `properties.spec` 값으로 분류. spec도 없으면 총괄로 처리 |
| **다부문 섹션**: 소제목 구조가 아닌 섹션 (예: 8-3-8 기타기계) | table_id에 소제목 번호가 없는 경우 → 해당 섹션 건너뛰기 |
| **13-1-1 배관설치**: table_id가 01 하나뿐인 경우 | 규격(재질/관경)이 WorkType name에 포함되어 있으므로 다른 기준으로 분류 필요 |

---

## 3. 결론: 적용 범위 및 권장 사항

### 즉시 적용 가능 (동일 패턴, SQL 패치만으로 해결)
- ✅ **13-2-3 강관용접** (122건) — table_id `01`~`07` 패턴 확인
- ✅ **13-2-6 응력제거** (98건)
- ✅ **13-1-4 Fitting 취부** (25건)
- ✅ **13-2-1** (55건)

### 별도 분석 필요 (구조가 다를 수 있음)
- ⚠️ **13-1-1 플랜트 배관 설치** (108건) — 소제목이 아닌 재질/관경 기반 분류
- ⚠️ **9-5-4 수치지도 작성** (113건) — 68개 청크, 도메인이 다름
- ⚠️ **8-2-12 크러셔** (87건) — 장비 도메인, 구조 확인 필요

### 근본적 해결: 파이프라인 코드 수정 (장기)
- `step2_entity_extractor.py`의 LLM 프롬프트에 **"이 표가 속한 소제목은 {X}이다"** 컨텍스트 주입
- `step3_relation_builder.py`에서 `properties.sub_section` 필드를 자동 생성하는 로직 추가
- 이렇게 하면 향후 데이터 재추출 시 sub_section이 자동으로 채워짐
