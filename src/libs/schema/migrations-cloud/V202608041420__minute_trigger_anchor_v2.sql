-- 판정식 v2 (ALPHA-745) — 가변 앵커 재발화, 2시간 쿨다운 폐지.
--
-- 규칙(판정기 price_consumer 가 유일 writer):
--   1) |close/세션시가 − 1| ≤ revert_threshold(1%): 발화 금지 구간. 앵커가 시가가
--      아니면 노출 회수 사건(ExposureReverted)을 발행하고 앵커를 시가로 리셋한다.
--   2) 그 외: |close/anchor − 1| ≥ abs_threshold(3%) 면 발화하고 앵커 ← 발화가.
-- 앵커 상태가 곧 "노출 회수 여부" 마커다 — 앵커=시가면 이미 회수됐거나 발화 전이라
-- 회수 사건이 중복 발행되지 않는다.

CREATE TABLE minute_trigger_anchor (
    session_id   TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    anchor_price NUMERIC(24, 6) NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, entity_id),
    CONSTRAINT fk_minute_trigger_anchor_session
        FOREIGN KEY (session_id) REFERENCES minute_ingestion_session (session_id),
    CONSTRAINT ck_minute_trigger_anchor_positive CHECK (anchor_price > 0)
);

COMMENT ON TABLE minute_trigger_anchor IS
'분봉 트리거 가변 앵커 (ALPHA-745). 행 존재 = 세션 중 한 번이라도 발화(anchor=발화가) 또는 회수 리셋(anchor=세션 시가). 행 부재 = 앵커가 세션 시가라는 뜻(첫 발화 전). writer 는 price_consumer 뿐.';

-- 쿨다운 유니크 은퇴 — 재발화 조건이 시간(2h 버킷)에서 가격(앵커 대비 3%·1% 복귀
-- 리셋)으로 바뀐다. 멱등 축은 window 로 옮긴다: 같은 window 재판정(재시도·정정 낡은
-- attempt 는 세대 잠금이 차단)은 같은 행에 DO NOTHING 이다.
ALTER TABLE minute_price_trigger ALTER COLUMN cooldown_bucket DROP NOT NULL;
ALTER TABLE minute_price_trigger DROP CONSTRAINT uq_minute_price_trigger_cooldown;
ALTER TABLE minute_price_trigger
    ADD CONSTRAINT uq_minute_price_trigger_window UNIQUE (entity_id, session_id, window_start);

-- v2 판정 근거 보존 — change_rate 가 앵커 대비가 되므로 어느 기준가였는지가 행에
-- 없으면 "왜 발화했나"를 재현할 수 없다. v1 행(앵커 개념 없음)은 NULL 로 남는다.
ALTER TABLE minute_price_trigger ADD COLUMN anchor_price NUMERIC(24, 6);

COMMENT ON COLUMN minute_price_trigger.cooldown_bucket IS
'v1(intraday-open-v1) 잔재 — 2h 쿨다운 버킷. v2(intraday-anchor-v2)부터 NULL(재발화 조건이 가격 축으로 이동, ALPHA-745).';
COMMENT ON COLUMN minute_price_trigger.change_rate IS
'판정 당시 |close/기준가 − 1|. v1 은 기준가=세션 시가, v2 는 기준가=anchor_price(직전 발화가 또는 시가). 시가 대비 변동은 open_price·close_price 로 재구성한다(엔진 observed_return 축).';
COMMENT ON COLUMN minute_price_trigger.anchor_price IS
'v2 판정 기준가(ALPHA-745) — 직전 발화가 또는 세션 시가. v1 행은 NULL.';
