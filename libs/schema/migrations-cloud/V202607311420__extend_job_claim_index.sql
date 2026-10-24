-- job claim 인덱스를 실제 claim 경로에 맞게 교체 (ALPHA-664).
--
-- claim 은 PENDING / RETRY_WAIT(시각 도달) / **lease 만료 CLAIMED**(consumer crash 회수)
-- 세 조건을 훑는데, 기존 partial index 는 PENDING/RETRY_WAIT 만 덮어 CLAIMED 암이
-- 인덱스 밖이었다 — SUCCEEDED/DEAD 이력이 쌓이면 매 claim 이 전체 스캔이 된다.
-- ORDER BY created_at 스캔에 맞춰 키도 created_at 으로 둔다(잔여 조건은 heap filter —
-- 활성 집합이 작아 충분). 신규 테이블(빈 상태)이라 잠금 부담 없다.

SET search_path TO public;

DROP INDEX idx_news_job_eligible;
CREATE INDEX idx_news_job_eligible
    ON news_extraction_job (created_at)
    WHERE status IN ('PENDING','RETRY_WAIT','CLAIMED');

DROP INDEX idx_price_job_eligible;
CREATE INDEX idx_price_job_eligible
    ON price_window_job (created_at)
    WHERE status IN ('PENDING','RETRY_WAIT','CLAIMED');
