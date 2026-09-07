-- ALPHA-1060: 현재 window와 별개로 실제 확정된 artifact 세대를 보존한다.
-- writer는 data-pipeline 하나다. window/history/job/outbox를 같은 transaction에서
-- 기록하며, 같은 PK의 다른 URI/checksum은 덮지 않고 전체 commit을 거부한다.
-- 기존 window나 과거 S3 객체를 소급 인증/수정하지 않는 확장 migration이다.
SET LOCAL lock_timeout = '3s';
SET search_path TO public;

CREATE TABLE minute_window_artifact_commit (
    session_id        TEXT NOT NULL,
    window_start      TIMESTAMPTZ NOT NULL,
    generation        INTEGER NOT NULL,
    artifact_uri      TEXT NOT NULL,
    artifact_checksum TEXT NOT NULL,
    manifest_uri      TEXT NOT NULL,
    manifest_checksum TEXT NOT NULL,
    committed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (session_id, window_start, generation),
    CONSTRAINT fk_minute_artifact_commit_window
        FOREIGN KEY (session_id, window_start)
        REFERENCES minute_ingestion_window (session_id, window_start),
    CONSTRAINT ck_minute_artifact_commit_generation CHECK (generation >= 1),
    CONSTRAINT ck_minute_artifact_commit_artifact_hash
        CHECK (artifact_checksum ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_minute_artifact_commit_manifest_hash
        CHECK (manifest_checksum ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_minute_artifact_commit_uris
        CHECK (length(btrim(artifact_uri)) > 0 AND length(btrim(manifest_uri)) > 0)
);

COMMENT ON TABLE minute_window_artifact_commit IS
'분 레인의 실제 확정 이력. data-pipeline이 window/job/outbox와 원자적으로 INSERT하고 URI/checksum/최초 committed_at을 변경하지 않는다. 현재 승자와 과거 승자만 인증하며 같은 세대의 미확정 후보는 포함하지 않는다.';
COMMENT ON COLUMN minute_window_artifact_commit.artifact_uri IS
'Storage bucket-relative key. generation과 무관한 content key 또는 검증된 legacy key.';
COMMENT ON COLUMN minute_window_artifact_commit.manifest_uri IS
'확정 당시 manifest의 Storage bucket-relative key. 자기 URI는 manifest 해시 입력에 넣지 않는다.';
