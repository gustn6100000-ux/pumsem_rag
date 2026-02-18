-- ═══════════════════════════════════════════════════════
-- Step 2.6 Migration: 그래프 RAG 테이블 생성
-- Supabase SQL Editor에서 전체 복사 후 실행
-- ═══════════════════════════════════════════════════════
-- 필수 확장
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- ───────────────────────────────────────────
-- 1. graph_entities (품셈 엔티티)
-- ───────────────────────────────────────────
CREATE TABLE graph_entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    properties JSONB DEFAULT '{}',
    source_section TEXT,
    embedding vector(768),
    created_at TIMESTAMPTZ DEFAULT now()
);
-- ───────────────────────────────────────────
-- 2. graph_relationships (추출 관계)
-- ───────────────────────────────────────────
CREATE TABLE graph_relationships (
    id SERIAL PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES graph_entities(id),
    target_id TEXT NOT NULL REFERENCES graph_entities(id),
    relation TEXT NOT NULL,
    properties JSONB DEFAULT '{}',
    source_chunk_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
-- ───────────────────────────────────────────
-- 2-1. graph_global_relationships (계층/참조)
-- ───────────────────────────────────────────
CREATE TABLE graph_global_relationships (
    id SERIAL PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES graph_entities(id),
    target_id TEXT NOT NULL REFERENCES graph_entities(id),
    relation TEXT NOT NULL,
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now()
);
-- ───────────────────────────────────────────
-- 3. graph_chunks (원문 청크)
-- ───────────────────────────────────────────
CREATE TABLE graph_chunks (
    id TEXT PRIMARY KEY,
    section_id TEXT,
    title TEXT,
    department TEXT,
    chapter TEXT,
    section TEXT,
    text TEXT,
    tables JSONB,
    notes JSONB,
    conditions JSONB,
    cross_references JSONB,
    revision_year TEXT,
    token_count INT,
    embedding vector(768),
    created_at TIMESTAMPTZ DEFAULT now()
);
-- ───────────────────────────────────────────
-- 4. ilwi_items (일위대가)
-- ───────────────────────────────────────────
CREATE TABLE ilwi_items (
    id SERIAL PRIMARY KEY,
    ilwi_code TEXT,
    name TEXT NOT NULL,
    spec TEXT,
    labor_types TEXT [],
    labor_cost NUMERIC,
    material_cost NUMERIC,
    expense_cost NUMERIC,
    total_cost NUMERIC,
    source_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);
-- ═══════════════════════════════════════════════════════
-- 인덱스
-- ═══════════════════════════════════════════════════════
CREATE INDEX idx_entities_embedding ON graph_entities USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_entities_type ON graph_entities(type);
CREATE INDEX idx_entities_name_trgm ON graph_entities USING gin (name gin_trgm_ops);
CREATE INDEX idx_rel_source ON graph_relationships(source_id);
CREATE INDEX idx_rel_target ON graph_relationships(target_id);
CREATE INDEX idx_rel_relation ON graph_relationships(relation);
CREATE INDEX idx_global_rel_source ON graph_global_relationships(source_id);
CREATE INDEX idx_global_rel_target ON graph_global_relationships(target_id);
CREATE INDEX idx_global_rel_relation ON graph_global_relationships(relation);
CREATE INDEX idx_chunks_embedding ON graph_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_section ON graph_chunks(section_id);
CREATE INDEX idx_ilwi_name ON ilwi_items(name);
CREATE INDEX idx_ilwi_name_trgm ON ilwi_items USING gin (name gin_trgm_ops);
-- ═══════════════════════════════════════════════════════
-- RLS 정책
-- ═══════════════════════════════════════════════════════
ALTER TABLE graph_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_global_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ilwi_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon_read" ON graph_entities FOR
SELECT TO anon USING (true);
CREATE POLICY "anon_read" ON graph_relationships FOR
SELECT TO anon USING (true);
CREATE POLICY "anon_read" ON graph_global_relationships FOR
SELECT TO anon USING (true);
CREATE POLICY "anon_read" ON graph_chunks FOR
SELECT TO anon USING (true);
CREATE POLICY "anon_read" ON ilwi_items FOR
SELECT TO anon USING (true);