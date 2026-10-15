-- 1분 가격·뉴스 파이프라인 ingestion 원장 (ALPHA-661, 계획 §7 / v0.7 10.1~10.5).
--
-- PostgreSQL 이 job identity·재시도 권위의 **SSOT** 이고 SQS 는 delivery transport 다
-- (v0.7 12.4 — 재비교 금지, 21절에 기각 근거). 이 테이블들은 ops_* 관측 원장과 달리
-- 실행을 **제어**한다: Worker 가 window 를 claim 하고, Consumer 가 job 을 claim 한다.
--
-- 설계 불변식:
-- - session 은 하루의 논리 identity — UNIQUE (dataset, source_group, session_date).
--   `universe_version` 은 identity 가 아니라 생성 시 고정되는 **속성**이다. 같은 날짜
--   non-finalized session 에 다른 universe 가 오면 새 session 을 만들지 않고 실패시킨다
--   (장중 universe 교체는 session_epoch 설계 전까지 불허 — v0.7 10.1).
-- - 가격 135,720개(348 unit × 390 window) symbol별 상태를 행으로 복제하지 않는다.
--   canonical bar 가 성공 symbol 의 사실이고 상세 목록은 S3 manifest 가 정본,
--   `missing_units` JSONB 는 작은 cache 다 (v0.7 10.2).
-- - 뉴스와 가격 job 은 identity 가 한 컬럼도 겹치지 않으므로 범용 job 테이블(JSONB
--   identity)로 합치지 않는다 — dedupe 제약이 DB UNIQUE 에서 코드로 이동하기 때문.
--   lifecycle 컬럼 블록은 코드(repository 파라미터화)로만 공유한다 (v0.7 10.5).
-- - CHECK 는 단일 컬럼 어휘 검증에 한정한다(ops 원장과 같은 결). 어휘는
--   data_pipeline/minute/states.py 상수와 **정확히 일치**해야 한다(테스트로 강제).
-- - ID 는 ADR-0027 계열(TEXT, ULID 또는 결정적 sha256). FK 는 원장 내부로만 건다.

SET search_path TO public;

-- ── minute_ingestion_session — 하루의 논리 session ─────────────────────
CREATE TABLE minute_ingestion_session (
    session_id                  TEXT NOT NULL,
    dataset                     TEXT NOT NULL,
    source_group                TEXT NOT NULL,
    session_date                DATE NOT NULL,
    -- 생성 시 고정되는 속성(identity 아님) — 다른 universe 제시는 INSERT 거부로 드러난다
    universe_version            TEXT NOT NULL,
    universe_hash               TEXT NOT NULL,
    phase                       TEXT NOT NULL DEFAULT 'PLANNED',
    expected_window_count       INTEGER NOT NULL,
    -- 결과가 기록된 가장 최신 구간 — 앞쪽 hole 이 있어도 전진한다
    processed_through           TIMESTAMPTZ,
    -- 처음부터 VALID/VALID_EMPTY 연속인 마지막 구간 — hole 에서 멈춘다
    contiguous_complete_through TIMESTAMPTZ,
    -- correction 대기기간이 업무상 필요할 때만 사용
    finalized_through           TIMESTAMPTZ,
    worker_fencing_token        BIGINT NOT NULL DEFAULT 0,
    lease_expires_at            TIMESTAMPTZ,
    heartbeat_at                TIMESTAMPTZ,
    drain_requested_at          TIMESTAMPTZ,
    drain_ack_at                TIMESTAMPTZ,
    final_generation            INTEGER,
    final_checksum              TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id),
    CONSTRAINT uq_minute_session_identity UNIQUE (dataset, source_group, session_date),
    CONSTRAINT ck_minute_session_phase CHECK (
        phase IN ('PLANNED','ACTIVE','DRAINING','DRAINED','QC_RUNNING','FINALIZED','FAILED')
    ),
    CONSTRAINT ck_minute_session_expected_windows CHECK (expected_window_count >= 0)
);

