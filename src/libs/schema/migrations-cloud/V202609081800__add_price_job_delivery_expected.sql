-- ALPHA-1066 — price job의 outbox 발행 의도를 영구 기록한다.
--
-- 과거일 백필은 job을 남기되 실시간 판정 event를 의도적으로 만들지 않는다(ALPHA-863).
-- session_date나 조회 현재 시각으로 이 의도를 다시 추정하면 자정 뒤 실시간 고착이 백필로
-- 오분류된다. writer가 commit 시점의 emit_outbox 결정을 이 컬럼에 같이 기록한다.

ALTER TABLE price_window_job
    ADD COLUMN delivery_expected BOOLEAN NOT NULL DEFAULT TRUE;

-- 기존 행은 event가 있으면 발행 대상이다. event가 없고 세션일 뒤 생성된 job만 당시 계약의
-- 과거일 백필로 복원한다. 이후 행은 writer가 명시하므로 이 추정에 의존하지 않는다.
UPDATE price_window_job j
   SET delivery_expected = FALSE
  FROM minute_ingestion_session s
 WHERE s.session_id = j.session_id
   AND (j.created_at AT TIME ZONE 'Asia/Seoul')::date > s.session_date
   AND NOT EXISTS (
       SELECT 1
         FROM dataset_commit_outbox o
        WHERE o.event_type = 'PriceWindowCommitted'
          AND o.aggregate_id = j.job_id
   );

COMMENT ON COLUMN price_window_job.delivery_expected IS
'이 job의 현재 세대가 PriceWindowCommitted outbox를 가져야 하는지 여부. 실시간=true, 과거일 백필=false(ALPHA-863). 조회 시각으로 재추정하지 않는다.';
