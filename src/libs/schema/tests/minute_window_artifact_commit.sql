-- 실제 PostgreSQL 제약 검증. 전용 ephemeral DB에서만 실행하고 fixture는 rollback한다.
\set ON_ERROR_STOP on
BEGIN;

INSERT INTO minute_ingestion_session (
    session_id, dataset, source_group, session_date, universe_version,
    universe_hash, expected_window_count
) VALUES ('alpha1060-schema-test', 'price_minute', 'schema-test', '2026-09-07', 'v1', 'h1', 1);
INSERT INTO minute_ingestion_window (
    session_id, window_start, window_end, scheduled_at
) VALUES ('alpha1060-schema-test', '2026-09-07T00:00Z', '2026-09-07T00:01Z', '2026-09-07T00:01Z');
INSERT INTO minute_window_artifact_commit (
    session_id, window_start, generation, artifact_uri, artifact_checksum,
    manifest_uri, manifest_checksum
) VALUES ('alpha1060-schema-test', '2026-09-07T00:00Z', 1, 'canonical/a', repeat('a', 64),
          'operations_archive/m', repeat('b', 64));

DO $$
DECLARE
    column_name TEXT;
    bad_hash TEXT;
BEGIN
    -- 같은 generation의 서로 다른 후보를 두 승자로 인증하면 안 된다.
    BEGIN
        INSERT INTO minute_window_artifact_commit
        SELECT session_id, window_start, generation, 'canonical/loser', artifact_checksum,
               manifest_uri, manifest_checksum, committed_at
        FROM minute_window_artifact_commit WHERE session_id = 'alpha1060-schema-test';
        RAISE EXCEPTION 'duplicate winner was accepted';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;

    BEGIN
        INSERT INTO minute_window_artifact_commit
        SELECT session_id, window_start + interval '1 minute', generation, artifact_uri,
               artifact_checksum, manifest_uri, manifest_checksum, committed_at
        FROM minute_window_artifact_commit WHERE session_id = 'alpha1060-schema-test';
        RAISE EXCEPTION 'nonexistent window was accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL;
    END;

    BEGIN
        UPDATE minute_window_artifact_commit SET generation = 0
        WHERE session_id = 'alpha1060-schema-test';
        RAISE EXCEPTION 'uncommitted generation was accepted';
    EXCEPTION WHEN check_violation THEN NULL;
    END;

    FOREACH column_name IN ARRAY ARRAY['artifact_checksum', 'manifest_checksum'] LOOP
        FOREACH bad_hash IN ARRAY ARRAY['', repeat('a', 63), repeat('a', 65), repeat('A', 64), repeat('g', 64)] LOOP
            BEGIN
                EXECUTE format('UPDATE minute_window_artifact_commit SET %I = $1 WHERE session_id = $2', column_name)
                USING bad_hash, 'alpha1060-schema-test';
                RAISE EXCEPTION '% accepted invalid hash %', column_name, bad_hash;
            EXCEPTION WHEN check_violation THEN NULL;
            END;
        END LOOP;
    END LOOP;

    FOREACH column_name IN ARRAY ARRAY['artifact_uri', 'manifest_uri'] LOOP
        BEGIN
            EXECUTE format('UPDATE minute_window_artifact_commit SET %I = $1 WHERE session_id = $2', column_name)
            USING '   ', 'alpha1060-schema-test';
            RAISE EXCEPTION '% accepted empty URI', column_name;
        EXCEPTION WHEN check_violation THEN NULL;
        END;
    END LOOP;

    IF NOT EXISTS (
        SELECT 1 FROM minute_window_artifact_commit
        WHERE session_id = 'alpha1060-schema-test' AND generation = 1
          AND artifact_uri = 'canonical/a' AND artifact_checksum = repeat('a', 64)
          AND manifest_uri = 'operations_archive/m' AND manifest_checksum = repeat('b', 64)
          AND committed_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'constraint failures changed the committed winner';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM minute_ingestion_window
        WHERE session_id = 'alpha1060-schema-test' AND generation = 0 AND checksum IS NULL
    ) THEN
        RAISE EXCEPTION 'history insertion changed the current window';
    END IF;
    RAISE NOTICE 'ALPHA-1060: 15 constraint rejections and winner/window preservation passed';
END $$;

ROLLBACK;
