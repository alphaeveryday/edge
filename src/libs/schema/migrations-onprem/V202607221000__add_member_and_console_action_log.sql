-- ============================================================================
-- 콘솔 사용자 + 콘솔 조작 감사 로그 — member, console_action_log
--
-- 사용자/권한과 콘솔 조작 이력은 온프렘 거주 한정 데이터다
-- (docs/domain/data-residency.md — Cloud 저장 금지 데이터).
--
-- 인증은 하이브리드(ADR-0025): 운영 = 증권사 SSO/AD, 데모 = 자체 계정 최소 구현.
-- 셀프 가입·초대 링크·재설정 메일 흐름은 없다(온프렘 SMTP 통제) — 관리자 직접
-- 등록만 있으므로 INVITED 같은 중간 상태를 두지 않는다.
-- ============================================================================

SET search_path TO public;

-- 콘솔 사용자 원장. email 소문자 정규화는 앱 계층 책임.
CREATE TABLE member (
    member_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email          TEXT NOT NULL,
    name           TEXT NOT NULL,
    -- 역할 4종(ADR-0025). 권한 매트릭스는 후속 결정 — 어휘만 스키마가 고정한다.
    role           VARCHAR(30) NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    -- 데모 자체 계정 경로 전용(ADR-0025) — 운영 SSO/AD 사용자는 NULL.
    password_hash  TEXT,
    -- 운영 SSO/AD 의 subject 식별자 — 데모 자체 계정은 NULL.
    sso_subject    TEXT,
    last_login_at  TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_member_email UNIQUE (email),
    CONSTRAINT uq_member_sso_subject UNIQUE (sso_subject),
    CONSTRAINT ck_member_role
        CHECK (role IN ('TENANT_ADMIN', 'COMPLIANCE_REVIEWER', 'OPERATOR', 'READ_ONLY'))
);

COMMENT ON TABLE member IS
'콘솔 사용자(역할 4종, ADR-0025) — 관리자 직접 등록·비활성화만, 초대 흐름 없음. 온프렘 거주 한정(data-residency.md). writer = tenant-console-api.';

-- 콘솔 조작 감사 로그 (append-only — UPDATE/DELETE 하지 않는다).
-- 정책 변경·제공 중단·범위 토글 등 "누가 콘솔에서 무엇을 했나"의 원장.
-- action 어휘는 콘솔 기능과 함께 늘어나므로 CHECK 로 닫지 않는다(앱 계층 관리).
CREATE TABLE console_action_log (
    console_action_log_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id     BIGINT NOT NULL REFERENCES member (member_id),
    action       VARCHAR(50) NOT NULL,
    -- 대상 참조는 다형(도메인 ID TEXT·서로게이트 BIGINT 혼재)이라 TEXT 로 담는다.
    target_type  VARCHAR(30),
    target_id    TEXT,
    detail       JSONB,
    client_ip    VARCHAR(45),
    occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE console_action_log IS
'콘솔 조작 감사 로그(append-only) — 정책 변경 이력 등 온프렘 거주 요구(data-residency.md) 커버. writer = tenant-console-api.';

-- 기간 기준 감사 조회용.
CREATE INDEX ix_console_action_log_occurred ON console_action_log (occurred_at);
