-- NOT NULL 전환의 짧은 DDL lock만 잡는다. 검증 scan은 다음 transaction으로 분리한다.

SET LOCAL lock_timeout = '3s';

ALTER TABLE price_window_job
    ADD CONSTRAINT ck_price_job_delivery_expected_not_null
    CHECK (delivery_expected IS NOT NULL) NOT VALID;
