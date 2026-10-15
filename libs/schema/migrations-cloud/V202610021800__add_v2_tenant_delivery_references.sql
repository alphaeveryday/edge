-- Prepare v2 delivery without changing the existing v1 publisher or queue.
SET LOCAL lock_timeout = '5s';

ALTER TABLE tenant_delivery
    ADD COLUMN movement_analysis_id TEXT REFERENCES movement_analyses (analysis_id),
    ADD COLUMN target_movement_analysis_id TEXT REFERENCES movement_analyses (analysis_id),
    DROP CONSTRAINT ck_tenant_delivery_payload,
    ADD CONSTRAINT ck_tenant_delivery_payload CHECK (
        (delivery_type = 'NEW'
            AND num_nonnulls(explanation_result_id, movement_analysis_id) = 1
            AND target_explanation_result_id IS NULL
            AND target_movement_analysis_id IS NULL
            AND reason IS NULL)
        OR (delivery_type = 'INVALIDATION'
            AND explanation_result_id IS NULL
            AND movement_analysis_id IS NULL
            AND num_nonnulls(target_explanation_result_id, target_movement_analysis_id) = 1
            AND reason IS NOT NULL)
    );

COMMENT ON COLUMN tenant_delivery.movement_analysis_id IS
'v2 가격 설명 NEW의 본체. v1 explanation_result_id와 배타적이며, 생산자 전환 전에는 NULL.';
COMMENT ON COLUMN tenant_delivery.target_movement_analysis_id IS
'v2 가격 설명 INVALIDATION의 대상. v1 target_explanation_result_id와 배타적.';
