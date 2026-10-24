-- API reads only published analysis records. Credentials are provisioned separately.
CREATE ROLE edge_analysis_v2_api_reader NOLOGIN NOSUPERUSER NOINHERIT
    NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5;
COMMENT ON ROLE edge_analysis_v2_api_reader IS 'edge-analysis-v2-api-result-reader';
ALTER ROLE edge_analysis_v2_api_reader SET default_transaction_read_only = on;
ALTER ROLE edge_analysis_v2_api_reader SET statement_timeout = '5s';
ALTER ROLE edge_analysis_v2_api_reader SET idle_in_transaction_session_timeout = '10s';
GRANT USAGE ON SCHEMA public TO edge_analysis_v2_api_reader;
GRANT SELECT ON movement_analyses, movement_items, outlook_analyses, outlook_items,
    outlook_factors, outlook_conclusion_keywords, outlook_factor_metrics, outlook_issue_items
TO edge_analysis_v2_api_reader;
