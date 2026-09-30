-- §33.12 로컬 실험(미커밋): 가격 판정이 실제로 쓴 기준선·앵커와 확정 결과를 판정 시도 단위로 남긴다.
-- writer 는 price_consumer 하나다. 발화·회수·무발화 모두 같은 트랜잭션에서 claim(job FOR UPDATE)과
-- window 세대(FOR UPDATE)를 대조한 뒤 쓴다. 무발화도 기록 실패·세대 정정·소유권 상실이면 성공하지 않는다.
SET LOCAL lock_timeout = '3s';
SET search_path TO public;

CREATE TABLE minute_price_baseline_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES minute_ingestion_session (session_id),
    entity_id   TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('prev_close', 'open_fallback')),
    ref         TEXT NOT NULL,          -- 전일 종가 기준일 | 시가 출처 window@generation
    value       NUMERIC NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE minute_price_baseline_set (
    set_id      TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    snapshot_id TEXT REFERENCES minute_price_baseline_snapshot (snapshot_id), -- NULL = 기록 도입 전 확정된 시가
    PRIMARY KEY (set_id, entity_id)
);

CREATE TABLE minute_price_judgment (
    job_id                   TEXT NOT NULL REFERENCES price_window_job (job_id),
    redrive_generation       INTEGER NOT NULL,
    attempt                  INTEGER NOT NULL,
    session_id               TEXT NOT NULL,
    window_start             TIMESTAMPTZ NOT NULL,
    generation               INTEGER NOT NULL,
    detection_policy_version TEXT NOT NULL,
    abs_threshold            NUMERIC NOT NULL,
    revert_threshold         NUMERIC NOT NULL,
    baseline_set_id          TEXT NOT NULL,
    summary                  JSONB NOT NULL,
    anchors_used             JSONB NOT NULL,   -- 판정이 읽은 앵커 스냅샷(행이 있던 종목만)
    tx_anchor                JSONB NOT NULL,   -- 기록 tx 안에서 관측한 앵커 창
    tx_anchor_locked         BOOLEAN NOT NULL, -- true=발화·회수 대상 앵커 행을 잠근 뒤 관측, false=무발화의 비잠금 관측
    judged_at                TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), -- 관측 시각일 뿐 순서 아님
    PRIMARY KEY (job_id, redrive_generation, attempt)
);
