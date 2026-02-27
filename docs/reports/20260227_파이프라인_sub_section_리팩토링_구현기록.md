# 파이프라인 sub_section 아키텍처 리팩토링 — 구현 기록서

> **작성일**: 2026-02-27  
> **작업 기반**: M37 심층 분석 결과 + Codex 6개 이슈 교차 검증  
> **수정 파일 3개**: `schemas.py`, `step2_llm_extractor.py`, `step3_relation_builder.py`

---

## 1. 배경 및 문제 정의

### 근본 원인
13-2-4 강판 전기아크용접 섹션의 130개 WorkType 엔티티에서 `sub_section`(V형/U형/H형/X형/Fillet 분류)이 **전부 null**인 문제가 발견됨. 역추적 결과 파이프라인 3단계에 걸친 구조적 결함:

| 단계 | 결함 |
|---|---|
| Step 2 (LLM 추출) | 프롬프트에 소제목 컨텍스트가 주입되지 않아 LLM이 분류를 알 수 없음 |
| 스키마 | `Entity` 모델에 `sub_section` 전용 필드가 없고 `properties: dict` 자유형만 존재 |
| Step 3 (병합) | `_entity_key()`가 sub_section을 동일성 판별에 미포함 → 다른 분류의 동명 엔티티가 병합 |

### 해결 전략
SQL 핫픽스(DB 직접 수정)가 아닌, **파이프라인 코드 자체를 수정**하여 재발을 원천 차단.

---

## 2. 구현 상세

### 2.1 Phase 1: 스키마 계약 강제

#### 파일: `schemas.py` (4줄 추가)

**변경 위치**: `Entity` 클래스 47행 (`properties` 필드 아래)

```diff
    properties: dict = Field(default_factory=dict, description="추가 속성")
+   # Why: sub_section을 명시적 필드로 승격하여 properties dict의 키 분화 위험 방지
+   #       프론트엔드 트리 필터링(재질→접합→관경)의 검색 단위(Facet)로 활용
+   sub_section: Optional[str] = Field(None, description="소제목 분류 (예: 1. 전기아크용접(V형))")
+   sub_section_no: Optional[str] = Field(None, description="소제목 번호 (예: 01)")
    confidence: float = Field(default=1.0, ge=0, le=1, description="추출 신뢰도")
```

**설계 의도**:
- `properties` dict에 자유형으로 넣으면 `sub_section`, `subSection`, `sub_title` 등 키 분화 위험
- Pydantic `Optional[str] = None`으로 기존 데이터와 하위 호환 보장
- DB 적재 시 별도 컬럼이 아닌 `properties` JSONB에 함께 직렬화됨

---

### 2.2 Phase 2-A: LLM 프롬프트 소제목 컨텍스트 주입

#### 파일: `step2_llm_extractor.py` (~60줄 추가/수정, 4개 지점)

#### 2.2.1 `LLMEntity` 필드 추가 (65~73행)

```diff
    quantity: Optional[float] = Field(None, description="수량 (숫자만)")
+   # Why: 파이프라인 sub_section 계약 — LLM이 소제목 분류를 직접 출력하도록
+   sub_section: Optional[str] = Field(None, description="소제목 분류 (예: 1. 전기아크용접(V형))")
```

**설계 의도**: LLM JSON 출력 스키마에 `sub_section` 필드가 있어야 LLM이 값을 생성할 수 있음.

#### 2.2.2 `SYSTEM_PROMPT` 규칙 10 추가 (128~137행)

```diff
9. 매트릭스 표가 감지되면 ... 전개를 시작하십시오.
+10. 🚨 **[소제목 분류 규칙]** 표 위에 `⚠️ 이 표는 '...' 분류에 속합니다` 지시가 있으면,
+    해당 분류를 WorkType 엔티티의 `sub_section` 필드에 **반드시** 기록하십시오.
+    예: `"sub_section": "1. 전기아크용접(V형)"`
```

JSON 출력 스키마도 동시 수정:
```diff
-  "entities": [{"type": "...", "name": "...", "spec": "...", "unit": "...", "quantity": ...}],
+  "entities": [{"type": "...", "name": "...", "spec": "...", "unit": "...", "quantity": ..., "sub_section": "소제목 분류 or null"}],
```

#### 2.2.3 `_extract_sub_headings()` 신규 함수 (214~257행, 46줄)

