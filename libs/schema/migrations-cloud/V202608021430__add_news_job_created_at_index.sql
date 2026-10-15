-- /minute 콘솔의 뉴스 job 날짜 집계용 인덱스 (ALPHA-651).
--
-- 콘솔은 created_at 반개구간(KST 하루)으로 전 상태를 집계하는데, 기존
-- idx_news_job_eligible 은 PENDING/RETRY_WAIT partial 이라 terminal(SUCCEEDED/DEAD)
-- 포함 집계는 못 탄다. 60초 자동 갱신 화면이라 이력 누적 후 매분 풀스캔이 된다.

SET search_path TO public;

CREATE INDEX idx_news_job_created_at ON news_extraction_job (created_at);
