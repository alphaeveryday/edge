-- ============================================================================
-- tenant_sync_cursor 재정의 — TIMESTAMPTZ watermark → BIGINT sequence cursor
--
-- cursor 를 TIMESTAMPTZ 에서 BIGINT 로 바꾸는 이유 (ADR-0015·0021):
--   timestamp watermark 는 동일 시각 충돌·시계 스큐·gap 감지 불가로 확정 배제된 방식이다.
--   cursor 는 테넌트별 단조 증가 sequence 값이며, 발번 시점은 테넌트별 fan-out(ADR-0021).
--   전달 레코드 저장 구조(outbox)의 물리 설계는 영서 오너십으로 별도 확정한다
--   (docs/contracts/event-bundle-schema.md).
--
-- 확장-수축 단계: 수축+확장 동시.
--   해당 테이블은 V202607150001 이 만든 직후이고 reader/writer 코드가 존재하지 않아
--   롤아웃 시차 중 깨질 코드가 없다 (V202607150001 의 구 마트 대체와 동일 근거).
-- ============================================================================

SET search_path TO public;

DROP TABLE tenant_sync_cursor;

CREATE TABLE tenant_sync_cursor (
    tenant_id           BIGINT PRIMARY KEY,
    last_cursor         BIGINT NOT NULL,
    last_synced_at      TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_tenant_sync_cursor_nonnegative
        CHECK (last_cursor >= 0)
);

COMMENT ON TABLE tenant_sync_cursor IS
'테넌트 pull 동기화의 마지막 수신 cursor(테넌트별 단조증가 sequence, 0 = 미수신)와 실제 동기화 시각. Tenant Sync API 가 단일 writer.';

-- ============================================================================
-- Foreign keys
-- ============================================================================

ALTER TABLE tenant_sync_cursor
    ADD CONSTRAINT fk_tenant_sync_cursor_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id);
