-- ALPHA-1066 — 확장 DDL이 commit되어 ACCESS EXCLUSIVE lock이 풀린 다음 기존 행만 복원한다.
--
-- cutoff는 이 migration 파일이 만들어진 시각이다. 실제 dev 적용은 그 뒤이므로, 앞 migration
-- commit 이후 구 writer가 새로 만든 rollout 행은 이 시각보다 늦어 NULL로 남는다. `now()`를
-- 쓰면 두 migration 사이에 들어온 행까지 추정해 의도 미기록이라는 사실을 잃는다.

SET LOCAL lock_timeout = '3s';

UPDATE price_window_job j
   SET delivery_expected = CASE
       WHEN EXISTS (
           SELECT 1
             FROM dataset_commit_outbox o
            WHERE o.event_type = 'PriceWindowCommitted'
              AND o.aggregate_id = j.job_id
       ) THEN TRUE
       WHEN (j.created_at AT TIME ZONE 'Asia/Seoul')::date > s.session_date THEN FALSE
       ELSE TRUE
   END
  FROM minute_ingestion_session s
 WHERE s.session_id = j.session_id
   AND j.delivery_expected IS NULL
   AND j.created_at < TIMESTAMPTZ '2026-09-08 18:10:00 Asia/Seoul';
