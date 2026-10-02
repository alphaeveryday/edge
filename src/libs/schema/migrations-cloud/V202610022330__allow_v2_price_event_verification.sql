-- The source reader verifies queue envelopes against the ingestion outbox.
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='edge_analysis_v2_reader') THEN
        CREATE ROLE edge_analysis_v2_reader NOLOGIN;
    END IF;
END $$;
GRANT SELECT(event_id,event_type,destination,payload,generation)
    ON dataset_commit_outbox TO edge_analysis_v2_reader;
