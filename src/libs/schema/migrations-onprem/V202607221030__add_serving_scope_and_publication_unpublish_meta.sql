-- ============================================================================
-- 제공 범위 + 수동 제공 중단 메타 — serving_scope, publication 확장
--
-- 제공 범위(콘솔 "환경 설정 › 제공 범위"): 시장/종목 단위 노출 토글.
-- 이해상충 종목 통제는 별도 룰이 아니라 이 범위 제외로 표현한다
-- (tenant-console.md — Settings 노출 범위 제외).
--
-- publication 확장은 additive(확장-수축의 확장 단계) — 기존 컬럼 불변.
-- ============================================================================

SET search_path TO public;

CREATE TABLE serving_scope (
    serving_scope_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope_type        VARCHAR(20) NOT NULL,
    -- MARKET = market_code(MIC, 예: XKRX — ADR-0027), INSTRUMENT = etf_ticker(서빙 키).
    scope_key         VARCHAR(50) NOT NULL,
    enabled           BOOLEAN NOT NULL,
    updated_by        BIGINT REFERENCES member (member_id),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_serving_scope UNIQUE (scope_type, scope_key),
    CONSTRAINT ck_serving_scope_type
        CHECK (scope_type IN ('MARKET', 'INSTRUMENT'))
);

-- 행 부재 = 기본 제공(옵트아웃 모델) — 제외·재개 이력이 있는 키만 행을 가진다.
-- 시장 비활성이 소속 종목에 우선하는 판정(시장 OFF 면 종목 토글 무시)은 앱 계층
-- (publication-api·tenant-console-api)이 공유하는 규칙이다.
COMMENT ON TABLE serving_scope IS
'제공 범위 토글(시장/종목) — 행 부재 = 기본 제공. 이해상충 종목 제외 통제 지점(tenant-console.md). writer = tenant-console-api.';

-- 수동 제공 중단(콘솔 stop 액션)의 감사 메타 — 자동 전이(정정 UNPUBLISHED·
-- 무효화 INVALIDATED)는 둘 다 NULL 로 남아 사람 개입 여부가 구분된다.
ALTER TABLE publication
    ADD COLUMN unpublish_reason TEXT,
    ADD COLUMN unpublished_by   BIGINT REFERENCES member (member_id);

COMMENT ON COLUMN publication.unpublish_reason IS
'수동 제공 중단 사유 — 자동 전이(정정·무효화)는 NULL.';
COMMENT ON COLUMN publication.unpublished_by IS
'수동 제공 중단 실행자(member) — 자동 전이는 NULL.';
