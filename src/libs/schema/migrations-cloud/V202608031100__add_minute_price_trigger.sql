-- 1분 가격 트리거 판정의 저장 표면 (ALPHA-708, 2026-08-02 설계 확정).
-- v0.7 10.5 가 "분 단위 트리거 저장 위치는 trigger schema owner 결정 대기"로 열어 둔
-- 자리가 이 확정(시가 대비·쿨다운 2h 버킷)으로 닫혔다. 판정식·임계의 정본은 분석엔진
-- 소관이고, 여기는 그 확정 규칙의 저장 계약만 정의한다.

-- ── minute_session_open — 세션×종목 시가 원장 ─────────────────────────
-- 시가 = 그날 첫 분봉 open. **확정 후 불변**이다(INSERT ... DO NOTHING) — 판정
-- Consumer 재시작·경쟁 실행이 같은 값을 다시 확정해도 no-op 이고, 첫 window 가
-- missing/invalid 라 시가가 없으면 그 사실을 **사유와 함께** 남긴다(조용히 건너뛰면
-- 그 ETF 가 하루 종일 판정에서 빠진 이유가 어디에도 없다).
CREATE TABLE minute_session_open (
    session_id   TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    status       TEXT NOT NULL,
    -- OPEN 이면 필수, MISSING 이면 NULL — CHECK 로 결속한다(한쪽만 있는 행은
    -- "시가가 있는데 없다고" 또는 그 반대로 읽힌다)
    open_price   NUMERIC(24, 6),
    reason       TEXT,
    -- 시가를 뽑은(또는 부재를 확정한) 근거 window
    source_window TIMESTAMPTZ NOT NULL,
    decided_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, entity_id),
    CONSTRAINT fk_minute_session_open_session
        FOREIGN KEY (session_id) REFERENCES minute_ingestion_session (session_id),
    CONSTRAINT ck_minute_open_status CHECK (status IN ('OPEN', 'MISSING')),
    CONSTRAINT ck_minute_open_price CHECK (
        (status = 'OPEN' AND open_price IS NOT NULL)
        OR (status = 'MISSING' AND open_price IS NULL AND reason IS NOT NULL)
    )
);

COMMENT ON TABLE minute_session_open IS
'세션×종목 시가 원장 (ALPHA-708). 시가=그날 첫 분봉 open, 확정 후 불변. MISSING 은 첫 window 가 커밋됐는데 그 종목 레코드가 없거나(missing) 계약 위반(invalid)이라는 판정 기록이다 — reason 필수.';

-- ── minute_price_trigger — 장중 가격 트리거 (쿨다운 = UNIQUE 한 줄) ────
-- 쿨다운 2시간 버킷: cooldown_bucket = floor(epoch(window_start) / 7200).
-- UNIQUE(entity_id, cooldown_bucket) + ON CONFLICT DO NOTHING 이 재무장 상태머신을
-- 대체한다 — 같은 버킷의 두 번째 발화는 행도 event 도 만들지 않는다.
CREATE TABLE minute_price_trigger (
    -- 결정적 ID: sha256([entity_id, cooldown_bucket, detection_policy_version]) 파생
    trigger_id               TEXT NOT NULL,
    entity_id                TEXT NOT NULL,
    session_id               TEXT NOT NULL,
    window_start             TIMESTAMPTZ NOT NULL,
    generation               INTEGER NOT NULL,
    detection_policy_version TEXT NOT NULL,
    open_price               NUMERIC(24, 6) NOT NULL,
    close_price              NUMERIC(24, 6) NOT NULL,
    -- |close/open - 1| — 판정 당시 값을 그대로 남긴다(사후 재계산은 정정 세대에서
    -- 다른 값이 나올 수 있어 "왜 발화했나"의 근거가 안 된다)
    change_rate              NUMERIC(12, 8) NOT NULL,
    threshold                NUMERIC(12, 8) NOT NULL,
    cooldown_bucket          BIGINT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (trigger_id),
    CONSTRAINT fk_minute_price_trigger_session
        FOREIGN KEY (session_id) REFERENCES minute_ingestion_session (session_id),
    CONSTRAINT uq_minute_price_trigger_cooldown UNIQUE (entity_id, cooldown_bucket),
    CONSTRAINT ck_minute_trigger_generation CHECK (generation >= 1)
);

COMMENT ON TABLE minute_price_trigger IS
'장중 가격 트리거 (ALPHA-708). 판정: |현재봉 close / 세션 시가 - 1| >= threshold, 대상은 universe.etf_ids. 쿨다운은 UNIQUE(entity_id, cooldown_bucket) 이 정본 — 트리거 행과 outbox event 는 한 트랜잭션이고, 버킷 충돌(DO NOTHING)이면 event 도 없다. 기존 price_movement_trigger(일 단위, prev_close 대비)와 축이 다르다 — detection_policy_version 으로 갈린다.';
