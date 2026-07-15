-- ============================================================================
-- Cloud Event Store — ETF 가격 변동 설명 물리 스키마 (하이브리드 피벗 이후)
--
-- 구 분석 마트(Cloud-only·embed widget 구조, V202606300001)를 대체한다.
-- 구 구조는 ADR-0010 하이브리드 On-Prem 피벗으로 폐기된 방향이다.
--
-- 확장-수축 단계: 수축+확장 동시 (구 마트 제거 + 신규 생성).
--   구 분석 마트에는 reader/writer 코드가 존재한 적이 없어(JPA 엔티티 0건, SQL 참조 0건)
--   롤아웃 시차 중 구버전 코드가 깨질 여지가 없다. 그래서 단계를 쪼개지 않는다.
--
-- 범위 밖 — SaaS 9개(organizations·members·invitations·roles·super_admins·
--   applications·widgets·compliance_settings·target_symbols)는 건드리지 않는다(조영서 오너십).
--   단 instruments 제거에 필요한 FK 제약 1건만 뗀다(아래 1번).
--
-- 타깃 스키마: public (libs/schema 규약. 원본 SQL의 `CREATE SCHEMA edge`는 적용하지 않음)
--
-- Naming conventions
--   * table names: singular snake_case
--   * primary/foreign keys: <entity>_id
--   * timestamps: *_at or *_as_of (TIMESTAMPTZ)
--   * business dates: *_date (DATE)
--   * rates/returns/weights/scores: DOUBLE PRECISION
--       - *_return, *_ratio, *_weight_ratio use decimal ratios (0.01 = 1%)
--       - *_pct uses percentage points (15.4 = 15.4%)
--   * monetary amounts and market prices: NUMERIC
--   * codes/status/version values: VARCHAR with CHECK constraints where stable
--
-- Version-management policy (MVP)
--   * release_bundle is the customer-facing product version.
--   * component versions are stored in release_bundle.component_versions JSONB.
--   * explanation_run references the actual release bundle used.
--   * independently regenerated outputs retain their own parser/model/policy version.
-- ============================================================================

SET search_path TO public;

-- ============================================================================
-- 0. 구 분석 마트 제거
-- ============================================================================

-- 1) 구 마트를 참조하는 유일한 외부 FK를 먼저 뗀다.
--    target_symbols 는 SaaS 쪽이라 남지만, 그 소속 서비스(widget-api)는 피벗으로 제거됐다
--    (context.md 서비스/API 변경표). 그래서 새 instrument 로 재지정하지 않고 제약만 제거한다.
--    target_symbols.instrument_id 컬럼과 데이터는 그대로 둔다 — 정리는 조영서 오너십.
--    DROP ... CASCADE 를 쓰지 않는 이유: CASCADE 는 이 제약 제거를 마이그레이션 본문에서
--    보이지 않게 숨긴다.
ALTER TABLE target_symbols DROP CONSTRAINT target_symbols_instrument_id_fkey;

-- 2) 구 분석 마트 15개 제거. 15개는 서로만 참조하는 닫힌 덩어리라 CASCADE 가 필요 없다.
--    set_updated_at() 함수는 남긴다 — SaaS 9개가 여전히 트리거로 쓴다.
DROP TABLE
    claim_news_links,
    claim_steps,
    pulse_factors,
    pulse_claims,
    daily_pulses,
    thesis_dialectic_news_links,
    thesis_dialectic_steps,
    thesis_scenarios,
    thesis_decision_points,
    investment_theses,
    issue_map_edges,
    issue_map_events,
    analysis_reports,
    news,
    instruments;

-- ============================================================================
-- 1. Shared entity, actor, instrument, and concept masters
-- ============================================================================

