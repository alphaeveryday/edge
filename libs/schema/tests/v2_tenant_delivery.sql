-- Dedicated test database only. Exercise the real constraints and roll back.
\set ON_ERROR_STOP on
BEGIN;

-- Copy the real CHECK constraints to test v1/v2 shapes without building the
-- unrelated legacy analysis graph. Real v2 foreign keys are tested below.
CREATE TEMP TABLE delivery_shapes (LIKE tenant_delivery INCLUDING CONSTRAINTS);
DO $$
DECLARE
    kind TEXT;
    mask INTEGER;
    expected BOOLEAN;
    accepted INTEGER := 0;
    rejected INTEGER := 0;
BEGIN
    FOREACH kind IN ARRAY ARRAY['NEW', 'INVALIDATION'] LOOP
        FOR mask IN 0..15 LOOP
            expected := (kind = 'NEW' AND mask IN (1, 2))
                     OR (kind = 'INVALIDATION' AND mask IN (4, 8));
            BEGIN
                INSERT INTO delivery_shapes
                    (tenant_id, cursor, delivery_type, explanation_result_id,
                     movement_analysis_id, target_explanation_result_id,
                     target_movement_analysis_id, reason, created_at)
                VALUES (1, 1, kind,
                    CASE WHEN mask & 1 <> 0 THEN 'v1' END,
                    CASE WHEN mask & 2 <> 0 THEN 'v2' END,
                    CASE WHEN mask & 4 <> 0 THEN 'v1' END,
                    CASE WHEN mask & 8 <> 0 THEN 'v2' END,
                    CASE WHEN kind = 'INVALIDATION' THEN 'withdraw' END, now());
                IF NOT expected THEN
                    RAISE EXCEPTION 'Invalid delivery shape accepted: %, %', kind, mask;
                END IF;
                accepted := accepted + 1;
            EXCEPTION WHEN check_violation THEN
                IF expected THEN RAISE; END IF;
                rejected := rejected + 1;
            END;
        END LOOP;
    END LOOP;
    IF accepted <> 4 OR rejected <> 28 THEN RAISE EXCEPTION 'Shape coverage incomplete'; END IF;
    RAISE NOTICE 'Delivery shapes: 4 valid, 28 invalid verified';
END $$;

INSERT INTO tenant (tenant_id, tenant_name, environment, status)
OVERRIDING SYSTEM VALUE
VALUES (-1151, 'v2 delivery test', 'DEV', 'ACTIVE');
INSERT INTO movement_analyses (analysis_id, etf_code, analysis_at, trading_date)
VALUES ('delivery-v2-test', '091160', '2026-10-02T10:00:00+09:00', '2026-10-02');
INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, movement_analysis_id)
VALUES (-1151, 1, 'NEW', 'delivery-v2-test');
INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, target_movement_analysis_id, reason)
VALUES (-1151, 2, 'INVALIDATION', 'delivery-v2-test', '가격 복귀');

DO $$
BEGIN
    BEGIN
        INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, movement_analysis_id)
        VALUES (-1151, 3, 'NEW', 'missing');
        RAISE EXCEPTION 'Missing analysis accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    BEGIN
        INSERT INTO tenant_delivery (tenant_id, cursor, delivery_type, target_movement_analysis_id, reason)
        VALUES (-1151, 3, 'INVALIDATION', 'missing', 'withdraw');
        RAISE EXCEPTION 'Missing withdrawal target accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    BEGIN
        DELETE FROM movement_analyses WHERE analysis_id = 'delivery-v2-test';
        RAISE EXCEPTION 'Delivered analysis deleted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;
    BEGIN
        UPDATE tenant_delivery SET reason = '  ' WHERE tenant_id = -1151 AND cursor = 2;
        RAISE EXCEPTION 'Blank withdrawal reason accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        UPDATE tenant_delivery SET reason = NULL WHERE tenant_id = -1151 AND cursor = 2;
        RAISE EXCEPTION 'Missing withdrawal reason accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    BEGIN
        UPDATE tenant_delivery SET reason = 'withdraw' WHERE tenant_id = -1151 AND cursor = 1;
        RAISE EXCEPTION 'New delivery with withdrawal reason accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;
    IF (SELECT count(*) FROM tenant_delivery WHERE tenant_id = -1151) <> 2 THEN
        RAISE EXCEPTION 'Delivery roundtrip changed';
    END IF;
    RAISE NOTICE 'Real v2 references, withdrawal reason and deletion protection verified';
END $$;
ROLLBACK;
