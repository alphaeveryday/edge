SET LOCAL lock_timeout = '3s';

CREATE TABLE cash_distribution_coverage (
    market_code TEXT NOT NULL,
    ticker TEXT NOT NULL,
    source_vendor TEXT NOT NULL,
    coverage_from DATE NOT NULL,
    coverage_to DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'failed')),
    observations_sha256 TEXT NOT NULL CHECK (observations_sha256 ~ '^[0-9a-f]{64}$'),
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
    PRIMARY KEY (market_code, ticker, source_vendor, raw_run_id),
    CHECK (market_code <> '' AND ticker <> '' AND source_vendor <> ''),
    CHECK (coverage_from <= coverage_to),
    CHECK (availability_basis = 'received' AND available_at = received_at),
    CHECK (raw_sha256 ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX cash_distribution_coverage_lookup ON cash_distribution_coverage
    (market_code, ticker, source_vendor, available_at DESC);

COMMENT ON TABLE cash_distribution_coverage IS
'Public provider query coverage per security and raw run, including successful empty responses. Complete describes the validated requested interval, not guaranteed vendor completeness. Raw provenance identifies the immutable collection manifest.';

GRANT SELECT ON cash_distribution_latest, cash_distribution_coverage
    TO edge_analysis_v2_api_reader;
