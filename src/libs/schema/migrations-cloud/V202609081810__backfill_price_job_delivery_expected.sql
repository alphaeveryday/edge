-- ALPHA-1066 — 확장 DDL이 commit되어 ACCESS EXCLUSIVE lock이 풀린 다음 기존 행만 복원한다.
--
-- 앞 migration의 snapshot에 보이던, 즉 DDL 전에 commit이 끝난 행만 복원한다. DDL 전에
-- transaction을 시작했어도 INSERT가 DDL 뒤에 commit된 구 writer 행은 snapshot에 보이지 않아
-- NULL로 남는다. `created_at`은 transaction-start 시각이라 이 경계를 대신할 수 없다.

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
       migration_alpha1066_price_delivery_snapshot m
 WHERE s.session_id = j.session_id
   AND j.delivery_expected IS NULL
   AND pg_visible_in_snapshot(j.xmin::text::xid8, m.pre_ddl_snapshot);

DROP TABLE migration_alpha1066_price_delivery_snapshot;
