-- 기존 행 scan은 ACCESS EXCLUSIVE를 보유하지 않는 별도 validation 단계에서 수행한다.

SET LOCAL lock_timeout = '3s';

ALTER TABLE price_window_job
    VALIDATE CONSTRAINT ck_price_job_delivery_expected_not_null;
