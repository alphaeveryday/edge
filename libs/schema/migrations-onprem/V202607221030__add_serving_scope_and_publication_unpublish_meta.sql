-- ============================================================================
-- 제공 범위 + publication 게시 스냅샷·수동 중단 메타 — serving_scope, publication 확장
--
-- 제공 범위(콘솔 "환경 설정 › 제공 범위", tenant-console.md Settings): 시장·종목
-- ON/OFF, 특정 ETF·섹터 제외, MTS/HTS/Internal 채널별 ON/OFF. 이해상충 종목
-- 통제는 별도 룰이 아니라 이 범위 제외로 표현한다(ADR-0023).
--
-- publication 확장은 additive(확장-수축의 확장 단계) — 기존 컬럼 불변.
-- ============================================================================

SET search_path TO public;

CREATE TABLE serving_scope (
    serving_scope_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope_type        VARCHAR(20) NOT NULL,
    -- 타입별 scope_key 의미: MARKET = market_code(MIC, 예: XKRX — ADR-0027),
    -- SECTOR = 섹터 분류 키(분류 체계는 ETF 마스터 후속과 함께 확정),
    -- INSTRUMENT = etf_ticker(서빙 키), CHANNEL = 노출 채널(exposure_log 와 동일 어휘).
    scope_key         VARCHAR(50) NOT NULL,
    enabled           BOOLEAN NOT NULL,
    updated_by        BIGINT REFERENCES member (member_id),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT uq_serving_scope UNIQUE (scope_type, scope_key),
    CONSTRAINT ck_serving_scope_type
        CHECK (scope_type IN ('MARKET', 'SECTOR', 'INSTRUMENT', 'CHANNEL')),
    CONSTRAINT ck_serving_scope_channel_key
        CHECK (scope_type <> 'CHANNEL' OR scope_key IN ('MTS', 'HTS', 'INTERNAL'))
);

-- 행 부재 = 기본 제공(옵트아웃 모델) — 제외·재개 이력이 있는 키만 행을 가진다.
-- 상위 범위 비활성이 하위에 우선하는 판정(시장 OFF 면 종목 토글 무시)은 앱 계층
-- (publication-api·tenant-console-api)이 공유하는 규칙이다.
-- 판정 가능 시점: INSTRUMENT(ticker)·CHANNEL(X-Channel 헤더)은 현행 서빙 데이터로
-- 즉시 판정 가능. MARKET·SECTOR 는 온프렘에 시장·섹터 식별이 공급된 뒤 동작한다
-- (경계면 확장·ETF 마스터 — 보류 항목. 현행 서빙 식별자는 ticker 뿐).
COMMENT ON TABLE serving_scope IS
'제공 범위 토글(시장/섹터/종목/채널, tenant-console.md Settings) — 행 부재 = 기본 제공. 이해상충 종목·섹터 제외 통제 지점(ADR-0023). writer = tenant-console-api.';

-- 게시 스냅샷 + 수동 제공 중단(콘솔 stop 액션) 감사 메타.
ALTER TABLE publication
    ADD COLUMN published_summary TEXT,
    ADD COLUMN unpublish_reason  TEXT,
    ADD COLUMN unpublished_by    BIGINT REFERENCES member (member_id),
    -- 수동 중단 메타는 짝으로만 존재한다 — 사유만·실행자만 남는 부분 기록 차단.
    -- 빈 문자열 사유(실질 사유 없는 감사 기록)도 결측과 같게 취급해 차단한다.
    ADD CONSTRAINT ck_publication_unpublish_meta_pair
        CHECK ((unpublish_reason IS NULL) = (unpublished_by IS NULL)),
    ADD CONSTRAINT ck_publication_unpublish_reason_nonblank
        CHECK (unpublish_reason IS NULL OR btrim(unpublish_reason, E' \t\n\r') <> ''),
    -- 수동 중단 메타는 UNPUBLISHED 행에만 — 자동 전이(정정 UNPUBLISHED·무효화
    -- INVALIDATED)와 PUBLISHED 행은 둘 다 NULL 이라 사람 개입 여부가 구분된다.
    ADD CONSTRAINT ck_publication_unpublish_meta_status
        CHECK (unpublish_reason IS NULL OR status = 'UNPUBLISHED'),
    -- 중단 시각 없는 UNPUBLISHED 를 차단 — 기존 writer(screening-worker)는
    -- 전이와 함께 unpublished_at = now() 를 이미 기록한다.
    ADD CONSTRAINT ck_publication_unpublished_at_presence
        CHECK (status <> 'UNPUBLISHED' OR unpublished_at IS NOT NULL);

COMMENT ON COLUMN publication.published_summary IS
'게시 시점 노출 문구 스냅샷 — 검수 편집(수정 승인, review_task.edited_summary)의 게시 경로이자, 번들 재수신 upsert 가 analysis_item.summary 를 되돌려도 노출 문구가 흔들리지 않는 고정점. NULL = 자동 게시 경로가 analysis_item.summary 를 그대로 노출(걷는 뼈대 현행) — 서빙 전환·검수 액션(ALPHA-437)과 함께 필수화한다.';
COMMENT ON COLUMN publication.unpublish_reason IS
'수동 제공 중단 사유 — 자동 전이(정정·무효화)는 NULL.';
COMMENT ON COLUMN publication.unpublished_by IS
'수동 제공 중단 실행자(member) — 자동 전이는 NULL.';