COMMENT ON TABLE minute_ingestion_session IS
'1분 파이프라인의 하루 논리 session. (dataset, source_group, session_date) 가 identity 이고 universe_version/hash 는 고정 속성이다. fencing token 으로 duplicate Worker 를 막는다 (v0.7 10.1).';

-- ── minute_ingestion_window — 명시적 1분 half-open window ──────────────
CREATE TABLE minute_ingestion_window (
    session_id           TEXT NOT NULL,
    window_start         TIMESTAMPTZ NOT NULL,
    window_end           TIMESTAMPTZ NOT NULL,
    scheduled_at         TIMESTAMPTZ NOT NULL,
    data_status          TEXT NOT NULL DEFAULT 'DUE',
    expected_unit_count  INTEGER,
    succeeded_unit_count INTEGER,
    failed_unit_count    INTEGER,
    record_count         INTEGER,
    generation           INTEGER NOT NULL DEFAULT 0,  -- 0 = 아직 커밋 없음, 첫 커밋이 1
    checksum             TEXT,
    manifest_uri         TEXT,
    manifest_checksum    TEXT,
    -- 작은 cache — 상세 missing/invalid 목록의 정본은 S3 manifest (v0.7 10.2)
    missing_units        JSONB,
    claimed_by           TEXT,
    claim_token          BIGINT,
    lease_expires_at     TIMESTAMPTZ,
    attempt_count        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at      TIMESTAMPTZ,
    stage_timestamps     JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, window_start),
    CONSTRAINT fk_minute_window_session
        FOREIGN KEY (session_id) REFERENCES minute_ingestion_session (session_id),
    CONSTRAINT ck_minute_window_half_open CHECK (window_start < window_end),
    CONSTRAINT ck_minute_window_status CHECK (
        data_status IN ('DUE','CLAIMED','VALID','VALID_EMPTY','INCOMPLETE','MISSING','INVALID')
    ),
    -- 코드(CollectionResult)의 ge=0 계약을 DB 도 보존한다 — 직접 SQL·버그 증감이
    -- 음수 수량을 영구 기록하면 QC 집계가 오염된다
    CONSTRAINT ck_minute_window_counts CHECK (
        (expected_unit_count  IS NULL OR expected_unit_count  >= 0) AND
        (succeeded_unit_count IS NULL OR succeeded_unit_count >= 0) AND
        (failed_unit_count    IS NULL OR failed_unit_count    >= 0) AND
        (record_count         IS NULL OR record_count         >= 0)
    ),
    CONSTRAINT ck_minute_window_generation CHECK (generation >= 0)
);

COMMENT ON TABLE minute_ingestion_window IS
'session 의 명시적 1분 half-open window 원장. 장 시작 시 하루치를 미리 materialize 해 프로세스가 안 떠도 MISSING 으로 드러난다. realtime/recovery 조회 인덱스는 PR 2B 의 실제 쿼리와 함께 추가한다.';

-- ── news_source_item — 소스 관측 identity (BigKinds NEWS_ID) ───────────
CREATE TABLE news_source_item (
    source_code          TEXT NOT NULL,
    source_item_id       TEXT NOT NULL,   -- BigKinds 는 NEWS_ID
    canonical_article_id TEXT,
    first_seen_at        TIMESTAMPTZ NOT NULL,
    last_seen_at         TIMESTAMPTZ NOT NULL,
    content_checksum     TEXT NOT NULL,
    generation           INTEGER NOT NULL DEFAULT 1,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (source_code, source_item_id)
);

COMMENT ON TABLE news_source_item IS
'소스가 준 기사 identity 의 관측 원장. 같은 NEWS_ID 재관측은 last_seen_at 갱신, 본문 변경은 content_checksum 변화 + generation 증가로 드러난다 (v0.7 10.3).';

