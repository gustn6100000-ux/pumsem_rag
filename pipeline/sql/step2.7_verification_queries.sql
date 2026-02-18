-- ═══════════════════════════════════════════════════════════════════
-- Step 2.7: 검증 쿼리 모음
-- 대상 DB: bfomacoarwtqzjfxszdr (pumsem)
-- 실행일: 2026-02-11
-- 전체 통과 확인 완료
-- ═══════════════════════════════════════════════════════════════════
-- ───────────────────────────────────────────────────────────────────
-- 1. NULL 잔존 확인
-- 기대: 양쪽 null_count = 0
-- 실제: graph_entities 0건, graph_chunks 0건 ✅
-- ───────────────────────────────────────────────────────────────────
SELECT 'graph_entities' AS tbl,
    COUNT(*) AS total,
    COUNT(embedding) AS has_emb,
    COUNT(*) - COUNT(embedding) AS null_count
FROM graph_entities
UNION ALL
SELECT 'graph_chunks',
    COUNT(*),
    COUNT(embedding),
    COUNT(*) - COUNT(embedding)
FROM graph_chunks;
-- 결과:
-- | tbl              | total  | has_emb | null_count |
-- |------------------|--------|---------|------------|
-- | graph_entities   | 16,364 | 16,364  | 0          |
-- | graph_chunks     | 2,105  | 2,105   | 0          |
-- ───────────────────────────────────────────────────────────────────
-- 2. 차원 검증 (768차원 일률 확인)
-- 기대: 모두 dim=768
-- 실제: graph_entities 768×16364, graph_chunks 768×2105 ✅
-- ───────────────────────────────────────────────────────────────────
SELECT 'graph_entities' AS tbl,
    vector_dims(embedding) AS dim,
    COUNT(*) AS cnt
FROM graph_entities
WHERE embedding IS NOT NULL
GROUP BY dim
UNION ALL
SELECT 'graph_chunks',
    vector_dims(embedding),
    COUNT(*)
FROM graph_chunks
WHERE embedding IS NOT NULL
GROUP BY vector_dims(embedding);
-- 결과:
-- | tbl              | dim | cnt    |
-- |------------------|-----|--------|
-- | graph_entities   | 768 | 16,364 |
-- | graph_chunks     | 768 | 2,105  |
-- ───────────────────────────────────────────────────────────────────
-- 3. SQL 함수 존재 확인
-- 기대: 4개 행
-- 실제: 4개 행 ✅
-- ───────────────────────────────────────────────────────────────────
SELECT routine_name,
    routine_type
FROM information_schema.routines
WHERE routine_schema = 'public'
    AND routine_name IN (
        'search_entities_by_embedding',
        'get_related_resources',
        'get_entity_hierarchy',
        'search_ilwi'
    );
-- 결과:
-- | routine_name                   | routine_type |
-- |--------------------------------|--------------|
-- | get_entity_hierarchy           | FUNCTION     |
-- | get_related_resources          | FUNCTION     |
-- | search_entities_by_embedding   | FUNCTION     |
-- | search_ilwi                    | FUNCTION     |
-- ───────────────────────────────────────────────────────────────────
-- 4. 벡터 유사도 검색 테스트
-- "보통인부"로 검색 → 의미적으로 관련 높은 Labor 항목 반환
-- ───────────────────────────────────────────────────────────────────
SELECT e.name,
    e.type,
    1 - (
        e.embedding <=> (
            SELECT embedding
            FROM graph_entities
            WHERE name = '보통인부'
            LIMIT 1
        )
    ) AS similarity
FROM graph_entities e
WHERE e.embedding IS NOT NULL
ORDER BY e.embedding <=> (
        SELECT embedding
        FROM graph_entities
        WHERE name = '보통인부'
        LIMIT 1
    )
LIMIT 5;
-- 결과:
-- | name                          | type  | similarity |
-- |-------------------------------|-------|------------|
-- | 보통인부                      | Labor | 1.000      |
-- | 보통인부(모래체가름 제외)      | Labor | 0.877      |
-- | 보통인부(모래체가름 포함)      | Labor | 0.875      |
-- | 보통인부(200mm이하)           | Labor | 0.873      |
-- | 덕트공보통인부                | Labor | 0.869      |
-- ───────────────────────────────────────────────────────────────────
-- 5. get_related_resources 테스트
-- W-0115 엔티티의 1-hop 관계 탐색
-- ───────────────────────────────────────────────────────────────────
SELECT *
FROM get_related_resources('W-0115')
LIMIT 5;
-- 결과:
-- | direction | relation   | related_name          | related_type |
-- |-----------|------------|-----------------------|--------------|
-- | outbound  | HAS_NOTE   | 철거(재사용)적용률     | Note         |
-- | outbound  | BELONGS_TO | 굴 취                 | Section      |
-- ───────────────────────────────────────────────────────────────────
-- 6. get_entity_hierarchy 테스트
-- S-0001(일반사항)의 전역 관계 → 하위 섹션 계층
-- ───────────────────────────────────────────────────────────────────
SELECT *
FROM get_entity_hierarchy('S-0001')
LIMIT 5;
-- 결과:
-- | direction | relation   | related_name           | related_type | properties                          |
-- |-----------|------------|------------------------|--------------|-------------------------------------|
-- | child     | HAS_CHILD  | 목적                   | Section      | {level: 3, child_id: 1-1-1}        |
-- | child     | HAS_CHILD  | 교통통제 및 안전처리    | Section      | {level: 3, child_id: 1-1-1#2}      |
-- | child     | HAS_CHILD  | 기본철골공수            | Section      | {level: 3, child_id: 1-1-1#3}      |
-- | child     | HAS_CHILD  | 용접접합               | Section      | {level: 3, child_id: 1-1-1#4}      |
-- | child     | HAS_CHILD  | 비탈면 보강공           | Section      | {level: 3, child_id: 1-1-1#5}      |
-- ───────────────────────────────────────────────────────────────────
-- 7. search_ilwi 테스트
-- "배관" 키워드로 일위대가 검색
-- ───────────────────────────────────────────────────────────────────
SELECT *
FROM search_ilwi('배관')
LIMIT 5;
-- 결과:
-- | id   | name                       | spec | labor_cost | material_cost | total_cost |
-- |------|----------------------------|------|------------|---------------|------------|
-- | 5380 | PB 이중관 접합 및 배관      | D16  | 14,996     | 149           | 15,145     |
-- | 5367 | PB 이중관 접합 및 배관      | D20  | 16,527     | 165           | 16,692     |
-- | 5381 | PB관 일반접합 및 배관       | D16  | 11,596     | 115           | 11,711     |
-- | 5376 | PB관 일반접합 및 배관       | D20  | 12,888     | 128           | 13,016     |
-- | 5378 | PE관 접합 및 배관           | D16  | 9,283      | 92            | 9,375      |
-- ───────────────────────────────────────────────────────────────────
-- 8. search_entities_by_embedding 입력 검증 테스트 (Codex F5)
-- NULL/빈 입력 시 에러 대신 빈 결과 반환 확인
-- ───────────────────────────────────────────────────────────────────
-- 8-a. NULL 입력
SELECT *
FROM search_entities_by_embedding(NULL);
-- 기대: 빈 결과 (0행) ✅
-- 8-b. 빈 문자열 입력
SELECT *
FROM search_entities_by_embedding('');
-- 기대: 빈 결과 (0행) ✅
-- 8-c. 잘못된 형식 입력
SELECT *
FROM search_entities_by_embedding('not_a_vector');
-- 기대: NOTICE 로그 출력 + 빈 결과 (0행, 에러 전파 안 함) ✅