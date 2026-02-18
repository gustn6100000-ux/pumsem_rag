-- ═══════════════════════════════════════════════════════════════════
-- Step 2.7: 벌크 임베딩 업데이트 보조 함수
-- 대상 DB: bfomacoarwtqzjfxszdr (pumsem)
-- 용도: Python step7_embedding_generator.py에서 RPC 호출로 사용
-- 실행일: 2026-02-11
-- ═══════════════════════════════════════════════════════════════════
-- ───────────────────────────────────────────────────────────────────
-- bulk_update_embeddings
-- 
-- Why: 건별 update().eq()는 DB 왕복이 행 수만큼 발생하여 너무 느림.
--      이 함수로 100건씩 묶어 1회 RPC 호출 → 속도 ~100x 향상.
--
-- 파라미터:
--   p_table      : 대상 테이블 ('graph_entities' 또는 'graph_chunks')
--   p_ids        : 업데이트할 행의 ID 배열
--   p_embeddings : 각 ID에 대응하는 임베딩 벡터 (TEXT 형태, '[0.1, 0.2, ...]')
--
-- 반환값: 업데이트된 행 수 (INT)
--
-- 보안: p_table은 화이트리스트('graph_entities', 'graph_chunks')만 허용.
--       동적 SQL 없이 분기 처리로 SQL Injection 방어.
-- ───────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION bulk_update_embeddings(
        p_table TEXT,
        p_ids TEXT [],
        p_embeddings TEXT []
    ) RETURNS INT AS $$
DECLARE updated INT := 0;
i INT;
BEGIN IF p_table = 'graph_entities' THEN FOR i IN 1..array_length(p_ids, 1) LOOP
UPDATE graph_entities
SET embedding = p_embeddings [i]::vector(768)
WHERE id = p_ids [i];
updated := updated + 1;
END LOOP;
ELSIF p_table = 'graph_chunks' THEN FOR i IN 1..array_length(p_ids, 1) LOOP
UPDATE graph_chunks
SET embedding = p_embeddings [i]::vector(768)
WHERE id = p_ids [i];
updated := updated + 1;
END LOOP;
ELSE RAISE EXCEPTION 'bulk_update_embeddings: 허용되지 않는 테이블 "%"',
p_table;
END IF;
RETURN updated;
END;
$$ LANGUAGE plpgsql;