-- ============================================================================
-- tenant_delivery — 테넌트별 전달 레코드(outbox), Sync 채널의 번들 조립 원장
--
-- event-bundle-schema.md "전달 레코드" 절의 물리 확정(ALPHA-396). 번들은 이
-- 테이블을 테넌트별 cursor 순으로 묶어 생성한다(tenant-sync-api 가 조회·조립).
--
-- 페이로드(explanation_result 본문 등)는 저장하지 않는다 — 번들 조립 시점에
-- 도메인 테이블을 조인해 싣는다(스냅샷 중복 저장 회피 — walking skeleton
-- 트레이드오프, 계약 문서에 명기). retention 정책은 후속(현재 무제한 보존).
-- ============================================================================

SET search_path TO public;

CREATE TABLE tenant_delivery (
    tenant_id                     BIGINT NOT NULL REFERENCES tenant (tenant_id),
    -- 테넌트별 단조증가 cursor — fan-out 발번 시점에 부여(ADR-0015·0021).
    cursor                        BIGINT NOT NULL,
    delivery_type                 VARCHAR(20) NOT NULL,
    -- NEW·CORRECTION: 전달할 결과. INVALIDATION 은 본문 없음.
    explanation_result_id         TEXT REFERENCES explanation_result (explanation_result_id),
    -- CORRECTION·INVALIDATION: 대상(기존 게시분) 참조.
    target_explanation_result_id  TEXT REFERENCES explanation_result (explanation_result_id),
    reason                        TEXT,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_tenant_delivery PRIMARY KEY (tenant_id, cursor),
    CONSTRAINT ck_tenant_delivery_type
        CHECK (delivery_type IN ('NEW', 'CORRECTION', 'INVALIDATION')),
    CONSTRAINT ck_tenant_delivery_cursor_positive CHECK (cursor > 0),
    -- 유형별 페이로드 형상(와이어 계약과 1:1): 정정·무효화는 사유 필수
    -- (state-machine.md — Super Admin 정정 시 사유 입력 필수).
    CONSTRAINT ck_tenant_delivery_payload CHECK (
        (delivery_type = 'NEW'
            AND explanation_result_id IS NOT NULL
            AND target_explanation_result_id IS NULL
            AND reason IS NULL)
        OR (delivery_type = 'CORRECTION'
            AND explanation_result_id IS NOT NULL
            AND target_explanation_result_id IS NOT NULL
            AND reason IS NOT NULL)
        OR (delivery_type = 'INVALIDATION'
            AND explanation_result_id IS NULL
            AND target_explanation_result_id IS NOT NULL
            AND reason IS NOT NULL)
    ),
    -- 정정분은 항상 새 ID(리비전 분리, state-machine.md) — 자기 자신을 대상으로 못 한다.
    CONSTRAINT ck_tenant_delivery_no_self_target
        CHECK (explanation_result_id IS NULL
            OR target_explanation_result_id IS NULL
            OR explanation_result_id <> target_explanation_result_id),
    -- 사유 필수 = 비어있지 않은 문구(빈 문자열·공백만은 사유가 아니다).
    CONSTRAINT ck_tenant_delivery_reason_not_blank
        CHECK (reason IS NULL OR btrim(reason) <> '')
);

COMMENT ON TABLE tenant_delivery IS
'테넌트별 전달 레코드(outbox) — 번들 조립 원장(event-bundle-schema.md). PK (tenant_id, cursor) = 전달 멱등 키. writer = fan-out 발번기(후속 — 도입 전 로컬 시드), reader = tenant-sync-api.';
