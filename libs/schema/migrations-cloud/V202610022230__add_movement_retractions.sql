SET LOCAL lock_timeout = '5s';
CREATE TABLE movement_retractions (
    event_id text PRIMARY KEY,
    etf_code text NOT NULL,
    session_id text NOT NULL,
    window_start timestamptz NOT NULL
);
CREATE INDEX movement_retractions_target ON movement_retractions(etf_code,session_id,window_start);
GRANT SELECT, INSERT ON movement_retractions TO edge_analysis_v2_writer;
GRANT SELECT, INSERT ON analysis_execution_requests TO edge_analysis_v2_writer;
GRANT UPDATE(accepted_at) ON analysis_execution_requests TO edge_analysis_v2_writer;
GRANT SELECT(delivery_type,target_movement_analysis_id),
    INSERT(target_movement_analysis_id,reason) ON tenant_delivery TO edge_analysis_v2_writer;
