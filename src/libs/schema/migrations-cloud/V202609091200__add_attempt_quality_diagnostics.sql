-- 뉴스 품질 유실의 상위 사유와 표본을 물리 실행 시도에 결합한다 (ALPHA-1067).
--
-- 실패 원인이 아닌 successful-but-INCOMPLETE 진단이므로 failure_reason에 섞지 않는다.
-- 후속 writer가 S3 품질 로그의 제한된 요약만 저장하고 API가 같은 attempt에서 읽는다.
-- 과거 attempt는 측정하지 않은 행이라 백필하지 않는다. 크기 상한은 원문/무제한 목록이
-- 운영 원장에 복제되는 것을 막고, 상세 정본은 기존 S3 품질 로그에 그대로 둔다.

SET search_path TO public;
SET LOCAL lock_timeout = '3s';

ALTER TABLE ops_task_attempt
    ADD COLUMN quality_diagnostics JSONB,
    ADD CONSTRAINT ck_ops_task_attempt_quality_diagnostics CHECK (
        quality_diagnostics IS NULL
        OR (
            jsonb_typeof(quality_diagnostics) = 'object'
            AND octet_length(quality_diagnostics::text) <= 16384
        )
    ) NOT VALID;

COMMENT ON COLUMN ops_task_attempt.quality_diagnostics IS
'이 물리 시도의 bounded 품질 진단 요약(JSON object, 최대 16KiB). 상세 정본은 S3 품질 로그이며 NULL은 계측 없음이다.';
