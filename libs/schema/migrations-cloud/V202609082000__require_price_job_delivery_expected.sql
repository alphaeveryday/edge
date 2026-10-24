-- 검증된 CHECK를 근거로 table 재검사 없이 NOT NULL metadata를 닫고 임시 CHECK를 제거한다.

SET LOCAL lock_timeout = '3s';

ALTER TABLE price_window_job
    ALTER COLUMN delivery_expected SET NOT NULL;

ALTER TABLE price_window_job
    DROP CONSTRAINT ck_price_job_delivery_expected_not_null;
