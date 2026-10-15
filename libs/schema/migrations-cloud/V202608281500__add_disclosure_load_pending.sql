-- ALPHA-1045: current disclosure manifest winners must survive loader/process failure.
SET LOCAL lock_timeout = '3s';

ALTER TABLE disclosure_fact
    ADD COLUMN is_current BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN disclosure_fact.is_current IS
'True for the latest parser winner. Removed correction ordinals are retained as false so immutable explanation lineage remains valid.';

CREATE TABLE disclosure_load_pending (
    rcept_no          TEXT PRIMARY KEY,
    disclosure_type   TEXT NOT NULL,
    -- TEXT is intentional: canonical Parquet may contain NaN/Infinity, which Python must retain
    -- so the loader can reject it loudly; PostgreSQL JSONB cannot represent those values.
    canonical_rows    TEXT NOT NULL,
    payload_sha256    CHAR(64) NOT NULL,
    source_fetched_at TIMESTAMPTZ NOT NULL,
    first_seen_run_id TEXT NOT NULL,
    last_seen_run_id  TEXT NOT NULL,
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt_count     INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_attempted_at TIMESTAMPTZ,
    last_error_code   TEXT,
    last_error        TEXT,
    CONSTRAINT ck_disclosure_load_pending_type
        CHECK (disclosure_type IN ('SUPPLY_CONTRACT', 'BUSINESS_SEGMENT')),
    CONSTRAINT ck_disclosure_load_pending_rows CHECK (length(canonical_rows) > 2),
    CONSTRAINT ck_disclosure_load_pending_sha
        CHECK (payload_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE disclosure_load_pending IS
'Completed disclosure manifest winners awaiting successful typed-fact load. Rows have no age expiry or lifetime retry cutoff: each eligible run tries each ID at most once (bounded runs match report_date; unbounded runs try all), success deletes it atomically, and failures remain observable through attempt_count/error fields.';

CREATE INDEX idx_disclosure_load_pending_retry
    ON disclosure_load_pending (last_attempted_at NULLS FIRST, first_seen_at, rcept_no);
