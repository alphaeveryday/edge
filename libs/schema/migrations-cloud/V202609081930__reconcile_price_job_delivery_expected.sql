-- ALPHA-1066 — schema→writer 전환 구간의 미기록 전달 의도를 복원한다.
--
-- 구 writer도 realtime job과 PriceWindowCommitted outbox를 같은 transaction에 썼고
-- backfill은 event를 의도적으로 생략했다(ALPHA-863). 따라서 nullable 확장 뒤 생긴 NULL은
-- event 존재 여부로 손실 없이 구분된다. 새 writer image 적용·구 worker 0 확인 뒤 실행한다.

SET LOCAL lock_timeout = '3s';

UPDATE price_window_job j
   SET delivery_expected = EXISTS (
       SELECT 1
         FROM dataset_commit_outbox o
        WHERE o.event_type = 'PriceWindowCommitted'
          AND o.aggregate_id = j.job_id
   )
 WHERE j.delivery_expected IS NULL;
