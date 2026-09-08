-- ALPHA-1066 — price job의 outbox 발행 의도를 영구 기록한다.
--
-- 과거일 백필은 job을 남기되 실시간 판정 event를 의도적으로 만들지 않는다(ALPHA-863).
-- session_date나 조회 현재 시각으로 이 의도를 다시 추정하면 자정 뒤 실시간 고착이 백필로
-- 오분류된다. writer가 commit 시점의 emit_outbox 결정을 이 컬럼에 같이 기록한다.

-- intraday writer의 짧은 transaction을 오래 막지 않는다. lock을 바로 못 얻으면 다음
-- schema-migrate 재실행에서 다시 시도한다.
SET LOCAL lock_timeout = '3s';

-- schema와 writer는 별도 배포다. 새 writer가 올라오기 전 구 writer가 만든 행은 NULL로
-- 남겨 "발행 의도 미기록"을 보존한다. 이를 TRUE로 default하면 그 사이의 과거 백필이
-- 영구 전달 실패로 오분류된다. writer 전환·검증 뒤 별도 수축 단계에서 NOT NULL로 닫는다.
ALTER TABLE price_window_job
    ADD COLUMN delivery_expected BOOLEAN;

COMMENT ON COLUMN price_window_job.delivery_expected IS
'이 job의 현재 세대가 PriceWindowCommitted outbox를 가져야 하는지 여부. 실시간=true, 과거일 백필=false(ALPHA-863), 구 writer 전환 구간의 미기록=NULL.';
