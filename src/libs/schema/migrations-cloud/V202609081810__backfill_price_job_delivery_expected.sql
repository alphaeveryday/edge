-- ALPHA-1066 — 확장 DDL이 commit되어 ACCESS EXCLUSIVE lock이 풀린 다음 기존 행만 복원한다.
--
-- cutoff는 앞 migration이 각 환경의 DB 시계로 기록했다. 앞 migration commit 이후 구 writer가
-- 새로 만든 rollout 행은 cutoff보다 늦어 NULL로 남는다. 여기서 `now()`를 쓰면 두 migration
-- 사이에 들어온 행까지 추정해 의도 미기록이라는 사실을 잃는다.

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
  FROM minute_ingestion_session s,
       migration_alpha1066_price_delivery_cutoff c
 WHERE s.session_id = j.session_id
   AND j.delivery_expected IS NULL
   AND j.created_at < c.cutoff_at;

DROP TABLE migration_alpha1066_price_delivery_cutoff;