```python
def _extract_sub_headings(text: str, tables: list[dict]) -> dict[str, str]:
    """table_id → 소제목 텍스트 매핑을 생성한다.

    전략:
    1. table_id에서 소제목 번호를 파싱 (T-13-2-4-01-1 → 01)
    2. chunk.text에서 "N. 전기아크용접(X형)" 패턴을 정규식으로 추출
    3. 번호가 매치되면 실제 소제목 텍스트를 반환
    4. 못 찾으면 table_id 번호 기반 폴백 ("소제목 #01")
    """
```

**핵심 로직**:
- 정규식 `r'(\d+)\.\s*([^\n]+?)(?:\n|$)'`로 text에서 `1. 전기아크용접(V형)` 패턴 추출
- `table_id`의 `-(\d{2})-\d+$` 패턴으로 소제목 번호 파싱
- 번호 → 텍스트 매핑 생성 (예: `"01"` → `"1. 전기아크용접(V형)"`)

**검증 결과**:
```python
>>> _extract_sub_headings(text, tables)
{'T-13-2-4-01-1': '1. 전기아크용접(V형)',
 'T-13-2-4-02-1': '2. 전기아크용접(U형)',
 'T-13-2-4-05-3': '5. 전기아크용접(Fillet용접)'}
```

#### 2.2.4 `build_user_prompt()` 내 소제목 주입 (290~308행)

```diff
    tables = chunk.get("tables", [])
+   _sub_headings = _extract_sub_headings(text, tables)
+
    for i, table in enumerate(tables):
        ...
        parts.append(f"\n## 테이블 {i+1} (유형: {table.get('type', 'unknown')})")
+
+       # 소제목 컨텍스트 주입 (table_id 기반)
+       table_id = table.get('table_id', '')
+       heading = _sub_headings.get(table_id, '')
+       if heading:
+           parts.append(f"⚠️ 이 표는 '{heading}' 분류에 속합니다. ...")
```

**설계 의도**: 단순 번호(`#01`)가 아닌 **실제 텍스트**(`1. 전기아크용접(V형)`)를 LLM에 전달. LLM이 문맥을 이해하고 WorkType의 `sub_section`에 정확한 분류를 태깅.

#### 2.2.5 `extract_single_chunk()` Entity 변환 시 전파 (338~352행)

```diff
    entity = Entity(
        type=etype, name=le.name, spec=le.spec,
        unit=le.unit, quantity=le.quantity,
+       # Why: LLM이 추출한 sub_section을 Entity 스키마로 전파
+       sub_section=le.sub_section if hasattr(le, 'sub_section') else None,
        source_chunk_id=chunk_id, ...
    )
```

---

### 2.3 Phase 2-B: Step3 지능형 병합 (Smart Merge)

#### 파일: `step3_relation_builder.py` (~40줄 추가/수정, 3개 지점)

#### 2.3.1 `_entity_key()` 수정 (37~51행)

```diff
def _entity_key(ent: dict) -> str:
-   """엔티티 동일성 판별 키. type + normalized_name (+ spec) 기반."""
+   """엔티티 동일성 판별 키. type + normalized_name (+ spec) (+ sub_section) 기반."""
    norm = ent.get("normalized_name", ent["name"].replace(" ", ""))
    spec = ent.get("spec", "")
+   sub = ent.get("sub_section", "") or ""
    
-   if ent["type"] in ("WorkType", ...) and spec:
-       return f"{ent['type']}::{norm.lower()}::{safe_spec}"
-   return f"{ent['type']}::{norm.lower()}"
+   parts = [ent['type'], norm.lower()]
+   if ent['type'] in ("WorkType", ...) and spec:
+       parts.append(str(spec).replace(" ", "").lower())
+   if sub:
+       parts.append(sub.replace(" ", "").lower())
+   return "::".join(parts)
```

**효과**: 같은 이름(`강판 전기아크용접`)이라도 V형과 U형의 키가 분리됨:
```
기존: WorkType::강판전기아크용접::3mm        (V형/U형 구분 불가 → 병합)
수정: WorkType::강판전기아크용접::3mm::1.v형  (V형)
      WorkType::강판전기아크용접::3mm::2.u형  (U형, 독립 유지)
```

#### 2.3.2 `_smart_inherit_sub_section()` 신규 함수 (75~99행, 25줄)