-- ── news_extraction_job — 기사별 LLM 추출 job (SSOT) ───────────────────
CREATE TABLE news_extraction_job (
    -- 결정적 ID: sha256([source_code, article_id, input_fingerprint,
    --                    tagger_version, ontology_version]) — v0.7 10.6 유도식
    job_id             TEXT NOT NULL,
    source_code        TEXT NOT NULL,
    article_id         TEXT NOT NULL,
    input_fingerprint  TEXT NOT NULL,
    tagger_version     TEXT NOT NULL,
    ontology_version   TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'PENDING',
    claimed_by         TEXT,
    lease_expires_at   TIMESTAMPTZ,
    attempt_count      INTEGER NOT NULL DEFAULT 0,
    next_attempt_at    TIMESTAMPTZ,
    redrive_generation INTEGER NOT NULL DEFAULT 0,
    result_checksum    TEXT,
    error_code         TEXT,
    completed_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (job_id),
    CONSTRAINT uq_news_extraction_job_identity UNIQUE (
        source_code, article_id, input_fingerprint, tagger_version, ontology_version
    ),
    CONSTRAINT ck_news_job_status CHECK (
        status IN ('PENDING','CLAIMED','SUCCEEDED','RETRY_WAIT','DEAD')
    )
);

-- 조회는 처리 대기분만 본다 — partial index 면 충분 (v0.7 10.5 와 같은 결)
CREATE INDEX idx_news_job_eligible
    ON news_extraction_job (next_attempt_at)
    WHERE status IN ('PENDING','RETRY_WAIT');

COMMENT ON TABLE news_extraction_job IS
'기사별 LLM 추출 논리 job 의 SSOT. retry 자격·attempt 수·다음 시각의 권위는 이 테이블에만 있고 SQS 는 wake-up transport 다 (v0.7 12.4). 수동 redrive 만 redrive_generation 을 올린다.';

-- ── price_window_job — window 단위 가격 분석 job (v0.7 10.5 정본) ──────
CREATE TABLE price_window_job (
    -- 결정적 ID: sha256([session_id, window_start, generation, trigger_schema_version])
    job_id                 TEXT NOT NULL,
    session_id             TEXT NOT NULL,
    window_start           TIMESTAMPTZ NOT NULL,
    generation             INTEGER NOT NULL,
    trigger_schema_version TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'PENDING',
    claimed_by             TEXT,
    lease_expires_at       TIMESTAMPTZ,
    attempt_count          INTEGER NOT NULL DEFAULT 0,
    next_attempt_at        TIMESTAMPTZ,
    redrive_generation     INTEGER NOT NULL DEFAULT 0,
    result_checksum        TEXT,
    error_code             TEXT,
    completed_at           TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (job_id),
    -- session 이 아니라 **window 행**을 참조한다 — 존재하지 않는 window 의 job 이
    -- 들어오면 stale-generation claim 조인이 빈손이 돼 job 이 고착된다. window 는
    -- 장 시작 시 미리 materialize 되므로(v0.7 6절) job 이 window 를 앞설 수 없다.
    CONSTRAINT fk_price_window_job_window
        FOREIGN KEY (session_id, window_start)
        REFERENCES minute_ingestion_window (session_id, window_start),
    CONSTRAINT uq_price_window_job_identity UNIQUE (
        session_id, window_start, generation, trigger_schema_version
    ),
    CONSTRAINT ck_price_job_status CHECK (
        status IN ('PENDING','CLAIMED','SUCCEEDED','RETRY_WAIT','DEAD')
    ),
    -- window generation 0 = 미커밋. job 은 커밋된 generation(>=1)에만 존재한다 —
    -- generation 0 job 은 stale 검사(job.gen < window.gen)를 그대로 통과해 버린다
    CONSTRAINT ck_price_job_generation CHECK (generation >= 1)
);

CREATE INDEX idx_price_job_eligible
    ON price_window_job (next_attempt_at)
    WHERE status IN ('PENDING','RETRY_WAIT');

COMMENT ON TABLE price_window_job IS
'job 단위는 window 다 — PriceWindowCommitted 1개 ↔ job 1개, ETF 는 결과의 granularity (v0.7 10.5). stale 거부는 claim 시점 한 곳: job.generation < window 현재 generation 이면 DEAD(''STALE'') CAS. price_movement_trigger·explanation_run 은 job 의 출력이지 job 이 아니다 — 분 단위 트리거 저장 위치는 trigger schema owner 결정 대기, 운영 lease/retry 컬럼을 분석 도메인 테이블에 넣지 않는다.';
