-- Run as the migration administrator after the cloud migrations, with ON_ERROR_STOP.
-- All test rows and temporary SET ROLE state are rolled back.
BEGIN;
DO $$
DECLARE
    r record;
    results text[] := ARRAY['movement_analyses','movement_items','outlook_analyses',
        'outlook_items','outlook_factors','outlook_conclusion_keywords',
        'outlook_factor_metrics','outlook_issue_items'];
    audit text[] := ARRAY['tool_definitions','tool_runs'];
BEGIN
    SELECT * INTO STRICT r FROM pg_roles WHERE rolname = 'edge_analysis_v2_writer';
    IF r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication
       OR r.rolbypassrls OR r.rolinherit THEN
        RAISE EXCEPTION 'Writer has elevated role attributes';
    END IF;
    IF EXISTS (SELECT FROM pg_auth_members WHERE member = r.oid) THEN
        RAISE EXCEPTION 'Writer inherits another role';
    END IF;
    FOR r IN SELECT c.oid, c.relname, n.nspname FROM pg_class c
             JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') LOOP
        IF r.relname = ANY(results || audit) THEN
            IF NOT has_table_privilege('edge_analysis_v2_writer',r.oid,'SELECT')
               OR NOT has_table_privilege('edge_analysis_v2_writer',r.oid,'INSERT') THEN
                RAISE EXCEPTION 'Missing result permission: %',r.relname;
            END IF;
            IF has_table_privilege('edge_analysis_v2_writer',r.oid,'TRUNCATE,TRIGGER,REFERENCES') THEN
                RAISE EXCEPTION 'Unexpected structural permission: %',r.relname;
            END IF;
            IF r.relname = ANY(audit)
               AND has_table_privilege('edge_analysis_v2_writer',r.oid,'UPDATE,DELETE') THEN
                RAISE EXCEPTION 'Audit evidence is mutable: %',r.relname;
            END IF;
            IF r.relname = ANY(results) AND (
                NOT has_table_privilege('edge_analysis_v2_writer',r.oid,'UPDATE') OR
                NOT has_table_privilege('edge_analysis_v2_writer',r.oid,'DELETE')) THEN
                RAISE EXCEPTION 'Missing result update permission: %',r.relname;
            END IF;
        ELSIF has_table_privilege('edge_analysis_v2_writer',r.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES') THEN
            RAISE EXCEPTION 'Unexpected source access: %',r.relname;
        END IF;
    END LOOP;
    -- 원천 읽기는 다섯 함수(SECURITY DEFINER)로만. 다른 DEFINER 함수를 실행할 수 있으면 테이블 권한 0 이 무의미하다.
    FOR r IN SELECT p.oid, p.proname, p.proacl FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.prosecdef LOOP
        IF r.proname IN ('macro_observations_as_of','financial_quarters_as_of',
                         'sector_classification_as_of','etf_constituent_source_coverage',
                         'source_observation_freshness') THEN
            IF NOT has_function_privilege('edge_analysis_v2_writer',r.oid,'EXECUTE') THEN
                RAISE EXCEPTION 'Missing source read function: %',r.proname;
            END IF;
            -- proacl NULL = 기본 권한(PUBLIC EXECUTE), grantee 0 = PUBLIC
            IF r.proacl IS NULL OR EXISTS (SELECT FROM aclexplode(r.proacl) a
                                           WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE') THEN
                RAISE EXCEPTION 'Source read function is open to PUBLIC: %',r.proname;
            END IF;
        ELSIF has_function_privilege('edge_analysis_v2_writer',r.oid,'EXECUTE') THEN
            RAISE EXCEPTION 'Unexpected definer function: %',r.proname;
        END IF;
    END LOOP;
    IF has_schema_privilege('edge_analysis_v2_writer','public','CREATE')
       OR has_database_privilege('edge_analysis_v2_writer',current_database(),'CREATE') THEN
        RAISE EXCEPTION 'Writer can create permanent objects';
    END IF;
END $$;

SET LOCAL ROLE edge_analysis_v2_writer;
INSERT INTO movement_analyses (analysis_id,etf_code,analysis_at,trading_date)
VALUES ('writer-permission-test','TEST',now(),current_date);
UPDATE movement_analyses SET summary='test' WHERE analysis_id='writer-permission-test';
INSERT INTO tool_definitions (tool_id,function_name,version,description)
VALUES ('writer-permission-test','writer-permission-test','v1','Test only');
INSERT INTO tool_runs (tool_run_id,tool_id,movement_analysis_id,arguments,output,status,finished_at)
VALUES ('writer-permission-test','writer-permission-test','writer-permission-test',
        '{}','{"tool_run_id":"writer-permission-test","result":{"amount_krw":18}}','completed',now());
DO $$
BEGIN
    IF (SELECT output->'result'->>'amount_krw' FROM tool_runs
        WHERE tool_run_id='writer-permission-test') <> '18' THEN
        RAISE EXCEPTION 'Stored evidence differs';
    END IF;
    BEGIN
        UPDATE tool_runs SET output='{}' WHERE tool_run_id='writer-permission-test';
        RAISE EXCEPTION 'Audit update was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        DELETE FROM tool_definitions WHERE tool_id='writer-permission-test';
        RAISE EXCEPTION 'Definition delete was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM 1 FROM instrument LIMIT 1;
        RAISE EXCEPTION 'Source read was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        DELETE FROM instrument WHERE false;
        RAISE EXCEPTION 'Source write was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    -- 원천 관측(ALPHA-1130): 시점 조회 함수는 되고, 그 뒤 테이블은 직접 못 읽는다.
    PERFORM * FROM macro_observations_as_of(now(),'usd_krw',1);
    PERFORM * FROM financial_quarters_as_of(now(),'005930');
    PERFORM * FROM sector_classification_as_of(now(),ARRAY['005930']);
    PERFORM * FROM etf_constituent_source_coverage('091160',now());
    PERFORM * FROM source_observation_freshness();
    BEGIN
        PERFORM 1 FROM macro_observation LIMIT 1;
        RAISE EXCEPTION 'Macro table read was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM 1 FROM financial_metric LIMIT 1;
        RAISE EXCEPTION 'Financial table read was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM 1 FROM sector_classification LIMIT 1;
        RAISE EXCEPTION 'Sector table read was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        PERFORM 1 FROM financial_report_version LIMIT 1;
        RAISE EXCEPTION 'Report version table read was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
    BEGIN
        INSERT INTO macro_observation SELECT * FROM macro_observation WHERE false;
        RAISE EXCEPTION 'Macro table write was permitted';
    EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
ROLLBACK;