```python
def _smart_inherit_sub_section(ent: dict, existing_map: dict[str, dict]) -> None:
    """테이블 엔티티에 sub_section이 없을 때, 같은 name+spec의 LLM 엔티티로부터 상속."""
```

**설계 의도**: Step 2.1(테이블 규칙 추출)은 `sub_section`을 생성하지 못하는 반면, Step 2.2(LLM)가 같은 name+spec에서 `sub_section`을 찾았다면, 중복 생성 대신 **빈 필드를 채워넣는(Fill) 단방향 상속**.

**호출 위치**: `merge_chunk_extractions()` 118~126행
```diff
        else:
            # 테이블에만 존재 → 추가
+           # Smart Merge: 테이블 엔티티에 sub_section이 없을 때,
+           #              같은 name+spec의 LLM 엔티티가 있다면 sub_section을 상속
            tent_copy = {**tent, "source_method": "table_rule"}
+           if not tent_copy.get("sub_section"):
+               _smart_inherit_sub_section(tent_copy, merged_ent_map)
            merged_entities.append(tent_copy)
```

---

### 2.4 Phase 3: Quality Gate

#### `run_step3()` 함수 끝부분 (622~633행)

```python
# ── Quality Gate: sub_section 채움률 검증 ──
worktypes = [e for ext in merged_exts for e in ext.get("entities", []) if e["type"] == "WorkType"]
filled = sum(1 for w in worktypes if w.get("sub_section"))
fill_rate = filled / len(worktypes) * 100 if worktypes else 0
print(f"\n  ⚠️ Quality Gate: sub_section 채움률 {fill_rate:.1f}% ({filled}/{len(worktypes)} WorkTypes)")
if fill_rate < 30:
    print(f"  ⚠️ 경고: sub_section 채움률이 30% 미만입니다.")
```

---

## 3. 검증 결과

| # | 테스트 | 명령어 | 결과 |
|---|---|---|---|
| 1 | Entity 인스턴스 생성 | `Entity(sub_section='1. V형', sub_section_no='01')` | ✅ `sub_section=1. V형` |
| 2 | 소제목 매핑 | `_extract_sub_headings(text, tables)` | ✅ 3개 table_id 정확 매핑 |
| 3 | 엔티티 키 분리 | `_entity_key(ent_with_sub)` | ✅ `::1.v형` 접미사로 분리 |
| 4 | 속성 상속 | `_smart_inherit_sub_section(ent, map)` | ✅ `after_inherit: 1. V형` |

---

## 4. 데이터 흐름 (Before → After)

```
[Before]
청크 text: "1. 전기아크용접(V형)\n(...표 데이터...)\n2. 전기아크용접(U형)\n..."
  ↓ Step 2 LLM
  LLM에게: "## 테이블 1 (유형: D_기타)\n| 구분 | 용접봉 | ..."  ← 소제목 정보 없음!
  ↓
  WorkType { name: "강판 전기아크용접", sub_section: null }  ← 분류 불가
  ↓ Step 3 Merge
  _entity_key = "WorkType::강판전기아크용접::3mm"  ← V/U형 구분 불가, 병합됨

[After]
청크 text: "1. 전기아크용접(V형)\n(...표 데이터...)\n2. 전기아크용접(U형)\n..."
  ↓ _extract_sub_headings
  {'T-13-2-4-01-1': '1. 전기아크용접(V형)'}
  ↓ Step 2 LLM
  LLM에게: "⚠️ 이 표는 '1. 전기아크용접(V형)' 분류에 속합니다\n| 구분 | 용접봉 | ..."
  ↓
  WorkType { name: "강판 전기아크용접", sub_section: "1. 전기아크용접(V형)" }  ← 정확!
  ↓ Step 3 Merge
  _entity_key = "WorkType::강판전기아크용접::3mm::1.전기아크용접(v형)"  ← V/U형 독립 유지
```

---

## 5. 다음 단계

| 단계 | 내용 | 비용/리스크 |
|---|---|---|
| 소규모 재추출 테스트 | `python step2_llm_extractor.py --section 13-2-4` | DeepSeek API ~$0.15 |
| 결과 검증 | JSON에서 `sub_section` 필드 채움 여부 확인 | 없음 |
| 전체 재추출 | 전체 배치 재실행 (사용자 승인 필요) | DeepSeek API ~$5~10 |
| Git 커밋 | 3개 파일 변경 커밋 + 푸시 | 없음 |
