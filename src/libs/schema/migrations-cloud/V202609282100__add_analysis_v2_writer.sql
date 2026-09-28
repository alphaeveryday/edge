-- Dedicated result writer. Credentials are provisioned separately, never in Flyway.
-- Fail on an existing identity rather than inheriting unreviewed privileges.
CREATE ROLE edge_analysis_v2_writer NOLOGIN NOSUPERUSER NOINHERIT
    NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5;
COMMENT ON ROLE edge_analysis_v2_writer IS 'edge-analysis-v2-result-writer';
ALTER ROLE edge_analysis_v2_writer SET statement_timeout = '15s';
ALTER ROLE edge_analysis_v2_writer SET idle_in_transaction_session_timeout = '30s';

GRANT USAGE ON SCHEMA public TO edge_analysis_v2_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    movement_analyses, movement_items, outlook_analyses, outlook_items,
    outlook_factors, outlook_conclusion_keywords
TO edge_analysis_v2_writer;

-- Tool evidence is append-only for the application, including its definitions.
GRANT SELECT, INSERT ON tool_definitions, tool_runs TO edge_analysis_v2_writer;
