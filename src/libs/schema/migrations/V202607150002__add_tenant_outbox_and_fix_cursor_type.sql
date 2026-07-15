-- ============================================================================
-- Sync 채널 물리 계약 — tenant_outbox 추가 + cursor 타입 정정
--
-- V202607150001 이 비워 둔 outbox(영서 오너십, event-bundle-schema.md)를 채우고,
-- tenant_sync_cursor 의 cursor 의미를 확정 결정에 맞게 정정한다.
--
-- 확장-수축 단계: 수축+확장 동시 (tenant_sync_cursor 재정의).
--   해당 테이블은 V202607150001 이 만든 직후이고 reader/writer 코드가 존재하지 않아
--   롤아웃 시차 중 깨질 코드가 없다 (V202607150001 의 구 마트 대체와 동일 근거).
--
-- cursor 를 TIMESTAMPTZ 에서 BIGINT 로 바꾸는 이유 (ADR-0015·0021):
--   timestamp watermark 는 동일 시각 충돌·시계 스큐·gap 감지 불가로 확정 배제된 방식이다.
--   cursor 는 테넌트별 단조 증가 sequence 값이며, 발번은 fan-out 트랜잭션 안에서
--   "해당 테넌트 last cursor + 1" 로 한다. DB sequence(nextval) 금지 — sequence 는
--   트랜잭션 밖에서 발번되어 커밋 순서 ≠ cursor 순서가 될 수 있고, 그 gap 은 순차
--   소비자가 이벤트를 영구히 건너뛰게 만든다 (event-bundle-schema.md fan-out 직렬화 규칙).
-- ============================================================================

SET search_path TO public;

-- ============================================================================
-- 1. tenant_sync_cursor 재정의 — TIMESTAMPTZ watermark → BIGINT sequence cursor
-- ============================================================================

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
-- 2. tenant_outbox — fan-out 산출물, 번들 생성의 유일한 소스
-- ============================================================================

CREATE TABLE tenant_outbox (
    tenant_id                       BIGINT NOT NULL,
    cursor                          BIGINT NOT NULL,
    delivery_type                   VARCHAR(20) NOT NULL,
    explanation_result_id           TEXT NOT NULL,
    target_explanation_result_id    TEXT,
    reason                          TEXT,
    enqueued_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_tenant_outbox PRIMARY KEY (tenant_id, cursor),
    CONSTRAINT ck_tenant_outbox_cursor_positive
        CHECK (cursor >= 1),
    CONSTRAINT ck_tenant_outbox_delivery_type
        CHECK (delivery_type IN ('NEW', 'CORRECTION', 'INVALIDATION')),
    -- 정정·무효화는 사유 필수 (Super Admin 사유 입력 필수 정책)
    CONSTRAINT ck_tenant_outbox_reason
        CHECK (delivery_type = 'NEW' OR reason IS NOT NULL),
    -- 대체 대상 참조는 CORRECTION 에만 존재한다
    CONSTRAINT ck_tenant_outbox_target
        CHECK ((delivery_type = 'CORRECTION') = (target_explanation_result_id IS NOT NULL))
);

COMMENT ON TABLE tenant_outbox IS
'테넌트별 전달 레코드(fan-out 산출물) — Event Bundle 생성의 유일한 소스. cursor 발번은 fan-out 워커가 테넌트별 직렬 처리로 같은 트랜잭션에서 last cursor + 1 (nextval 금지 — event-bundle-schema.md). writer = fan-out 워커(진기), reader = Tenant Sync API(영서).';

COMMENT ON COLUMN tenant_outbox.explanation_result_id IS
'전달 본체. NEW·CORRECTION = 게시본/재게시본, INVALIDATION = 철회 대상.';

COMMENT ON COLUMN tenant_outbox.target_explanation_result_id IS
'CORRECTION 에서만 — 재게시로 대체된 구 게시본. On-Prem 이 UNPUBLISHED 처리할 대상.';

-- ============================================================================
-- Foreign keys
-- ============================================================================

ALTER TABLE tenant_sync_cursor
    ADD CONSTRAINT fk_tenant_sync_cursor_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id);

ALTER TABLE tenant_outbox
    ADD CONSTRAINT fk_tenant_outbox_tenant
        FOREIGN KEY (tenant_id) REFERENCES tenant (tenant_id);

ALTER TABLE tenant_outbox
    ADD CONSTRAINT fk_tenant_outbox_explanation_result
        FOREIGN KEY (explanation_result_id) REFERENCES explanation_result (explanation_result_id);

ALTER TABLE tenant_outbox
    ADD CONSTRAINT fk_tenant_outbox_target_explanation_result
        FOREIGN KEY (target_explanation_result_id) REFERENCES explanation_result (explanation_result_id);
