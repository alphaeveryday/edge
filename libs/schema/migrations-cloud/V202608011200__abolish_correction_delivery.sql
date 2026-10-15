-- ============================================================================
-- CORRECTION 전달 폐지 (ADR-0044) — tenant_delivery 를 NEW·INVALIDATION 2형상으로 축소
--
-- 생산자(발번)가 구현된 적 없는 유형이라 실환경 행은 없고, 로컬 시드 행만 존재할
-- 수 있다 — CHECK 축소 전에 제거한다(시드는 R__seed_local_demo_delivery.sql 이
-- 2경로 서사로 함께 축소됐다).
-- ============================================================================

SET search_path TO public;

DELETE FROM tenant_delivery WHERE delivery_type = 'CORRECTION';

ALTER TABLE tenant_delivery
    DROP CONSTRAINT ck_tenant_delivery_type,
    ADD CONSTRAINT ck_tenant_delivery_type
        CHECK (delivery_type IN ('NEW', 'INVALIDATION'));

-- 유형별 페이로드 형상(와이어 계약과 1:1): 무효화는 대상·사유 필수.
ALTER TABLE tenant_delivery
    DROP CONSTRAINT ck_tenant_delivery_payload,
    ADD CONSTRAINT ck_tenant_delivery_payload CHECK (
        (delivery_type = 'NEW'
            AND explanation_result_id IS NOT NULL
            AND target_explanation_result_id IS NULL
            AND reason IS NULL)
        OR (delivery_type = 'INVALIDATION'
            AND explanation_result_id IS NULL
            AND target_explanation_result_id IS NOT NULL
            AND reason IS NOT NULL)
    );

-- 2형상에서는 본체·대상이 동시에 실리는 유형이 없어(payload CHECK 가 배타를 강제)
-- 자기참조 금지 제약이 공집합이 됐다 — 죽은 제약은 지운다.
ALTER TABLE tenant_delivery
    DROP CONSTRAINT ck_tenant_delivery_no_self_target;

COMMENT ON TABLE tenant_delivery IS
'테넌트별 전달 레코드(outbox) — 번들 조립 원장(event-bundle-schema.md). PK (tenant_id, cursor) = 전달 멱등 키. 전달 유형은 NEW·INVALIDATION 2형상(ADR-0044 — CORRECTION 폐지). writer = analysis-engine write-time fan-out(NEW, ALPHA-493)·INVALIDATION 발번은 후속(ALPHA-440), reader = tenant-sync-api.';