CREATE TABLE entity (
    entity_id           TEXT PRIMARY KEY,
    entity_type         VARCHAR(20) NOT NULL,
    display_name        TEXT NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',

    CONSTRAINT ck_entity_type
        CHECK (entity_type IN ('ACTOR', 'INSTRUMENT', 'CONCEPT')),
    CONSTRAINT ck_entity_status
        CHECK (status IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT uq_entity_id_type
        UNIQUE (entity_id, entity_type)
);

COMMENT ON TABLE entity IS
'문서·주장·이벤트에서 공통으로 참조하는 의미 객체의 상위 테이블. ACTOR, INSTRUMENT, CONCEPT 중 하나다.';

CREATE INDEX ix_entity_type ON entity (entity_type);
CREATE INDEX ix_entity_display_name ON entity (display_name);

CREATE TABLE actor (
    actor_id            TEXT PRIMARY KEY,
    entity_type         VARCHAR(20)
        GENERATED ALWAYS AS ('ACTOR'::VARCHAR(20)) STORED,
    actor_type          VARCHAR(30) NOT NULL,
    country_code        VARCHAR(2),

    CONSTRAINT ck_actor_type
        CHECK (actor_type IN ('COMPANY', 'GOVERNMENT', 'INSTITUTION', 'PERSON', 'OTHER')),
    CONSTRAINT ck_actor_country_code
        CHECK (country_code IS NULL OR country_code ~ '^[A-Z]{2}$'),
    CONSTRAINT uq_actor_id_type
        UNIQUE (actor_id, actor_type)
);

COMMENT ON TABLE actor IS
'회사·기관·정부·인물처럼 행동하거나 의사결정하는 엔터티의 상세 테이블.';

CREATE INDEX ix_actor_type ON actor (actor_type);

CREATE TABLE company_profile (
    actor_id            TEXT PRIMARY KEY,
    actor_type          VARCHAR(30)
        GENERATED ALWAYS AS ('COMPANY'::VARCHAR(30)) STORED,
    dart_corp_code      VARCHAR(8),
    profile_as_of_date  DATE,

    CONSTRAINT uq_company_profile_dart_corp_code
        UNIQUE (dart_corp_code),
    CONSTRAINT ck_company_profile_dart_corp_code
        CHECK (dart_corp_code IS NULL OR dart_corp_code ~ '^[0-9]{8}$')
);

COMMENT ON TABLE company_profile IS
'COMPANY 유형 actor에만 선택적으로 존재하는 회사 식별 프로필. 모든 actor가 이 행을 갖는 것은 아니다.';

CREATE TABLE instrument (
    instrument_id       TEXT PRIMARY KEY,
    entity_type         VARCHAR(20)
        GENERATED ALWAYS AS ('INSTRUMENT'::VARCHAR(20)) STORED,
    market_code         VARCHAR(30) NOT NULL,
    ticker              VARCHAR(30) NOT NULL,
    instrument_type     VARCHAR(20) NOT NULL,
    currency_code       VARCHAR(3),

    CONSTRAINT ck_instrument_type
        CHECK (instrument_type IN ('ETF', 'EQUITY')),
    CONSTRAINT ck_instrument_currency_code
        CHECK (currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'),
    CONSTRAINT uq_instrument_market_ticker
        UNIQUE (market_code, ticker),
    CONSTRAINT uq_instrument_id_type
        UNIQUE (instrument_id, instrument_type)
);

COMMENT ON TABLE instrument IS
'거래 가능한 ETF와 개별주식의 공통 식별 정보. 회사 actor와 회사가 발행한 주식 instrument는 서로 다른 엔터티다.';

CREATE INDEX ix_instrument_type ON instrument (instrument_type);

CREATE TABLE concept (
    concept_id          TEXT PRIMARY KEY,
    entity_type         VARCHAR(20)
        GENERATED ALWAYS AS ('CONCEPT'::VARCHAR(20)) STORED,
    concept_type        VARCHAR(30) NOT NULL,
    parent_concept_id   TEXT,

    CONSTRAINT ck_concept_not_self_parent
        CHECK (parent_concept_id IS NULL OR parent_concept_id <> concept_id)
);

COMMENT ON TABLE concept IS
'제품·산업·사업부문·테마·매크로 등 분석 개념 엔터티의 상세 테이블.';

CREATE INDEX ix_concept_type ON concept (concept_type);

CREATE TABLE market_series (
    market_series_id    TEXT PRIMARY KEY,
    series_type         VARCHAR(30) NOT NULL,
    series_name         TEXT NOT NULL,
    source_code         VARCHAR(50) NOT NULL,
    source_series_id    TEXT NOT NULL,
    market_code         VARCHAR(30),
    currency_code       VARCHAR(3),
    status              VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',

    CONSTRAINT ck_market_series_type
        CHECK (series_type IN ('INDEX', 'ETF_PROXY', 'FX', 'RATE')),
    CONSTRAINT ck_market_series_currency_code
        CHECK (currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'),
    CONSTRAINT ck_market_series_status
        CHECK (status IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT uq_market_series_source
        UNIQUE (source_code, source_series_id)
);

COMMENT ON TABLE market_series IS
'지수·ETF 대용치·환율·금리 등 비거래 지표까지 포괄하는 시장 시계열 마스터.';

CREATE INDEX ix_market_series_type_market ON market_series (series_type, market_code);

CREATE TABLE etf_profile (
    instrument_id               TEXT PRIMARY KEY,
    instrument_type             VARCHAR(20)
        GENERATED ALWAYS AS ('ETF'::VARCHAR(20)) STORED,
    etf_type                    VARCHAR(40) NOT NULL,
    tracking_market_series_id   TEXT,
    primary_theme_concept_id    TEXT,
    asset_manager_name          TEXT,
    leverage_multiplier         DOUBLE PRECISION,
    currency_hedged             BOOLEAN,
    decomposition_template      VARCHAR(50),
    total_expense_ratio         DOUBLE PRECISION,
    profile_as_of_date          DATE,

    CONSTRAINT ck_etf_profile_total_expense_ratio
        CHECK (total_expense_ratio IS NULL OR total_expense_ratio BETWEEN 0 AND 1),
    CONSTRAINT ck_etf_profile_leverage_multiplier
        CHECK (leverage_multiplier IS NULL OR leverage_multiplier <> 0)
);

COMMENT ON TABLE etf_profile IS
'ETF 하나의 상품 특성과 ETF Identity 분석에 필요한 분해 기준을 저장한다.';

CREATE TABLE equity_profile (
    instrument_id       TEXT PRIMARY KEY,
    instrument_type     VARCHAR(20)
        GENERATED ALWAYS AS ('EQUITY'::VARCHAR(20)) STORED,
    issuer_actor_id     TEXT NOT NULL,
    share_class_code    VARCHAR(20) NOT NULL,
    profile_as_of_date  DATE,

    CONSTRAINT ck_equity_profile_share_class
        CHECK (share_class_code IN ('COMMON', 'PREFERRED', 'OTHER'))
);

COMMENT ON TABLE equity_profile IS
'개별주식의 발행회사와 주식 종류를 저장한다. 보통주와 우선주는 서로 다른 instrument다.';

CREATE INDEX ix_equity_profile_issuer ON equity_profile (issuer_actor_id);

-- ============================================================================
-- 2. ETF inputs, movement trigger, contribution analysis, and routing
-- ============================================================================

CREATE TABLE etf_holding_snapshot (
    etf_instrument_id           TEXT NOT NULL,
    constituent_instrument_id   TEXT NOT NULL,
    trade_date                  DATE NOT NULL,
    weight_ratio                DOUBLE PRECISION,
    available_at                TIMESTAMPTZ NOT NULL,
    data_version                VARCHAR(50) NOT NULL,

    PRIMARY KEY (etf_instrument_id, constituent_instrument_id, trade_date),

    CONSTRAINT ck_etf_holding_weight_ratio
        CHECK (weight_ratio IS NULL OR weight_ratio BETWEEN 0 AND 1),
    CONSTRAINT ck_etf_holding_not_self
        CHECK (etf_instrument_id <> constituent_instrument_id)
);

COMMENT ON TABLE etf_holding_snapshot IS
'ETF·거래일·구성 금융상품 grain의 보유 비중 입력. 결측을 0으로 대체하지 않는다.';

CREATE INDEX ix_etf_holding_snapshot_etf_date
    ON etf_holding_snapshot (etf_instrument_id, trade_date);
CREATE INDEX ix_etf_holding_snapshot_constituent_date
    ON etf_holding_snapshot (constituent_instrument_id, trade_date);

CREATE TABLE etf_nav_daily (
    etf_instrument_id   TEXT NOT NULL,
    trade_date          DATE NOT NULL,
    nav                 NUMERIC(24, 8) NOT NULL,
    available_at        TIMESTAMPTZ NOT NULL,
    data_version        VARCHAR(50) NOT NULL,

    PRIMARY KEY (etf_instrument_id, trade_date),

    CONSTRAINT ck_etf_nav_daily_nav
        CHECK (nav > 0)
);

COMMENT ON TABLE etf_nav_daily IS
'ETF·거래일 grain의 공식 NAV 관측값. 거래통화는 instrument.currency_code에서 조회한다.';

CREATE TABLE price_movement_trigger (
    price_movement_trigger_id   TEXT PRIMARY KEY,
    etf_instrument_id           TEXT NOT NULL,
    trade_date                  DATE NOT NULL,
    detected_at                 TIMESTAMPTZ NOT NULL,
    observed_return             DOUBLE PRECISION NOT NULL,
    market_relative_return      DOUBLE PRECISION,
    absolute_gate_triggered     BOOLEAN NOT NULL,
    relative_gate_triggered     BOOLEAN NOT NULL,
    detection_policy_version    VARCHAR(50) NOT NULL,
    detection_reason            TEXT,

    CONSTRAINT uq_price_movement_trigger
        UNIQUE (etf_instrument_id, trade_date, detected_at),
    CONSTRAINT ck_price_movement_trigger_gate
        CHECK (absolute_gate_triggered OR relative_gate_triggered),
    CONSTRAINT ck_price_movement_trigger_return
        CHECK (observed_return >= -1)
);

COMMENT ON TABLE price_movement_trigger IS
'ETF 가격 변동이 진입 게이트를 통과하여 심층 분석을 시작한 사건. 이 행이 없는 날에는 후속 분석 행이 없을 수 있다.';

CREATE INDEX ix_price_movement_trigger_etf_date
    ON price_movement_trigger (etf_instrument_id, trade_date DESC);

CREATE TABLE etf_contribution_observation (
    contribution_observation_id         TEXT PRIMARY KEY,
    price_movement_trigger_id           TEXT NOT NULL UNIQUE,
    etf_return                          DOUBLE PRECISION,
    nav_return                          DOUBLE PRECISION,
    constituent_contribution_return     DOUBLE PRECISION,
    fx_contribution_return              DOUBLE PRECISION,
    premium_discount_contribution_return DOUBLE PRECISION,
    reconciliation_error                DOUBLE PRECISION,
    advancing_constituent_count         INTEGER,
    total_constituent_count             INTEGER,
    top3_contribution_ratio             DOUBLE PRECISION,
    available_at                        TIMESTAMPTZ NOT NULL,
    data_version                        VARCHAR(50) NOT NULL,

    CONSTRAINT ck_etf_contribution_counts
        CHECK (
            (advancing_constituent_count IS NULL AND total_constituent_count IS NULL)
            OR (
                advancing_constituent_count >= 0
                AND total_constituent_count >= 0
                AND advancing_constituent_count <= total_constituent_count
            )
        ),
    CONSTRAINT ck_etf_contribution_top3_ratio
        CHECK (top3_contribution_ratio IS NULL OR top3_contribution_ratio BETWEEN 0 AND 1)
);

COMMENT ON TABLE etf_contribution_observation IS
'가격변동트리거 하나에 대한 ETF 수준 항등식 분해 결과. NAV·구성종목·환율·괴리 기여와 라우팅 통계를 제공한다.';

CREATE TABLE etf_contribution_member (
    contribution_observation_id  TEXT NOT NULL,
    constituent_instrument_id    TEXT NOT NULL,
    weight_ratio                 DOUBLE PRECISION,
    constituent_return           DOUBLE PRECISION,
    contribution_return          DOUBLE PRECISION,
    contribution_rank            INTEGER,

    PRIMARY KEY (contribution_observation_id, constituent_instrument_id),

    CONSTRAINT ck_etf_contribution_member_weight
        CHECK (weight_ratio IS NULL OR weight_ratio BETWEEN 0 AND 1),
    CONSTRAINT ck_etf_contribution_member_rank
        CHECK (contribution_rank IS NULL OR contribution_rank > 0)
);

COMMENT ON TABLE etf_contribution_member IS
'ETF 기여 관찰에서 구성 금융상품 하나가 차지한 비중·수익률·기여와 순위.';

CREATE INDEX ix_etf_contribution_member_constituent
    ON etf_contribution_member (constituent_instrument_id);

CREATE TABLE explanation_route (
    explanation_route_id        TEXT PRIMARY KEY,
    contribution_observation_id TEXT NOT NULL UNIQUE,
    route_code                  VARCHAR(30) NOT NULL,
    event_search_required       BOOLEAN NOT NULL,
    decision_reason             TEXT,
    evaluated_at                TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_explanation_route_code
        CHECK (route_code IN (
            'PRICE_ONLY',
            'FLOW_DOMINATED',
            'CONCENTRATED',
            'COMMON_FACTOR',
            'FALLBACK_2LEG'
        )),
    CONSTRAINT ck_explanation_route_event_search
        CHECK (
            (route_code IN ('CONCENTRATED', 'COMMON_FACTOR') AND event_search_required)
            OR
            (route_code IN ('PRICE_ONLY', 'FLOW_DOMINATED', 'FALLBACK_2LEG') AND NOT event_search_required)
        )
);

COMMENT ON TABLE explanation_route IS
'ETF 기여 관찰을 바탕으로 가격 중심 종료 또는 이벤트 탐색 경로를 결정한 Analysis Engine 산출.';

CREATE TABLE explanation_route_decomposition (
    explanation_route_id        TEXT NOT NULL,
    price_decomposition_id      TEXT NOT NULL,
    usage_type                  VARCHAR(30),
    priority                    INTEGER,

    PRIMARY KEY (explanation_route_id, price_decomposition_id),

    CONSTRAINT ck_explanation_route_decomposition_priority
        CHECK (priority IS NULL OR priority > 0)
);

COMMENT ON TABLE explanation_route_decomposition IS
'설명 경로가 경로 결정·대상 선정·보조 검증에 실제 사용한 가격분해 결과의 N:M lineage.';

CREATE TABLE explanation_route_target (
    explanation_route_id        TEXT NOT NULL,
    target_entity_id            TEXT NOT NULL,
    source_instrument_id        TEXT,
    target_scope                VARCHAR(20) NOT NULL,
    selection_reason            VARCHAR(50),
    priority                    INTEGER,

    PRIMARY KEY (explanation_route_id, target_entity_id, target_scope),

    CONSTRAINT ck_explanation_route_target_scope
        CHECK (target_scope IN ('MARKET', 'THEME', 'CONSTITUENT')),
    CONSTRAINT ck_explanation_route_target_priority
        CHECK (priority IS NULL OR priority > 0)
);

COMMENT ON TABLE explanation_route_target IS
'설명 경로가 후속 이벤트 탐색 대상으로 선택한 시장·테마·회사·금융상품 엔터티.';

-- ============================================================================
-- 3. Documents and document assertions
-- ============================================================================

CREATE TABLE document (
    document_id         TEXT PRIMARY KEY,
    document_type       VARCHAR(20) NOT NULL,
    source_code         VARCHAR(50) NOT NULL,
    source_document_id  TEXT NOT NULL,
    title               TEXT,
    language_code       VARCHAR(10),
    published_at        TIMESTAMPTZ,
    available_at        TIMESTAMPTZ NOT NULL,
    source_uri          TEXT,

    CONSTRAINT ck_document_type
        CHECK (document_type IN ('NEWS', 'DISCLOSURE')),
    CONSTRAINT uq_document_source
        UNIQUE (source_code, source_document_id),
    CONSTRAINT uq_document_id_type
        UNIQUE (document_id, document_type)
);

COMMENT ON TABLE document IS
'외부 원천에서 수집한 뉴스 또는 공시 문서의 공통 식별·시간·원문 위치.';

CREATE INDEX ix_document_available_at ON document (available_at);
CREATE INDEX ix_document_type_available_at ON document (document_type, available_at);

CREATE TABLE news_document (
    document_id                 TEXT PRIMARY KEY,
    document_type               VARCHAR(20)
        GENERATED ALWAYS AS ('NEWS'::VARCHAR(20)) STORED,
    lead_text                   TEXT,
    representative_document_id TEXT,
    dedup_cluster_id            TEXT,
    theme_concept_id            TEXT
);

COMMENT ON TABLE news_document IS
'뉴스 문서의 리드, near-duplicate 클러스터, 대표 문서 및 대표 테마.';

CREATE INDEX ix_news_document_dedup_cluster ON news_document (dedup_cluster_id);

CREATE TABLE disclosure_document (
    document_id         TEXT PRIMARY KEY,
    document_type       VARCHAR(20)
        GENERATED ALWAYS AS ('DISCLOSURE'::VARCHAR(20)) STORED,
    issuer_actor_id     TEXT NOT NULL,
    disclosure_type     VARCHAR(50) NOT NULL,
    report_date         DATE,
    parser_version      VARCHAR(50) NOT NULL,
    parsed_result_uri   TEXT
);

COMMENT ON TABLE disclosure_document IS
'공시 문서의 공식 제출회사, 공시 유형, 보고일 및 실제 사용한 파서 버전.';

CREATE INDEX ix_disclosure_document_issuer_date
    ON disclosure_document (issuer_actor_id, report_date DESC);

CREATE TABLE document_entity (
    document_id     TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    matched_text    TEXT,
    link_method     VARCHAR(30),
    confidence      DOUBLE PRECISION,

    PRIMARY KEY (document_id, entity_id),

    CONSTRAINT ck_document_entity_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

COMMENT ON TABLE document_entity IS
'문서와 정규화 엔터티의 N:M 연결 결과. 사건 내 역할은 assertion_argument와 event_argument가 소유한다.';

CREATE INDEX ix_document_entity_entity ON document_entity (entity_id);

CREATE TABLE document_assertion (
    assertion_id        TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL,
    event_type_code     VARCHAR(120) NOT NULL,
    predicate_code      VARCHAR(80) NOT NULL,
    modality_code       VARCHAR(30) NOT NULL,
    confidence          DOUBLE PRECISION,
    lifecycle_stage     VARCHAR(50),
    available_at        TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_document_assertion_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

COMMENT ON TABLE document_assertion IS
'뉴스 또는 공시 문서에서 추출한 구조화 주장 한 건. 팀 논리 계약의 document_assertion에 대응한다.';

CREATE INDEX ix_document_assertion_document ON document_assertion (document_id);
CREATE INDEX ix_document_assertion_event_type ON document_assertion (event_type_code, predicate_code);

CREATE TABLE assertion_argument (
    assertion_argument_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assertion_id           TEXT NOT NULL,
    role_code              VARCHAR(80) NOT NULL,
    entity_id              TEXT NOT NULL,
    confidence             DOUBLE PRECISION,

    CONSTRAINT uq_assertion_argument
        UNIQUE (assertion_id, role_code, entity_id),
    CONSTRAINT ck_assertion_argument_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

COMMENT ON TABLE assertion_argument IS
'문서 주장 안에서 엔터티가 수행하는 공급자·고객·계약대상 등의 역할.';

CREATE INDEX ix_assertion_argument_entity ON assertion_argument (entity_id);

CREATE TABLE assertion_metric (
    assertion_metric_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assertion_id        TEXT NOT NULL,
    metric_code         VARCHAR(80) NOT NULL,
    numeric_value       NUMERIC(30, 8),
    unit_code           VARCHAR(30),
    period_basis        VARCHAR(30),
    integrity_status    VARCHAR(20),
    confidence          DOUBLE PRECISION,

    CONSTRAINT ck_assertion_metric_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    CONSTRAINT ck_assertion_metric_integrity_status
        CHECK (integrity_status IS NULL OR integrity_status IN ('OK', 'PARTIAL', 'CONFLICT', 'UNKNOWN'))
);

COMMENT ON TABLE assertion_metric IS
'문서 주장에서 추출한 금액·비율·기간 등의 정규화 수치.';

CREATE INDEX ix_assertion_metric_assertion_code
    ON assertion_metric (assertion_id, metric_code);

-- ============================================================================
-- 4. Source-local events and source-neutral event threads
-- ============================================================================

CREATE TABLE source_event (
    source_event_id     TEXT PRIMARY KEY,
    source_class        VARCHAR(20) NOT NULL,
    event_type_code     VARCHAR(120) NOT NULL,
    event_date          DATE,
    lifecycle_stage     VARCHAR(50),
    event_status        VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    available_at        TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_source_event_source_class
        CHECK (source_class IN ('NEWS', 'DISCLOSURE')),
    CONSTRAINT ck_source_event_status
        CHECK (event_status IN ('ACTIVE', 'REJECTED')),
    CONSTRAINT uq_source_event_id_class
        UNIQUE (source_event_id, source_class)
);

COMMENT ON TABLE source_event IS
'뉴스 또는 공시 한 소스에서 조립된 정규화 이벤트. 팀 문서의 source-local canonical_event에 대응한다.';

CREATE INDEX ix_source_event_type_date ON source_event (event_type_code, event_date);
CREATE INDEX ix_source_event_available_at ON source_event (available_at);

CREATE TABLE event_argument (
    source_event_id     TEXT NOT NULL,
    role_code           VARCHAR(80) NOT NULL,
    entity_id           TEXT NOT NULL,
    confidence          DOUBLE PRECISION,

    PRIMARY KEY (source_event_id, role_code, entity_id),

    CONSTRAINT ck_event_argument_confidence
        CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);

COMMENT ON TABLE event_argument IS
'여러 문서 주장을 조립한 뒤 소스 이벤트에서 확정한 엔터티 역할.';

CREATE INDEX ix_event_argument_entity ON event_argument (entity_id);

CREATE TABLE event_evidence (
    evidence_id         TEXT PRIMARY KEY,
    source_event_id     TEXT NOT NULL,
    assertion_id        TEXT NOT NULL,
    evidence_type       VARCHAR(30) NOT NULL,
    evidence_text       TEXT,
    link_confidence     DOUBLE PRECISION,

    CONSTRAINT uq_event_evidence
        UNIQUE (source_event_id, assertion_id, evidence_type),
    CONSTRAINT uq_event_evidence_event_id
        UNIQUE (source_event_id, evidence_id),
    CONSTRAINT ck_event_evidence_confidence
        CHECK (link_confidence IS NULL OR link_confidence BETWEEN 0 AND 1)
);

COMMENT ON TABLE event_evidence IS
'소스 이벤트를 뒷받침하는 문서 주장 근거와 연결 신뢰도.';

CREATE INDEX ix_event_evidence_assertion ON event_evidence (assertion_id);

CREATE TABLE event_thread (
    thread_id           TEXT PRIMARY KEY,
    thread_key          TEXT NOT NULL UNIQUE,
    event_type_code     VARCHAR(120) NOT NULL,
    current_stage       VARCHAR(50),
    opened_at           TIMESTAMPTZ,
    last_state_at       TIMESTAMPTZ,

    CONSTRAINT ck_event_thread_time
        CHECK (opened_at IS NULL OR last_state_at IS NULL OR last_state_at >= opened_at)
);

COMMENT ON TABLE event_thread IS
'뉴스와 공시의 소스 이벤트를 동일 실제 사건 계보로 묶는 소스 중립 thread header.';

CREATE INDEX ix_event_thread_type ON event_thread (event_type_code);

CREATE TABLE event_thread_link (
    source_event_id     TEXT PRIMARY KEY,
    thread_id           TEXT,
    source_class        VARCHAR(20) NOT NULL,
    novelty_status      VARCHAR(30) NOT NULL,
    link_type           VARCHAR(30),
    evaluated_at        TIMESTAMPTZ NOT NULL,
    unknown_reason      TEXT,

    CONSTRAINT ck_event_thread_link_source_class
        CHECK (source_class IN ('NEWS', 'DISCLOSURE')),
    CONSTRAINT ck_event_thread_link_novelty_status
        CHECK (novelty_status IN (
            'FIRST_IN_THREAD',
            'FOLLOW_UP_STAGE',
            'CORRECTION',
            'DUPLICATE_REBROADCAST',
            'UNKNOWN'
        )),
    CONSTRAINT ck_event_thread_link_unknown
        CHECK (
            (novelty_status = 'UNKNOWN' AND thread_id IS NULL)
            OR
            (novelty_status <> 'UNKNOWN' AND thread_id IS NOT NULL)
        )
);

COMMENT ON TABLE event_thread_link IS
'소스 이벤트 한 건의 thread 귀속 결과와 신규성 상태. UNKNOWN이면 thread_id가 NULL일 수 있다.';

CREATE INDEX ix_event_thread_link_thread ON event_thread_link (thread_id);

CREATE TABLE thread_discovery_snapshot (
    source_event_id             TEXT PRIMARY KEY,
    thread_id                   TEXT,
    prior_event_count           INTEGER,
    days_since_previous_stage   INTEGER,
    is_novel                    BOOLEAN,
    unknown_reason              TEXT,
    evaluated_at                TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_thread_discovery_prior_event_count
        CHECK (prior_event_count IS NULL OR prior_event_count >= 0),
    CONSTRAINT ck_thread_discovery_days
        CHECK (days_since_previous_stage IS NULL OR days_since_previous_stage >= 0)
);

COMMENT ON TABLE thread_discovery_snapshot IS
'소스 이벤트를 처음 thread 판정할 당시의 prior-event 상태와 novelty 판단 스냅샷.';

CREATE INDEX ix_thread_discovery_snapshot_thread ON thread_discovery_snapshot (thread_id);

-- ============================================================================
-- 5. Normalized disclosure facts
-- ============================================================================

CREATE TABLE disclosure_fact (
    fact_id             TEXT PRIMARY KEY,
    document_id         TEXT NOT NULL,
    fact_type           VARCHAR(30) NOT NULL,
    available_at        TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_disclosure_fact_type
        CHECK (fact_type IN ('SUPPLY_CONTRACT', 'BUSINESS_SEGMENT')),
    CONSTRAINT uq_disclosure_fact_id_type
        UNIQUE (fact_id, fact_type)
);

COMMENT ON TABLE disclosure_fact IS
'공시 parser가 생성한 정규화 사실의 공통 header. 유형별 상세는 typed child table이 소유한다.';

CREATE INDEX ix_disclosure_fact_document_type ON disclosure_fact (document_id, fact_type);
CREATE INDEX ix_disclosure_fact_type_available ON disclosure_fact (fact_type, available_at);

CREATE TABLE supply_contract_fact (
    fact_id                     TEXT PRIMARY KEY,
    fact_type                   VARCHAR(30)
        GENERATED ALWAYS AS ('SUPPLY_CONTRACT'::VARCHAR(30)) STORED,
    counterparty_actor_id       TEXT,
    counterparty_raw_name       TEXT,
    counterparty_withheld       BOOLEAN NOT NULL DEFAULT FALSE,
    contract_object_concept_id  TEXT,
    contract_amount_krw         NUMERIC(30, 0),
    revenue_ratio_pct           DOUBLE PRECISION,
    contract_start_date         DATE,
    contract_end_date           DATE,
    late_disclosure             BOOLEAN NOT NULL DEFAULT FALSE,
    completeness_status         VARCHAR(20),

    CONSTRAINT ck_supply_contract_counterparty
        CHECK (
            counterparty_actor_id IS NOT NULL
            OR counterparty_raw_name IS NOT NULL
            OR counterparty_withheld
        ),
    CONSTRAINT ck_supply_contract_withheld
        CHECK (NOT counterparty_withheld OR counterparty_actor_id IS NULL),
    CONSTRAINT ck_supply_contract_amount
        CHECK (contract_amount_krw IS NULL OR contract_amount_krw >= 0),
    CONSTRAINT ck_supply_contract_revenue_ratio
        CHECK (revenue_ratio_pct IS NULL OR revenue_ratio_pct >= 0),
    CONSTRAINT ck_supply_contract_dates
        CHECK (
            contract_start_date IS NULL
            OR contract_end_date IS NULL
            OR contract_end_date >= contract_start_date
        ),
    CONSTRAINT ck_supply_contract_completeness
        CHECK (completeness_status IS NULL OR completeness_status IN ('FULL', 'PARTIAL', 'REVIEW'))
);

COMMENT ON TABLE supply_contract_fact IS
'공급계약 공시의 상대방·계약대상·금액·매출대비 비율·계약기간 및 품질 상태.';

CREATE INDEX ix_supply_contract_counterparty ON supply_contract_fact (counterparty_actor_id);

CREATE TABLE business_segment_fact (
    fact_id                 TEXT PRIMARY KEY,
    fact_type               VARCHAR(30)
        GENERATED ALWAYS AS ('BUSINESS_SEGMENT'::VARCHAR(30)) STORED,
    segment_name            TEXT NOT NULL,
    concept_id              TEXT,
    period_label            VARCHAR(30),
    revenue_krw             NUMERIC(30, 0),
    revenue_share_pct       DOUBLE PRECISION,
    share_basis             VARCHAR(20),
    concept_link_confidence DOUBLE PRECISION,

    CONSTRAINT ck_business_segment_revenue
        CHECK (revenue_krw IS NULL OR revenue_krw >= 0),
    CONSTRAINT ck_business_segment_share
        CHECK (revenue_share_pct IS NULL OR revenue_share_pct >= 0),
    CONSTRAINT ck_business_segment_link_confidence
        CHECK (concept_link_confidence IS NULL OR concept_link_confidence BETWEEN 0 AND 1),
    CONSTRAINT ck_business_segment_share_basis
        CHECK (share_basis IS NULL OR share_basis IN ('REPORTED', 'COMPUTED', 'RESCALED', 'UNRELIABLE'))
);

COMMENT ON TABLE business_segment_fact IS
'공시·기간·사업부문 grain의 매출액·매출비중과 정규화 concept 연결.';

CREATE INDEX ix_business_segment_concept ON business_segment_fact (concept_id);

-- ============================================================================
-- 6. Price ledger, observations, decompositions, and event-price validation
-- ============================================================================

CREATE TABLE price_daily (
    instrument_id       TEXT NOT NULL,
    trade_date          DATE NOT NULL,
    close_price         NUMERIC(24, 8),
    adjusted_close_price NUMERIC(24, 8),
    simple_return       DOUBLE PRECISION,
    log_return          DOUBLE PRECISION,
    volume              BIGINT,
    turnover_value      NUMERIC(30, 4),
    price_basis         VARCHAR(30),
    available_at        TIMESTAMPTZ NOT NULL,
    data_version        VARCHAR(50) NOT NULL,

    PRIMARY KEY (instrument_id, trade_date),

    CONSTRAINT ck_price_daily_values
        CHECK (
            (close_price IS NULL OR close_price > 0)
            AND (adjusted_close_price IS NULL OR adjusted_close_price > 0)
            AND (volume IS NULL OR volume >= 0)
            AND (turnover_value IS NULL OR turnover_value >= 0)
            AND (simple_return IS NULL OR simple_return >= -1)
        )
);

COMMENT ON TABLE price_daily IS
'금융상품·거래일 grain의 일별 가격 원장. 트리거가 없는 날에도 계속 적재된다.';

CREATE INDEX ix_price_daily_trade_date ON price_daily (trade_date);

CREATE TABLE price_observation (
    price_observation_id        TEXT PRIMARY KEY,
    price_movement_trigger_id   TEXT NOT NULL,
    instrument_id               TEXT NOT NULL,
    window_type                 VARCHAR(30) NOT NULL,
    window_start_at             TIMESTAMPTZ NOT NULL,
    window_end_at               TIMESTAMPTZ NOT NULL,
    anchor_trade_date           DATE NOT NULL,
    evaluation_as_of            TIMESTAMPTZ NOT NULL,
    policy_version              VARCHAR(50) NOT NULL,
    data_version                VARCHAR(50) NOT NULL,

    CONSTRAINT ck_price_observation_window
        CHECK (window_end_at > window_start_at)
);

COMMENT ON TABLE price_observation IS
'가격변동트리거를 분석하기 위해 설정한 금융상품·시간구간 grain의 관찰창.';

CREATE INDEX ix_price_observation_trigger
    ON price_observation (price_movement_trigger_id);
CREATE INDEX ix_price_observation_instrument_date
    ON price_observation (instrument_id, anchor_trade_date);

CREATE TABLE price_decomposition (
    price_decomposition_id      TEXT PRIMARY KEY,
    price_observation_id        TEXT NOT NULL,
    benchmark_series_id         TEXT NOT NULL,
    target_return               DOUBLE PRECISION,
    market_return               DOUBLE PRECISION,
    peer_raw_return             DOUBLE PRECISION,
    peer_orthogonal_return      DOUBLE PRECISION,
    intercept_contribution_return DOUBLE PRECISION,
    market_explained_return     DOUBLE PRECISION,
    peer_explained_return       DOUBLE PRECISION,
    explained_return            DOUBLE PRECISION,
    residual_return             DOUBLE PRECISION,
    volume_abnormality          DOUBLE PRECISION,
    volatility_change           DOUBLE PRECISION,
    residual_gate_status        VARCHAR(20),
    residual_gate_score         DOUBLE PRECISION,
    policy_version              VARCHAR(50) NOT NULL,
    model_version               VARCHAR(50) NOT NULL,
    evaluation_as_of            TIMESTAMPTZ NOT NULL,
    input_uri                   TEXT,

    CONSTRAINT ck_price_decomposition_gate_status
        CHECK (residual_gate_status IS NULL OR residual_gate_status IN ('PASS', 'FAIL', 'REVIEW'))
);

COMMENT ON TABLE price_decomposition IS
'가격관찰을 특정 benchmark·peer·정책·모델로 분해한 계산 결과. 가격관찰과 1:N 관계를 가진다.';

CREATE INDEX ix_price_decomposition_observation
    ON price_decomposition (price_observation_id);
CREATE INDEX ix_price_decomposition_benchmark
    ON price_decomposition (benchmark_series_id);

CREATE TABLE price_decomposition_peer (
    price_decomposition_id  TEXT NOT NULL,
    peer_instrument_id      TEXT NOT NULL,
    peer_rank               INTEGER,
    peer_weight_ratio       DOUBLE PRECISION,
    peer_return             DOUBLE PRECISION,
    peer_orthogonal_return  DOUBLE PRECISION,

    PRIMARY KEY (price_decomposition_id, peer_instrument_id),

    CONSTRAINT uq_price_decomposition_peer_rank
        UNIQUE (price_decomposition_id, peer_rank),
    CONSTRAINT ck_price_decomposition_peer_rank
        CHECK (peer_rank IS NULL OR peer_rank > 0),
    CONSTRAINT ck_price_decomposition_peer_weight
        CHECK (peer_weight_ratio IS NULL OR peer_weight_ratio BETWEEN 0 AND 1)
);

COMMENT ON TABLE price_decomposition_peer IS
'가격분해에 실제 사용한 피어 금융상품의 구성·순위·가중치·수익률.';

CREATE INDEX ix_price_decomposition_peer_instrument
    ON price_decomposition_peer (peer_instrument_id);

CREATE TABLE event_price_observation (
    event_price_observation_id  TEXT PRIMARY KEY,
    source_event_id             TEXT NOT NULL,
    instrument_id               TEXT NOT NULL,
    anchor_evidence_id          TEXT,
    benchmark_series_id         TEXT NOT NULL,
    window_start_at             TIMESTAMPTZ NOT NULL,
    window_end_at               TIMESTAMPTZ NOT NULL,
    simple_return               DOUBLE PRECISION,
    market_return               DOUBLE PRECISION,
    peer_orthogonal_return      DOUBLE PRECISION,
    explained_return            DOUBLE PRECISION,
    residual_return             DOUBLE PRECISION,
    window_status               VARCHAR(30),
    is_confounded               BOOLEAN NOT NULL DEFAULT FALSE,
    evaluation_as_of            TIMESTAMPTZ NOT NULL,
    policy_version              VARCHAR(50) NOT NULL,
    input_uri                   TEXT,

    CONSTRAINT ck_event_price_observation_window
        CHECK (window_end_at > window_start_at),
    CONSTRAINT ck_event_price_observation_status
        CHECK (
            window_status IS NULL
            OR window_status IN ('VALID', 'PARTIAL', 'CENSORED', 'NO_PRICE', 'NOT_YET_OBSERVABLE')
        ),
    CONSTRAINT uq_event_price_observation
        UNIQUE (
            source_event_id,
            instrument_id,
            window_start_at,
            window_end_at,
            policy_version
        )
);

COMMENT ON TABLE event_price_observation IS
'소스이벤트·금융상품·반응창 grain의 가격 검증 결과. 방향·크기·타이밍 정합성을 확인하되 인과를 확정하지 않는다.';

CREATE INDEX ix_event_price_observation_event
    ON event_price_observation (source_event_id);
CREATE INDEX ix_event_price_observation_instrument
    ON event_price_observation (instrument_id, window_start_at);

CREATE TABLE event_price_observation_peer (
    event_price_observation_id  TEXT NOT NULL,
    peer_instrument_id          TEXT NOT NULL,
    peer_rank                   INTEGER,
    peer_weight_ratio           DOUBLE PRECISION,
    peer_return                 DOUBLE PRECISION,
    peer_orthogonal_return      DOUBLE PRECISION,

    PRIMARY KEY (event_price_observation_id, peer_instrument_id),

    CONSTRAINT uq_event_price_observation_peer_rank
        UNIQUE (event_price_observation_id, peer_rank),
    CONSTRAINT ck_event_price_observation_peer_rank
        CHECK (peer_rank IS NULL OR peer_rank > 0),
    CONSTRAINT ck_event_price_observation_peer_weight
        CHECK (peer_weight_ratio IS NULL OR peer_weight_ratio BETWEEN 0 AND 1)
);

COMMENT ON TABLE event_price_observation_peer IS
'이벤트 가격 검증에 실제 사용한 피어 금융상품과 입력 수익률.';

CREATE INDEX ix_event_price_observation_peer_instrument
    ON event_price_observation_peer (peer_instrument_id);

-- ============================================================================
-- 7. Minimal release/version management, explanation runs, and serving results
-- ============================================================================

CREATE TABLE release_bundle (
    bundle_version      VARCHAR(30) PRIMARY KEY,
    component_versions  JSONB NOT NULL,
    component_hash      CHAR(64) NOT NULL,
    status              VARCHAR(20) NOT NULL,
    release_notes       TEXT,
    published_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_release_bundle_component_versions
        CHECK (jsonb_typeof(component_versions) = 'object'),
    CONSTRAINT ck_release_bundle_component_hash
        CHECK (component_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_release_bundle_status
        CHECK (status IN ('DRAFT', 'PUBLISHED', 'REVOKED')),
    CONSTRAINT ck_release_bundle_publish_state
        CHECK (
            (status = 'DRAFT' AND published_at IS NULL)
            OR
            (status IN ('PUBLISHED', 'REVOKED') AND published_at IS NOT NULL)
        )
);

COMMENT ON TABLE release_bundle IS
'고객사가 승인·적용하는 EDGE 제품 버전. 분석엔진·설명엔진·파서·ML/LLM·프롬프트·정책 버전은 component_versions JSONB에 불변 manifest로 저장한다.';

CREATE INDEX ix_release_bundle_component_hash ON release_bundle (component_hash);

CREATE TABLE explanation_run (
    explanation_run_id     TEXT PRIMARY KEY,
    explanation_route_id   TEXT NOT NULL,
    bundle_version         VARCHAR(30) NOT NULL,
    explanation_as_of      TIMESTAMPTZ NOT NULL,
    run_reason             VARCHAR(30),
    run_status             VARCHAR(20) NOT NULL,
    started_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at            TIMESTAMPTZ,

    CONSTRAINT ck_explanation_run_status
        CHECK (run_status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    CONSTRAINT ck_explanation_run_time
        CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT ck_explanation_run_finished_status
        CHECK (
            (run_status IN ('PENDING', 'RUNNING') AND finished_at IS NULL)
            OR
            (run_status IN ('SUCCEEDED', 'FAILED', 'CANCELLED') AND finished_at IS NOT NULL)
        )
);

COMMENT ON TABLE explanation_run IS
'Explanation Engine 실행 한 번. 실제 사용한 고객 공개 릴리스 번들을 bundle_version으로 고정한다.';

CREATE INDEX ix_explanation_run_route ON explanation_run (explanation_route_id);
CREATE INDEX ix_explanation_run_bundle ON explanation_run (bundle_version);
CREATE INDEX ix_explanation_run_latest_success
    ON explanation_run (explanation_as_of DESC, finished_at DESC)
    WHERE run_status = 'SUCCEEDED';

CREATE TABLE explanation_run_event_evidence (
    explanation_run_id  TEXT NOT NULL,
    evidence_id         TEXT NOT NULL,
    stage_code          VARCHAR(10) NOT NULL,
    usage_type          VARCHAR(30),
    priority            INTEGER,
    weight_ratio        DOUBLE PRECISION,

    PRIMARY KEY (explanation_run_id, evidence_id, stage_code),

    CONSTRAINT ck_explanation_run_event_evidence_priority
        CHECK (priority IS NULL OR priority > 0),
    CONSTRAINT ck_explanation_run_event_evidence_weight
        CHECK (weight_ratio IS NULL OR weight_ratio BETWEEN 0 AND 1)
);

COMMENT ON TABLE explanation_run_event_evidence IS
'설명실행이 특정 단계에서 실제 사용한 이벤트 근거의 lineage.';

CREATE INDEX ix_explanation_run_event_evidence_reverse
    ON explanation_run_event_evidence (evidence_id, explanation_run_id);

CREATE TABLE explanation_run_disclosure_fact (
    explanation_run_id  TEXT NOT NULL,
    fact_id             TEXT NOT NULL,
    stage_code          VARCHAR(10) NOT NULL,
    usage_type          VARCHAR(30),
    priority            INTEGER,
    weight_ratio        DOUBLE PRECISION,

    PRIMARY KEY (explanation_run_id, fact_id, stage_code),

    CONSTRAINT ck_explanation_run_disclosure_fact_priority
        CHECK (priority IS NULL OR priority > 0),
    CONSTRAINT ck_explanation_run_disclosure_fact_weight
        CHECK (weight_ratio IS NULL OR weight_ratio BETWEEN 0 AND 1)
);

COMMENT ON TABLE explanation_run_disclosure_fact IS
'설명실행이 중요도·무결성 판단에 실제 사용한 공시 정규화 사실의 lineage.';

CREATE INDEX ix_explanation_run_disclosure_fact_reverse
    ON explanation_run_disclosure_fact (fact_id, explanation_run_id);

CREATE TABLE explanation_run_event_price_observation (
    explanation_run_id          TEXT NOT NULL,
    event_price_observation_id  TEXT NOT NULL,
    stage_code                  VARCHAR(10) NOT NULL,
    usage_type                  VARCHAR(30),
    priority                    INTEGER,
    weight_ratio                DOUBLE PRECISION,

    PRIMARY KEY (explanation_run_id, event_price_observation_id, stage_code),

    CONSTRAINT ck_explanation_run_event_price_priority
        CHECK (priority IS NULL OR priority > 0),
    CONSTRAINT ck_explanation_run_event_price_weight
        CHECK (weight_ratio IS NULL OR weight_ratio BETWEEN 0 AND 1)
);

COMMENT ON TABLE explanation_run_event_price_observation IS
'설명실행이 가격 정합성·체크포인트 판단에 실제 사용한 이벤트 가격 관찰 lineage.';

CREATE INDEX ix_explanation_run_event_price_reverse
    ON explanation_run_event_price_observation (event_price_observation_id, explanation_run_id);

CREATE TABLE explanation_result (
    explanation_result_id   TEXT PRIMARY KEY,
    explanation_run_id      TEXT NOT NULL UNIQUE,
    etf_instrument_id       TEXT NOT NULL,
    trade_date              DATE NOT NULL,
    explanation_as_of       TIMESTAMPTZ NOT NULL,
    primary_thread_id       TEXT,
    explanation_type        VARCHAR(30) NOT NULL,
    summary                 TEXT NOT NULL,
    confidence_level        VARCHAR(20),
    stage_results           JSONB,
    publication_status      VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    generated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    result_uri              TEXT,

    CONSTRAINT ck_explanation_result_type
        CHECK (explanation_type IN ('PRICE_ONLY', 'EVENT_SUPPORTED', 'MIXED', 'UNCERTAIN')),
    CONSTRAINT ck_explanation_result_confidence
        CHECK (confidence_level IS NULL OR confidence_level IN ('HIGH', 'MEDIUM', 'LOW')),
    CONSTRAINT ck_explanation_result_publication_status
        CHECK (publication_status IN ('DRAFT', 'PUBLISHED', 'WITHDRAWN'))
);

COMMENT ON TABLE explanation_result IS
'API·MTS가 직접 읽는 ETF·거래일·기준시각 grain의 최종 설명. 조회 편의를 위해 ETF와 거래일을 의도적으로 중복 저장한다.';

CREATE INDEX ix_explanation_result_serving
    ON explanation_result (etf_instrument_id, trade_date DESC, explanation_as_of DESC);
CREATE INDEX ix_explanation_result_published
    ON explanation_result (etf_instrument_id, trade_date DESC, generated_at DESC)
    WHERE publication_status = 'PUBLISHED';

CREATE TABLE tenant (
    tenant_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_name         VARCHAR(100) NOT NULL UNIQUE,
    environment         VARCHAR(10) NOT NULL,
    status              VARCHAR(20) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_tenant_environment
        CHECK (environment IN ('DEV', 'PROD')),
    CONSTRAINT ck_tenant_status
        CHECK (status IN ('ONBOARDING', 'ACTIVE', 'SYNC_DELAYED'))
);

COMMENT ON TABLE tenant IS
'EDGE를 사용하는 고객사 환경 한 개.';

CREATE TABLE tenant_release_history (
    tenant_id           BIGINT NOT NULL,
    bundle_version      VARCHAR(30) NOT NULL,
    approved_by         VARCHAR(100) NOT NULL,
    approved_at         TIMESTAMPTZ NOT NULL,
    applied_at          TIMESTAMPTZ,

    PRIMARY KEY (tenant_id, bundle_version, approved_at),

    CONSTRAINT ck_tenant_release_history_time
        CHECK (applied_at IS NULL OR applied_at >= approved_at)
);

COMMENT ON TABLE tenant_release_history IS
'테넌트가 특정 릴리스 번들을 승인·적용한 이력. 롤백은 과거 행 수정이 아니라 이전 번들을 가리키는 새 행으로 남긴다.';

CREATE INDEX ix_tenant_release_history_current
    ON tenant_release_history (tenant_id, applied_at DESC)
    WHERE applied_at IS NOT NULL;

CREATE TABLE tenant_credential (
    credential_hash     CHAR(64) PRIMARY KEY,
    tenant_id           BIGINT NOT NULL,
    status              VARCHAR(20) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at          TIMESTAMPTZ,

    CONSTRAINT ck_tenant_credential_hash
        CHECK (credential_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_tenant_credential_status
        CHECK (status IN ('ACTIVE', 'REVOKED')),
    CONSTRAINT ck_tenant_credential_revocation
        CHECK (
            (status = 'ACTIVE' AND revoked_at IS NULL)
            OR
            (status = 'REVOKED' AND revoked_at IS NOT NULL)
        )
);

COMMENT ON TABLE tenant_credential IS
'테넌트 동기화 채널의 인증 자격증명. 원문 키는 저장하지 않고 SHA-256 해시만 저장한다.';

CREATE INDEX ix_tenant_credential_tenant ON tenant_credential (tenant_id);

CREATE TABLE tenant_sync_cursor (
    tenant_id           BIGINT PRIMARY KEY,
    cursor_at           TIMESTAMPTZ NOT NULL,
    last_synced_at      TIMESTAMPTZ NOT NULL,

    CONSTRAINT ck_tenant_sync_cursor_time
        CHECK (last_synced_at >= cursor_at)
);

COMMENT ON TABLE tenant_sync_cursor IS
'테넌트 pull 동기화의 마지막 수신 커서와 실제 동기화 시각.';

-- ============================================================================
-- Foreign keys
-- ============================================================================

-- Entity subtype integrity
ALTER TABLE actor
    ADD CONSTRAINT fk_actor_entity
    FOREIGN KEY (actor_id, entity_type)
    REFERENCES entity (entity_id, entity_type)
    ON DELETE CASCADE;

ALTER TABLE company_profile
    ADD CONSTRAINT fk_company_profile_actor
    FOREIGN KEY (actor_id, actor_type)
    REFERENCES actor (actor_id, actor_type)
    ON DELETE CASCADE;

ALTER TABLE instrument
    ADD CONSTRAINT fk_instrument_entity
    FOREIGN KEY (instrument_id, entity_type)
    REFERENCES entity (entity_id, entity_type)
    ON DELETE CASCADE;

ALTER TABLE concept
    ADD CONSTRAINT fk_concept_entity
    FOREIGN KEY (concept_id, entity_type)
    REFERENCES entity (entity_id, entity_type)
    ON DELETE CASCADE;

ALTER TABLE concept
    ADD CONSTRAINT fk_concept_parent
    FOREIGN KEY (parent_concept_id)
    REFERENCES concept (concept_id)
    ON DELETE SET NULL;

ALTER TABLE etf_profile
    ADD CONSTRAINT fk_etf_profile_instrument
    FOREIGN KEY (instrument_id, instrument_type)
    REFERENCES instrument (instrument_id, instrument_type)
    ON DELETE CASCADE;

ALTER TABLE etf_profile
    ADD CONSTRAINT fk_etf_profile_tracking_series
    FOREIGN KEY (tracking_market_series_id)
    REFERENCES market_series (market_series_id)
    ON DELETE SET NULL;

ALTER TABLE etf_profile
    ADD CONSTRAINT fk_etf_profile_theme
    FOREIGN KEY (primary_theme_concept_id)
    REFERENCES concept (concept_id)
    ON DELETE SET NULL;

ALTER TABLE equity_profile
    ADD CONSTRAINT fk_equity_profile_instrument
    FOREIGN KEY (instrument_id, instrument_type)
    REFERENCES instrument (instrument_id, instrument_type)
    ON DELETE CASCADE;

ALTER TABLE equity_profile
    ADD CONSTRAINT fk_equity_profile_issuer
    FOREIGN KEY (issuer_actor_id)
    REFERENCES company_profile (actor_id)
    ON DELETE RESTRICT;

-- ETF inputs and routing
ALTER TABLE etf_holding_snapshot
    ADD CONSTRAINT fk_etf_holding_snapshot_etf
    FOREIGN KEY (etf_instrument_id)
    REFERENCES etf_profile (instrument_id)
    ON DELETE CASCADE;

ALTER TABLE etf_holding_snapshot
    ADD CONSTRAINT fk_etf_holding_snapshot_constituent
    FOREIGN KEY (constituent_instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE RESTRICT;

ALTER TABLE etf_nav_daily
    ADD CONSTRAINT fk_etf_nav_daily_etf
    FOREIGN KEY (etf_instrument_id)
    REFERENCES etf_profile (instrument_id)
    ON DELETE CASCADE;

ALTER TABLE price_movement_trigger
    ADD CONSTRAINT fk_price_movement_trigger_etf
    FOREIGN KEY (etf_instrument_id)
    REFERENCES etf_profile (instrument_id)
    ON DELETE RESTRICT;

ALTER TABLE etf_contribution_observation
    ADD CONSTRAINT fk_etf_contribution_trigger
    FOREIGN KEY (price_movement_trigger_id)
    REFERENCES price_movement_trigger (price_movement_trigger_id)
    ON DELETE CASCADE;

ALTER TABLE etf_contribution_member
    ADD CONSTRAINT fk_etf_contribution_member_observation
    FOREIGN KEY (contribution_observation_id)
    REFERENCES etf_contribution_observation (contribution_observation_id)
    ON DELETE CASCADE;

ALTER TABLE etf_contribution_member
    ADD CONSTRAINT fk_etf_contribution_member_instrument
    FOREIGN KEY (constituent_instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_route
    ADD CONSTRAINT fk_explanation_route_contribution
    FOREIGN KEY (contribution_observation_id)
    REFERENCES etf_contribution_observation (contribution_observation_id)
    ON DELETE CASCADE;

ALTER TABLE explanation_route_target
    ADD CONSTRAINT fk_explanation_route_target_route
    FOREIGN KEY (explanation_route_id)
    REFERENCES explanation_route (explanation_route_id)
    ON DELETE CASCADE;

ALTER TABLE explanation_route_target
    ADD CONSTRAINT fk_explanation_route_target_entity
    FOREIGN KEY (target_entity_id)
    REFERENCES entity (entity_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_route_target
    ADD CONSTRAINT fk_explanation_route_target_source_instrument
    FOREIGN KEY (source_instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE SET NULL;

-- Documents and assertions
ALTER TABLE news_document
    ADD CONSTRAINT fk_news_document_type
    FOREIGN KEY (document_id, document_type)
    REFERENCES document (document_id, document_type)
    ON DELETE CASCADE;

ALTER TABLE news_document
    ADD CONSTRAINT fk_news_document_representative
    FOREIGN KEY (representative_document_id)
    REFERENCES news_document (document_id)
    ON DELETE SET NULL;

ALTER TABLE news_document
    ADD CONSTRAINT fk_news_document_theme
    FOREIGN KEY (theme_concept_id)
    REFERENCES concept (concept_id)
    ON DELETE SET NULL;

ALTER TABLE disclosure_document
    ADD CONSTRAINT fk_disclosure_document_type
    FOREIGN KEY (document_id, document_type)
    REFERENCES document (document_id, document_type)
    ON DELETE CASCADE;

ALTER TABLE disclosure_document
    ADD CONSTRAINT fk_disclosure_document_issuer
    FOREIGN KEY (issuer_actor_id)
    REFERENCES company_profile (actor_id)
    ON DELETE RESTRICT;

ALTER TABLE document_entity
    ADD CONSTRAINT fk_document_entity_document
    FOREIGN KEY (document_id)
    REFERENCES document (document_id)
    ON DELETE CASCADE;

ALTER TABLE document_entity
    ADD CONSTRAINT fk_document_entity_entity
    FOREIGN KEY (entity_id)
    REFERENCES entity (entity_id)
    ON DELETE RESTRICT;

ALTER TABLE document_assertion
    ADD CONSTRAINT fk_document_assertion_document
    FOREIGN KEY (document_id)
    REFERENCES document (document_id)
    ON DELETE CASCADE;

ALTER TABLE assertion_argument
    ADD CONSTRAINT fk_assertion_argument_assertion
    FOREIGN KEY (assertion_id)
    REFERENCES document_assertion (assertion_id)
    ON DELETE CASCADE;

ALTER TABLE assertion_argument
    ADD CONSTRAINT fk_assertion_argument_entity
    FOREIGN KEY (entity_id)
    REFERENCES entity (entity_id)
    ON DELETE RESTRICT;

ALTER TABLE assertion_metric
    ADD CONSTRAINT fk_assertion_metric_assertion
    FOREIGN KEY (assertion_id)
    REFERENCES document_assertion (assertion_id)
    ON DELETE CASCADE;

-- Source events and threads
ALTER TABLE event_argument
    ADD CONSTRAINT fk_event_argument_event
    FOREIGN KEY (source_event_id)
    REFERENCES source_event (source_event_id)
    ON DELETE CASCADE;

ALTER TABLE event_argument
    ADD CONSTRAINT fk_event_argument_entity
    FOREIGN KEY (entity_id)
    REFERENCES entity (entity_id)
    ON DELETE RESTRICT;

ALTER TABLE event_evidence
    ADD CONSTRAINT fk_event_evidence_event
    FOREIGN KEY (source_event_id)
    REFERENCES source_event (source_event_id)
    ON DELETE CASCADE;

ALTER TABLE event_evidence
    ADD CONSTRAINT fk_event_evidence_assertion
    FOREIGN KEY (assertion_id)
    REFERENCES document_assertion (assertion_id)
    ON DELETE RESTRICT;

ALTER TABLE event_thread_link
    ADD CONSTRAINT fk_event_thread_link_event_class
    FOREIGN KEY (source_event_id, source_class)
    REFERENCES source_event (source_event_id, source_class)
    ON DELETE CASCADE;

ALTER TABLE event_thread_link
    ADD CONSTRAINT fk_event_thread_link_thread
    FOREIGN KEY (thread_id)
    REFERENCES event_thread (thread_id)
    ON DELETE RESTRICT;

ALTER TABLE thread_discovery_snapshot
    ADD CONSTRAINT fk_thread_discovery_event
    FOREIGN KEY (source_event_id)
    REFERENCES source_event (source_event_id)
    ON DELETE CASCADE;

ALTER TABLE thread_discovery_snapshot
    ADD CONSTRAINT fk_thread_discovery_thread
    FOREIGN KEY (thread_id)
    REFERENCES event_thread (thread_id)
    ON DELETE SET NULL;

-- Disclosure facts
ALTER TABLE disclosure_fact
    ADD CONSTRAINT fk_disclosure_fact_document
    FOREIGN KEY (document_id)
    REFERENCES disclosure_document (document_id)
    ON DELETE CASCADE;

ALTER TABLE supply_contract_fact
    ADD CONSTRAINT fk_supply_contract_fact_type
    FOREIGN KEY (fact_id, fact_type)
    REFERENCES disclosure_fact (fact_id, fact_type)
    ON DELETE CASCADE;

ALTER TABLE supply_contract_fact
    ADD CONSTRAINT fk_supply_contract_counterparty
    FOREIGN KEY (counterparty_actor_id)
    REFERENCES actor (actor_id)
    ON DELETE SET NULL;

ALTER TABLE supply_contract_fact
    ADD CONSTRAINT fk_supply_contract_concept
    FOREIGN KEY (contract_object_concept_id)
    REFERENCES concept (concept_id)
    ON DELETE SET NULL;

ALTER TABLE business_segment_fact
    ADD CONSTRAINT fk_business_segment_fact_type
    FOREIGN KEY (fact_id, fact_type)
    REFERENCES disclosure_fact (fact_id, fact_type)
    ON DELETE CASCADE;

ALTER TABLE business_segment_fact
    ADD CONSTRAINT fk_business_segment_concept
    FOREIGN KEY (concept_id)
    REFERENCES concept (concept_id)
    ON DELETE SET NULL;

-- Price observations and decompositions
ALTER TABLE price_daily
    ADD CONSTRAINT fk_price_daily_instrument
    FOREIGN KEY (instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE CASCADE;

ALTER TABLE price_observation
    ADD CONSTRAINT fk_price_observation_trigger
    FOREIGN KEY (price_movement_trigger_id)
    REFERENCES price_movement_trigger (price_movement_trigger_id)
    ON DELETE CASCADE;

ALTER TABLE price_observation
    ADD CONSTRAINT fk_price_observation_instrument
    FOREIGN KEY (instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE RESTRICT;

ALTER TABLE price_decomposition
    ADD CONSTRAINT fk_price_decomposition_observation
    FOREIGN KEY (price_observation_id)
    REFERENCES price_observation (price_observation_id)
    ON DELETE CASCADE;

ALTER TABLE price_decomposition
    ADD CONSTRAINT fk_price_decomposition_benchmark
    FOREIGN KEY (benchmark_series_id)
    REFERENCES market_series (market_series_id)
    ON DELETE RESTRICT;

ALTER TABLE price_decomposition_peer
    ADD CONSTRAINT fk_price_decomposition_peer_decomposition
    FOREIGN KEY (price_decomposition_id)
    REFERENCES price_decomposition (price_decomposition_id)
    ON DELETE CASCADE;

ALTER TABLE price_decomposition_peer
    ADD CONSTRAINT fk_price_decomposition_peer_instrument
    FOREIGN KEY (peer_instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_route_decomposition
    ADD CONSTRAINT fk_explanation_route_decomposition_route
    FOREIGN KEY (explanation_route_id)
    REFERENCES explanation_route (explanation_route_id)
    ON DELETE CASCADE;

ALTER TABLE explanation_route_decomposition
    ADD CONSTRAINT fk_explanation_route_decomposition_price
    FOREIGN KEY (price_decomposition_id)
    REFERENCES price_decomposition (price_decomposition_id)
    ON DELETE RESTRICT;

-- Event-price validation
ALTER TABLE event_price_observation
    ADD CONSTRAINT fk_event_price_observation_event
    FOREIGN KEY (source_event_id)
    REFERENCES source_event (source_event_id)
    ON DELETE CASCADE;

ALTER TABLE event_price_observation
    ADD CONSTRAINT fk_event_price_observation_instrument
    FOREIGN KEY (instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE RESTRICT;

ALTER TABLE event_price_observation
    ADD CONSTRAINT fk_event_price_observation_anchor_same_event
    FOREIGN KEY (source_event_id, anchor_evidence_id)
    REFERENCES event_evidence (source_event_id, evidence_id)
    ON DELETE RESTRICT;

ALTER TABLE event_price_observation
    ADD CONSTRAINT fk_event_price_observation_benchmark
    FOREIGN KEY (benchmark_series_id)
    REFERENCES market_series (market_series_id)
    ON DELETE RESTRICT;

ALTER TABLE event_price_observation_peer
    ADD CONSTRAINT fk_event_price_observation_peer_observation
    FOREIGN KEY (event_price_observation_id)
    REFERENCES event_price_observation (event_price_observation_id)
    ON DELETE CASCADE;

ALTER TABLE event_price_observation_peer
    ADD CONSTRAINT fk_event_price_observation_peer_instrument
    FOREIGN KEY (peer_instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE RESTRICT;

-- Explanation runs, results, and release bundle
ALTER TABLE explanation_run
    ADD CONSTRAINT fk_explanation_run_route
    FOREIGN KEY (explanation_route_id)
    REFERENCES explanation_route (explanation_route_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_run
    ADD CONSTRAINT fk_explanation_run_bundle
    FOREIGN KEY (bundle_version)
    REFERENCES release_bundle (bundle_version)
    ON DELETE RESTRICT;

ALTER TABLE explanation_run_event_evidence
    ADD CONSTRAINT fk_explanation_run_event_evidence_run
    FOREIGN KEY (explanation_run_id)
    REFERENCES explanation_run (explanation_run_id)
    ON DELETE CASCADE;

ALTER TABLE explanation_run_event_evidence
    ADD CONSTRAINT fk_explanation_run_event_evidence_evidence
    FOREIGN KEY (evidence_id)
    REFERENCES event_evidence (evidence_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_run_disclosure_fact
    ADD CONSTRAINT fk_explanation_run_disclosure_fact_run
    FOREIGN KEY (explanation_run_id)
    REFERENCES explanation_run (explanation_run_id)
    ON DELETE CASCADE;

ALTER TABLE explanation_run_disclosure_fact
    ADD CONSTRAINT fk_explanation_run_disclosure_fact_fact
    FOREIGN KEY (fact_id)
    REFERENCES disclosure_fact (fact_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_run_event_price_observation
    ADD CONSTRAINT fk_explanation_run_event_price_run
    FOREIGN KEY (explanation_run_id)
    REFERENCES explanation_run (explanation_run_id)
    ON DELETE CASCADE;

ALTER TABLE explanation_run_event_price_observation
    ADD CONSTRAINT fk_explanation_run_event_price_observation
    FOREIGN KEY (event_price_observation_id)
    REFERENCES event_price_observation (event_price_observation_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_result
    ADD CONSTRAINT fk_explanation_result_run
    FOREIGN KEY (explanation_run_id)
    REFERENCES explanation_run (explanation_run_id)
    ON DELETE CASCADE;

ALTER TABLE explanation_result
    ADD CONSTRAINT fk_explanation_result_etf
    FOREIGN KEY (etf_instrument_id)
    REFERENCES etf_profile (instrument_id)
    ON DELETE RESTRICT;

ALTER TABLE explanation_result
    ADD CONSTRAINT fk_explanation_result_thread
    FOREIGN KEY (primary_thread_id)
    REFERENCES event_thread (thread_id)
    ON DELETE SET NULL;

-- Tenant release and synchronization
ALTER TABLE tenant_release_history
    ADD CONSTRAINT fk_tenant_release_history_tenant
    FOREIGN KEY (tenant_id)
    REFERENCES tenant (tenant_id)
    ON DELETE CASCADE;

ALTER TABLE tenant_release_history
    ADD CONSTRAINT fk_tenant_release_history_bundle
    FOREIGN KEY (bundle_version)
    REFERENCES release_bundle (bundle_version)
    ON DELETE RESTRICT;

ALTER TABLE tenant_credential
    ADD CONSTRAINT fk_tenant_credential_tenant
    FOREIGN KEY (tenant_id)
    REFERENCES tenant (tenant_id)
    ON DELETE CASCADE;

ALTER TABLE tenant_sync_cursor
    ADD CONSTRAINT fk_tenant_sync_cursor_tenant
    FOREIGN KEY (tenant_id)
    REFERENCES tenant (tenant_id)
    ON DELETE CASCADE;
