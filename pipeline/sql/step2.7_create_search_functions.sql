-- ═══════════════════════════════════════════════════════════════════
-- Step 2.7: SQL 검색 함수 4개
-- 대상 DB: bfomacoarwtqzjfxszdr (pumsem)
-- 실행: Supabase SQL Editor 또는 MCP
-- ═══════════════════════════════════════════════════════════════════
-- ───────────────────────────────────────────────────────────────────
-- 함수 1: search_entities_by_embedding
-- 벡터 유사도 검색 → 유사 엔티티 반환
-- F2: TEXT → ::vector 캐스팅
-- F5: PL/pgSQL wrapper로 입력 검증 (차원/형식 오류 시 빈 결과 반환)
-- ───────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION search_entities_by_embedding(
        query_embedding_text TEXT,
        -- '[0.1, 0.2, ...]' 형태 문자열
        match_count INT DEFAULT 5,
        match_threshold FLOAT DEFAULT 0.5
    ) RETURNS TABLE(
        id TEXT,
        name TEXT,
        type TEXT,
        properties JSONB,
        similarity FLOAT,
        source_section TEXT
    ) AS $$
DECLARE parsed_vector vector(768);
BEGIN -- 입력 검증: NULL 또는 빈 문자열
IF query_embedding_text IS NULL
OR trim(query_embedding_text) = '' THEN RAISE NOTICE 'search_entities_by_embedding: 빈 입력 벡터';
RETURN;
END IF;
-- 차원/형식 검증: TEXT → vector 캐스팅 시도
BEGIN parsed_vector := query_embedding_text::vector(768);
EXCEPTION
WHEN OTHERS THEN RAISE NOTICE 'search_entities_by_embedding: 벡터 파싱 실패 - %',
SQLERRM;
RETURN;
END;
RETURN QUERY
SELECT ge.id,
    ge.name,
    ge.type,
    ge.properties,
    (1 - (ge.embedding <=> parsed_vector))::FLOAT AS similarity,
    ge.source_section
FROM graph_entities ge
WHERE ge.embedding IS NOT NULL
    AND 1 - (ge.embedding <=> parsed_vector) > match_threshold
ORDER BY ge.embedding <=> parsed_vector
LIMIT match_count;
END;
$$ LANGUAGE plpgsql STABLE;
-- ───────────────────────────────────────────────────────────────────
-- 함수 2: get_related_resources
-- 그래프 확장: 엔티티 ID → 1-hop 연결된 모든 리소스
-- F4: direction 컬럼으로 inbound/outbound 구분
-- ───────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_related_resources(p_entity_id TEXT) RETURNS TABLE(
        direction TEXT,
        relation TEXT,
        related_id TEXT,
        related_name TEXT,
        related_type TEXT,
        properties JSONB
    ) AS $$ -- outbound: 이 엔티티 → 연결된 대상
SELECT 'outbound'::TEXT AS direction,
    r.relation,
    e.id AS related_id,
    e.name AS related_name,
    e.type AS related_type,
    r.properties
FROM graph_relationships r
    JOIN graph_entities e ON e.id = r.target_id
WHERE r.source_id = p_entity_id;
$$ LANGUAGE sql STABLE;
-- ───────────────────────────────────────────────────────────────────
-- 함수 3: get_entity_hierarchy (Codex F6: 전역 관계 탐색)
-- 전역 관계(HAS_CHILD, REFERENCES) → 계층 컨텍스트 제공
-- ───────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION get_entity_hierarchy(p_entity_id TEXT) RETURNS TABLE(
        direction TEXT,
        relation TEXT,
        related_id TEXT,
        related_name TEXT,
        related_type TEXT,
        properties JSONB
    ) AS $$ -- outbound: 이 엔티티의 하위 항목
SELECT 'child'::TEXT AS direction,
    r.relation,
    e.id AS related_id,
    e.name AS related_name,
    e.type AS related_type,
    r.properties
FROM graph_global_relationships r
    JOIN graph_entities e ON e.id = r.target_id
WHERE r.source_id = p_entity_id
UNION ALL
-- inbound: 이 엔티티의 상위 항목
SELECT 'parent'::TEXT AS direction,
    r.relation,
    e.id AS related_id,
    e.name AS related_name,
    e.type AS related_type,
    r.properties
FROM graph_global_relationships r
    JOIN graph_entities e ON e.id = r.source_id
WHERE r.target_id = p_entity_id;
$$ LANGUAGE sql STABLE;
-- ───────────────────────────────────────────────────────────────────
-- 함수 4: search_ilwi
-- 일위대가 키워드 검색
-- ───────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION search_ilwi(
        search_name TEXT,
        search_spec TEXT DEFAULT NULL
    ) RETURNS TABLE(
        id INT,
        name TEXT,
        spec TEXT,
        labor_cost NUMERIC,
        material_cost NUMERIC,
        expense_cost NUMERIC,
        total_cost NUMERIC
    ) AS $$
SELECT i.id,
    i.name,
    i.spec,
    i.labor_cost,
    i.material_cost,
    i.expense_cost,
    i.total_cost
FROM ilwi_items i
WHERE i.name ILIKE '%' || search_name || '%'
    AND (
        search_spec IS NULL
        OR i.spec ILIKE '%' || search_spec || '%'
    )
ORDER BY i.name,
    i.spec
LIMIT 20;
$$ LANGUAGE sql STABLE;