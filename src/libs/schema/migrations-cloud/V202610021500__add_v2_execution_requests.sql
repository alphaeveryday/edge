-- Admission only. Existing publication and ingestion tables remain unchanged.
CREATE TABLE analysis_execution_requests (
    analysis_id TEXT PRIMARY KEY CHECK (analysis_id ~ '^[a-f0-9]{32}$'),
    source_event_id TEXT UNIQUE,
    input_json TEXT NOT NULL CHECK (jsonb_typeof(input_json::jsonb) = 'object'),
    execution_arn TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at TIMESTAMPTZ
);

COMMENT ON TABLE analysis_execution_requests IS
'v2 immutable request input and Step Functions acceptance. accepted_at is not analysis completion. Dedicated admission-role grants follow at activation.';
