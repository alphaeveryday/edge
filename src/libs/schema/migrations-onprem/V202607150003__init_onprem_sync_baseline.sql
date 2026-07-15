-- ============================================================================
-- 온프렘 sync baseline — Raw Event Store 최소형 + 에이전트 cursor 상태
--
-- 온프렘 세트(migrations-onprem/)의 첫 마이그레이션. cloud 세트(migrations/)와
-- 독립적으로 버전이 증가하며, 적용 대상은 테넌트 온프렘 PostgreSQL 이다
-- (아티팩트 2종 분리 — ADR-0016, docs/implementation.md §1).
--
-- 온프렘 DB 는 테넌트별 물리 격리(ADR-0011)라 tenant_id 를 두지 않는다.
-- 상태 분기 테이블(analysis_items 등, state-machine.md ERD)은 후속 마이그레이션.
-- ============================================================================

SET search_path TO public;

-- 수신 번들 원본 보존 (Raw Event Store 최소형).
-- 체크섬 검증을 통과한 응답 바이트를 그대로 담는다 — 수신 원본 불변 원칙
-- (docs/contracts/event-bundle-schema.md 체크섬 절).
CREATE TABLE received_bundle (
    cursor_from     BIGINT NOT NULL,
    cursor_to       BIGINT NOT NULL,
    checksum        CHAR(64) NOT NULL,
    body            BYTEA NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_received_bundle PRIMARY KEY (cursor_from),
    CONSTRAINT ck_received_bundle_range
        CHECK (cursor_to >= cursor_from),
    CONSTRAINT ck_received_bundle_checksum
        CHECK (checksum ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE received_bundle IS
'수신 번들 원본(응답 바이트 그대로, 불변) — Raw Event Store 최소형. writer = sync-agent.';

-- 에이전트 로컬 cursor — 계약(sync-protocol.md): Sync Agent 는 마지막 처리 cursor 를 로컬 저장.
CREATE TABLE sync_state (
    single_row      BOOLEAN PRIMARY KEY DEFAULT TRUE,
    last_cursor     BIGINT NOT NULL,
    last_synced_at  TIMESTAMPTZ,

    CONSTRAINT ck_sync_state_single_row CHECK (single_row),
    CONSTRAINT ck_sync_state_cursor_nonnegative CHECK (last_cursor >= 0)
);

COMMENT ON TABLE sync_state IS
'sync-agent 의 마지막 처리 cursor(0 = 미수신, last_synced_at NULL = 동기화 이력 없음). 단일 행 강제. writer = sync-agent.';

INSERT INTO sync_state (single_row, last_cursor, last_synced_at)
VALUES (TRUE, 0, NULL);
