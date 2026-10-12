-- Envelopes are readable; the analysis consumer must never modify ingestion state.
BEGIN;
DO $$
DECLARE column_name text;
BEGIN
    FOREACH column_name IN ARRAY ARRAY['event_id','event_type','destination','payload','generation'] LOOP
        IF NOT has_column_privilege('edge_analysis_v2_reader','dataset_commit_outbox',column_name,'SELECT') THEN
            RAISE EXCEPTION 'Cannot verify event field: %',column_name;
        END IF;
    END LOOP;
    IF has_table_privilege('edge_analysis_v2_reader','dataset_commit_outbox','INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') THEN
        RAISE EXCEPTION 'Analysis reader can mutate ingestion outbox';
    END IF;
END $$;
SET LOCAL ROLE edge_analysis_v2_reader;
SELECT event_id,event_type,destination,payload,generation FROM dataset_commit_outbox LIMIT 0;
ROLLBACK;
