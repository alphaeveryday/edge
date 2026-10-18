-- Public distributions only; customer holdings and receipts stay outside this store.
SET LOCAL lock_timeout = '3s';

CREATE TABLE cash_distribution_observation (
    market_code TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source_vendor TEXT NOT NULL,
    source_event_key TEXT NOT NULL,
    record_date DATE NOT NULL,
    ex_date DATE,
    pay_date DATE,
    amount_per_share NUMERIC,
    currency TEXT NOT NULL,
    distribution_kind TEXT NOT NULL,
    confirmation TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    availability_basis TEXT NOT NULL,
    raw_run_id TEXT NOT NULL,
    raw_key TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    canonical_run_id TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (market_code, ticker, source_vendor, source_event_key, raw_run_id),
    CHECK (market_code <> '' AND ticker <> '' AND source_vendor <> '' AND source_event_key <> ''),
    CHECK (amount_per_share IS NULL OR (amount_per_share >= 0 AND amount_per_share < 'Infinity'::numeric)),
    CHECK (currency ~ '^[A-Z]{3}$'),
    CHECK (confirmation IN ('pending', 'published', 'cancelled')),
    CHECK (distribution_kind IN ('cash', 'regular', 'special')),
    CHECK (confirmation <> 'published' OR (amount_per_share IS NOT NULL AND pay_date IS NOT NULL)),
    CHECK (pay_date IS NULL OR pay_date >= record_date),
    CHECK (availability_basis = 'received' AND available_at = received_at),
    CHECK (raw_sha256 ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX cash_distribution_lookup ON cash_distribution_observation
    (market_code, ticker, source_vendor, source_event_key, available_at DESC);

-- Choose a revision before filtering publication state: a later cancellation or
-- pending correction must not expose an older published amount as current.
CREATE VIEW cash_distribution_latest AS
SELECT DISTINCT ON (market_code, ticker, source_vendor, source_event_key) *
FROM cash_distribution_observation
ORDER BY market_code, ticker, source_vendor, source_event_key,
         available_at DESC, raw_run_id DESC;

COMMENT ON TABLE cash_distribution_observation IS
'One source observation of one security cash distribution per raw collection run. Amount is original-currency gross cash per share, not investor receipts. No reinvestment.';
