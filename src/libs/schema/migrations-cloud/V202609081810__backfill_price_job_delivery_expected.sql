-- ALPHA-1066 — 확장 DDL이 commit되어 ACCESS EXCLUSIVE lock이 풀린 다음 기존 행만 복원한다.
--
-- 앞 migration이 기존 행에만 남긴 TRUE 중 과거 백필을 FALSE로 좁힌다. DDL commit 뒤
-- 구 writer 행은 default가 제거돼 NULL이므로 이 집합에 들어오지 않는다. 기존 행이 그 사이
-- claim/heartbeat로 새 tuple version을 얻어도 delivery_expected=TRUE 값 자체는 보존된다.

SET LOCAL lock_timeout = '3s';

UPDATE price_window_job j
   SET delivery_expected = FALSE
  FROM minute_ingestion_session s
 WHERE s.session_id = j.session_id
   AND j.delivery_expected IS TRUE
   AND (j.created_at AT TIME ZONE 'Asia/Seoul')::date > s.session_date
   AND NOT EXISTS (
       SELECT 1
         FROM dataset_commit_outbox o
        WHERE o.event_type = 'PriceWindowCommitted'
          AND o.aggregate_id = j.job_id
   );
