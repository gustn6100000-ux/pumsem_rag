-- Step 2.6 검증 쿼리: 전체 테이블 건수 확인
-- 적재 완료 후 실행
-- 1. 전체 건수 검증
SELECT 'graph_entities' AS tbl,
    COUNT(*) AS cnt
FROM graph_entities
UNION ALL
SELECT 'graph_relationships',
    COUNT(*)
FROM graph_relationships
UNION ALL
SELECT 'graph_global_relationships',
    COUNT(*)
FROM graph_global_relationships
UNION ALL
SELECT 'graph_chunks',
    COUNT(*)
FROM graph_chunks
UNION ALL
SELECT 'ilwi_items',
    COUNT(*)
FROM ilwi_items;
-- 기대 결과:
-- graph_entities:             16,364
-- graph_relationships:        23,586
-- graph_global_relationships: 1,063
-- graph_chunks:               2,105
-- ilwi_items:                 6,992
-- 2. 엔티티 타입별 건수
SELECT type,
    COUNT(*) AS cnt
FROM graph_entities
GROUP BY type
ORDER BY cnt DESC;
-- 3. 관계 타입별 건수
SELECT relation,
    COUNT(*) AS cnt
FROM graph_relationships
GROUP BY relation
ORDER BY cnt DESC;
-- 4. 전역 관계 타입별 건수
SELECT relation,
    COUNT(*) AS cnt
FROM graph_global_relationships
GROUP BY relation;
-- 5. Orphaned FK 확인 (둘 다 0이어야 함)
SELECT COUNT(*) AS orphaned_source
FROM graph_relationships r
    LEFT JOIN graph_entities e ON e.id = r.source_id
WHERE e.id IS NULL;
SELECT COUNT(*) AS orphaned_target
FROM graph_relationships r
    LEFT JOIN graph_entities e ON e.id = r.target_id
WHERE e.id IS NULL;
-- 6. 임베딩 NULL 확인 (Step 2.7 전이므로 전부 NULL)
SELECT COUNT(*) AS null_embeddings
FROM graph_entities
WHERE embedding IS NULL;
-- 기대: 16,364
-- 7. 일위대가 Name 분포 (Top 10)
SELECT name,
    COUNT(*) AS cnt
FROM ilwi_items
GROUP BY name
ORDER BY cnt DESC
LIMIT 10;