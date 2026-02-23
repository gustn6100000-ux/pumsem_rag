-- 20260223_phase4_get_related_resources_v2.sql
-- Phase 4: RAG Search Layer Refactoring
-- Purpose: Create v2 of get_related_resources to only fetch outbound relationships
--          and rename parameter to p_entity_id to match PostgREST conventions.

CREATE OR REPLACE FUNCTION get_related_resources_v2(p_entity_id TEXT)
RETURNS JSONB AS $$
DECLARE
    result JSONB;
BEGIN
    SELECT jsonb_build_object(
        'outbound', COALESCE(
            jsonb_agg(
                jsonb_build_object(
                    'relation', r.relation_type,
                    'related_id', tgt.id,
                    'related_name', tgt.name,
                    'related_type', tgt.type,
                    'properties', tgt.properties
                )
            ) FILTER (WHERE r.source_id = p_entity_id AND r.relation_type != 'has_workType'), '[]'::jsonb
        )
    ) INTO result
    FROM graph_relationships r
    LEFT JOIN graph_entities tgt ON r.target_id = tgt.id
    WHERE r.source_id = p_entity_id;

    RETURN COALESCE(result, '{"outbound": []}'::jsonb);
END;
$$ LANGUAGE plpgsql;

-- Grant permissions
GRANT EXECUTE ON FUNCTION get_related_resources_v2(TEXT) TO anon, authenticated, service_role;

-- Do NOT drop the old function here to ensure zero-downtime deployment.
-- It will be dropped after the Edge Function is updated and verified.
