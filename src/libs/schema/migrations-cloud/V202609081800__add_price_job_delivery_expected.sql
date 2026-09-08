-- ALPHA-1066 — price job의 outbox 발행 의도를 영구 기록한다.
--
-- 과거일 백필은 job을 남기되 실시간 판정 event를 의도적으로 만들지 않는다(ALPHA-863).
-- session_date나 조회 현재 시각으로 이 의도를 다시 추정하면 자정 뒤 실시간 고착이 백필로
-- 오분류된다. writer가 commit 시점의 emit_outbox 결정을 이 컬럼에 같이 기록한다.

-- intraday writer의 짧은 transaction을 오래 막지 않는다. lock을 바로 못 얻으면 다음
-- schema-migrate 재실행에서 다시 시도한다.
SET LOCAL lock_timeout = '3s';

-- schema와 writer는 별도 배포다. ADD 시점의 constant DEFAULT는 PG 11+ fast default로 기존
-- tuple에만 TRUE를 읽히게 한 뒤 같은 transaction에서 즉시 제거한다. 그래서 DDL 전에 있던
-- 행은 이후 status UPDATE로 tuple xmin이 바뀌어도 TRUE를 보존하고, DDL commit 뒤 구 writer가
-- 컬럼을 생략해 만든 행은 NULL(의도 미기록)이다. writer 전환 뒤 별도 수축 단계에서 닫는다.
ALTER TABLE price_window_job
    ADD COLUMN delivery_expected BOOLEAN DEFAULT TRUE;

ALTER TABLE price_window_job
    ALTER COLUMN delivery_expected DROP DEFAULT;

COMMENT ON COLUMN price_window_job.delivery_expected IS
'이 job의 현재 세대가 PriceWindowCommitted outbox를 가져야 하는지 여부. 실시간=true, 과거일 백필=false(ALPHA-863), 구 writer 전환 구간의 미기록=NULL.';
