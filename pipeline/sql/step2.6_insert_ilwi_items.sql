-- Step 2.6 Phase 6: unit_costs → ilwi_items 변환
-- Supabase SQL Editor에서 실행
-- labor_types 컬럼은 scalar 혼재로 제외 (필요시 별도 업데이트)
INSERT INTO ilwi_items (
        ilwi_code,
        name,
        spec,
        labor_cost,
        material_cost,
        expense_cost,
        total_cost,
        source_id
    )
SELECT metadata->>'ilwi_code' AS ilwi_code,
    name,
    metadata->>'spec' AS spec,
    NULLIF(metadata->>'labor_cost', '')::NUMERIC AS labor_cost,
    NULLIF(metadata->>'material_cost', '')::NUMERIC AS material_cost,
    NULLIF(metadata->>'expense_cost', '')::NUMERIC AS expense_cost,
    NULLIF(metadata->>'total_cost', '')::NUMERIC AS total_cost,
    id::TEXT AS source_id
FROM unit_costs
WHERE namespace = 'standard_price_2025';