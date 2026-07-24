-- ============================================================
-- AI Stock Analysis Mart + SaaS ERD - MVP (baseline)
-- DBML "AI Stock Analysis Mart + SaaS ERD - MVP" → PostgreSQL DDL
-- 타깃 스키마: public (기본)
-- ============================================================

SET search_path TO public;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- updated_at 자동 갱신용 공통 트리거 함수 (DEFAULT now()는 INSERT에만 적용되므로 필요)
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;


-- ============================================================
-- 1. 분석 콘텐츠 도메인
-- ============================================================

-- 1-1. 분석 대상
CREATE TABLE instruments (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    market_code     text        NOT NULL,
    ticker          text        NOT NULL,
    name            text        NOT NULL,
    instrument_type text        NOT NULL DEFAULT 'stock',
    exchange_code   text,
    currency_code   text,
    country_code    text,
    is_active       boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT instruments_market_ticker_key UNIQUE (market_code, ticker)
);

-- 1-3. 뉴스 / 출처 표시용
CREATE TABLE news (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    title             text        NOT NULL,
    publisher_name    text        NOT NULL,
    publisher_domain  text,
    article_url       text,
    canonical_url     text,
    provider_summary  text,
    content_language  text,
    published_at      timestamptz NOT NULL,
    fetched_at        timestamptz,
    provider          text,
    provider_news_id  text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- 1-2. 분석 리포트 루트
CREATE TABLE analysis_reports (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    instrument_id     uuid        NOT NULL REFERENCES instruments (id),
    report_date       date        NOT NULL,
    data_as_of_at     timestamptz NOT NULL,
    generated_at      timestamptz NOT NULL DEFAULT now(),
    language          text        NOT NULL DEFAULT 'ko',
    status            text        NOT NULL DEFAULT 'completed',
    model_version     text,
    prompt_version    text,
    pipeline_version  text,
    input_snapshot_uri text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    -- DBML: MVP에서는 completed/partial만 사용(실패 상세는 작업 로그). 값 확장은 후속 마이그레이션으로.
    CONSTRAINT analysis_reports_status_chk CHECK (status IN ('completed', 'partial'))
);

-- 1-4. Daily Pulse
CREATE TABLE daily_pulses (
    id                      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id               uuid        NOT NULL UNIQUE REFERENCES analysis_reports (id) ON DELETE CASCADE,
    pulse_direction         text        NOT NULL,
    pulse_strength          smallint    NOT NULL,
    headline                text        NOT NULL,
    subheadline             text,
    conclusion_title        text,
    conclusion_body         text,
    direction_support_ratio smallint,
    latest_news_id          uuid        REFERENCES news (id) ON DELETE SET NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT daily_pulses_direction_chk CHECK (pulse_direction IN ('positive', 'negative', 'neutral')),
    CONSTRAINT daily_pulses_strength_chk CHECK (pulse_strength BETWEEN 1 AND 5),
    CONSTRAINT daily_pulses_support_ratio_chk CHECK (direction_support_ratio BETWEEN 0 AND 100)
);

CREATE TABLE pulse_factors (
    id            uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    pulse_id      uuid     NOT NULL REFERENCES daily_pulses (id) ON DELETE CASCADE,
    factor_side   text     NOT NULL,
    label         text     NOT NULL,
    display_order smallint NOT NULL,
    CONSTRAINT pulse_factors_side_chk CHECK (factor_side IN ('support', 'oppose')),
    CONSTRAINT pulse_factors_order_key UNIQUE (pulse_id, factor_side, display_order)
);

CREATE TABLE pulse_claims (
    id            uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    pulse_id      uuid     NOT NULL REFERENCES daily_pulses (id) ON DELETE CASCADE,
    claim_type    text     NOT NULL,
    title         text     NOT NULL,
    subtitle      text,
    display_order smallint NOT NULL DEFAULT 1,
    CONSTRAINT pulse_claims_type_chk CHECK (claim_type IN ('support_case', 'risk_case')),
    CONSTRAINT pulse_claims_order_key UNIQUE (pulse_id, claim_type, display_order)
);

CREATE TABLE claim_steps (
    id               uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id         uuid     NOT NULL REFERENCES pulse_claims (id) ON DELETE CASCADE,
    statement        text     NOT NULL,
    evidence_summary text,
    display_order    smallint NOT NULL,
    CONSTRAINT claim_steps_order_key UNIQUE (claim_id, display_order)
);

CREATE TABLE claim_news_links (
    id            uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id      uuid     NOT NULL REFERENCES pulse_claims (id) ON DELETE CASCADE,
    news_id       uuid     NOT NULL REFERENCES news (id) ON DELETE RESTRICT,
    display_order smallint NOT NULL DEFAULT 1,
    CONSTRAINT claim_news_links_order_key UNIQUE (claim_id, display_order),
    CONSTRAINT claim_news_links_claim_news_key UNIQUE (claim_id, news_id)
);

-- 1-5. Investment Thesis
CREATE TABLE investment_theses (
    id                     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id              uuid        NOT NULL UNIQUE REFERENCES analysis_reports (id) ON DELETE CASCADE,
    consensus              text,
    investment_edge        text,
    catalyst               text,
    investment_horizon     text,
    thesis_direction       text,
    invalidation_condition text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT investment_theses_direction_chk CHECK (thesis_direction IN ('positive', 'negative', 'neutral'))
);

CREATE TABLE thesis_dialectic_steps (
    id               uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    thesis_id        uuid     NOT NULL REFERENCES investment_theses (id) ON DELETE CASCADE,
    display_order    smallint NOT NULL,
    claim            text     NOT NULL,
    counterargument  text,
    signal           text,
    evidence_summary text,
    CONSTRAINT thesis_dialectic_steps_order_key UNIQUE (thesis_id, display_order)
);

CREATE TABLE thesis_dialectic_news_links (
    id                uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    dialectic_step_id uuid     NOT NULL REFERENCES thesis_dialectic_steps (id) ON DELETE CASCADE,
    news_id           uuid     NOT NULL REFERENCES news (id) ON DELETE RESTRICT,
    display_order     smallint NOT NULL DEFAULT 1,
    CONSTRAINT thesis_dialectic_news_links_order_key UNIQUE (dialectic_step_id, display_order),
    CONSTRAINT thesis_dialectic_news_links_step_news_key UNIQUE (dialectic_step_id, news_id)
);

CREATE TABLE thesis_decision_points (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    thesis_id   uuid        NOT NULL UNIQUE REFERENCES investment_theses (id) ON DELETE CASCADE,
    title       text        NOT NULL,
    summary     text,
    watch_event text,
    event_date  date,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE thesis_scenarios (
    id                       uuid     PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_point_id        uuid     NOT NULL REFERENCES thesis_decision_points (id) ON DELETE CASCADE,
    scenario_label           text     NOT NULL,
    scenario_case            text     NOT NULL,
    condition_summary        text     NOT NULL,
    expected_result          text     NOT NULL,
    probability              smallint,
    rationale                text,
    historical_hit_label     text,
    historical_result_label  text,
    hit_ratio                numeric,
    display_order            smallint NOT NULL DEFAULT 1,
    CONSTRAINT thesis_scenarios_case_chk CHECK (scenario_case IN ('upside', 'base', 'downside')),
    CONSTRAINT thesis_scenarios_probability_chk CHECK (probability BETWEEN 0 AND 100),
    CONSTRAINT thesis_scenarios_hit_ratio_chk CHECK (hit_ratio BETWEEN 0 AND 1),
    CONSTRAINT thesis_scenarios_order_key UNIQUE (decision_point_id, display_order)
);

-- 1-6. Issue Map
CREATE TABLE issue_map_events (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id        uuid        NOT NULL REFERENCES analysis_reports (id) ON DELETE CASCADE,
    title            text        NOT NULL,
    impact_direction text        NOT NULL,
    impact_score     numeric     NOT NULL,
    event_date       date,
    description      text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT issue_map_events_direction_chk CHECK (impact_direction IN ('positive', 'negative', 'neutral')),
    CONSTRAINT issue_map_events_score_chk CHECK (impact_score BETWEEN 0 AND 100),
    -- 서로 다른 report의 event가 edge로 연결되지 않도록 보장하는 composite FK 타깃
    CONSTRAINT issue_map_events_report_id_key UNIQUE (report_id, id)
);

CREATE TABLE issue_map_edges (
    id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id        uuid        NOT NULL,
    from_event_id    uuid        NOT NULL,
    to_event_id      uuid        NOT NULL,
    effect_direction text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT issue_map_edges_effect_chk CHECK (effect_direction IN ('positive', 'negative')),
    CONSTRAINT issue_map_edges_unique_key UNIQUE (report_id, from_event_id, to_event_id),
    CONSTRAINT issue_map_edges_from_fk FOREIGN KEY (report_id, from_event_id)
        REFERENCES issue_map_events (report_id, id) ON DELETE CASCADE,
    CONSTRAINT issue_map_edges_to_fk FOREIGN KEY (report_id, to_event_id)
        REFERENCES issue_map_events (report_id, id) ON DELETE CASCADE
);


-- ============================================================
-- 2. SaaS 멀티테넌시 도메인
-- ============================================================

CREATE TABLE organizations (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text        NOT NULL,
    status     text        NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT organizations_status_chk CHECK (status IN ('active', 'suspended'))
);

CREATE TABLE super_admins (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id text        NOT NULL UNIQUE,
    email       text        NOT NULL,
    name        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    -- 이메일 유니크는 members·invitations와 동일하게 대소문자 무시(lower) — 아래 인덱스로 정의
    CONSTRAINT super_admins_email_len_chk CHECK (char_length(email) <= 320)
);

CREATE TABLE roles (
    id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    role_type  text        NOT NULL UNIQUE,
    name       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE members (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid        NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    role_id         uuid        NOT NULL REFERENCES roles (id),
    external_id     text        NOT NULL UNIQUE,
    email           text        NOT NULL,
    name            text,
    status          text        NOT NULL DEFAULT 'active',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT members_status_chk CHECK (status IN ('active', 'disabled')),
    CONSTRAINT members_email_len_chk CHECK (char_length(email) <= 320),
    -- 테넌트 격리: invitations가 (organization_id, invited_by_member_id) composite FK로 참조
    CONSTRAINT members_org_id_key UNIQUE (organization_id, id)
    -- 조직 내 이메일 유니크는 invitations와 동일하게 대소문자 무시(lower) — 아래 인덱스로 정의
);

CREATE TABLE invitations (
    id                        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id           uuid        NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    role_id                   uuid        NOT NULL REFERENCES roles (id),
    email                     text        NOT NULL,
    token_hash                text        NOT NULL UNIQUE,
    status                    text        NOT NULL DEFAULT 'pending',
    expires_at                timestamptz NOT NULL,
    accepted_at               timestamptz,
    invited_by_super_admin_id uuid        REFERENCES super_admins (id),
    invited_by_member_id      uuid,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT invitations_status_chk CHECK (status IN ('pending', 'accepted', 'expired', 'revoked')),
    CONSTRAINT invitations_email_len_chk CHECK (char_length(email) <= 320),
    CONSTRAINT invitations_invited_by_chk CHECK (num_nonnulls(invited_by_super_admin_id, invited_by_member_id) = 1),
    -- 테넌트 격리: 초대한 member는 반드시 초대 대상 organization 소속이어야 한다.
    -- (invited_by_member_id가 NULL인 super-admin 초대 경로는 MATCH SIMPLE로 검사되지 않아 그대로 허용)
    CONSTRAINT invitations_invited_by_member_fk FOREIGN KEY (organization_id, invited_by_member_id)
        REFERENCES members (organization_id, id)
);

CREATE TABLE compliance_settings (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id   uuid        NOT NULL UNIQUE REFERENCES organizations (id) ON DELETE CASCADE,
    disclaimer_text   text,
    keyword_blacklist text[]      NOT NULL DEFAULT '{}'::text[],
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE applications (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid        NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    name            text        NOT NULL,
    status          text        NOT NULL DEFAULT 'active',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT applications_status_chk CHECK (status IN ('active', 'archived')),
    CONSTRAINT applications_org_name_key UNIQUE (organization_id, name),
    -- 테넌트 격리: widgets·target_symbols가 (organization_id, application_id) composite FK로 참조
    CONSTRAINT applications_org_id_key UNIQUE (organization_id, id)
);

CREATE TABLE widgets (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid        NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    application_id  uuid        NOT NULL,
    key_hash        text        NOT NULL UNIQUE,
    name            text        NOT NULL,
    design          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status          text        NOT NULL DEFAULT 'active',
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT widgets_status_chk CHECK (status IN ('active', 'archived')),
    -- 테넌트 격리: application은 반드시 같은 organization 소유여야 한다 (cross-tenant 참조 차단)
    CONSTRAINT widgets_application_fk FOREIGN KEY (organization_id, application_id)
        REFERENCES applications (organization_id, id) ON DELETE CASCADE
);

CREATE TABLE target_symbols (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid        NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    application_id  uuid        NOT NULL,
    instrument_id   uuid        NOT NULL REFERENCES instruments (id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT target_symbols_app_instrument_key UNIQUE (application_id, instrument_id),
    -- 테넌트 격리: application은 반드시 같은 organization 소유여야 한다 (cross-tenant 참조 차단)
    CONSTRAINT target_symbols_application_fk FOREIGN KEY (organization_id, application_id)
        REFERENCES applications (organization_id, id) ON DELETE CASCADE
);


-- ============================================================
-- 3. 인덱스 (DBML Indexes 중 unique 제약/PK로 이미 커버되지 않은 것)
-- ============================================================

-- instruments
CREATE INDEX instruments_type_idx ON instruments (instrument_type);
CREATE INDEX instruments_active_idx ON instruments (is_active);

-- analysis_reports
CREATE INDEX analysis_reports_lookup_idx ON analysis_reports (instrument_id, language, data_as_of_at, generated_at);
CREATE INDEX analysis_reports_date_idx ON analysis_reports (instrument_id, language, report_date);
CREATE INDEX analysis_reports_status_idx ON analysis_reports (status);

-- news
CREATE INDEX news_canonical_url_idx ON news (canonical_url);
CREATE INDEX news_provider_idx ON news (provider, provider_news_id);
CREATE INDEX news_published_at_idx ON news (published_at);
CREATE INDEX news_publisher_published_idx ON news (publisher_name, published_at);

-- claim_news_links / thesis_dialectic_news_links (news_id 역방향 조회)
CREATE INDEX claim_news_links_news_idx ON claim_news_links (news_id);
CREATE INDEX thesis_dialectic_news_links_news_idx ON thesis_dialectic_news_links (news_id);

-- thesis_decision_points
CREATE INDEX thesis_decision_points_event_date_idx ON thesis_decision_points (event_date);

-- thesis_scenarios
CREATE INDEX thesis_scenarios_case_idx ON thesis_scenarios (scenario_case);

-- issue_map_events  ((report_id, id)는 unique 제약으로 커버됨)
CREATE INDEX issue_map_events_report_date_idx ON issue_map_events (report_id, event_date);
CREATE INDEX issue_map_events_direction_idx ON issue_map_events (impact_direction);

-- issue_map_edges
CREATE INDEX issue_map_edges_from_idx ON issue_map_edges (from_event_id);
CREATE INDEX issue_map_edges_to_idx ON issue_map_edges (to_event_id);

-- super_admins (이메일 중복 방지 — members·invitations와 동일하게 대소문자 무시)
CREATE UNIQUE INDEX super_admins_email_uq ON super_admins (lower(email));

-- members (조직 내 이메일 중복 방지 — invitations와 동일하게 대소문자 무시)
CREATE UNIQUE INDEX members_org_email_uq ON members (organization_id, lower(email));

-- invitations
CREATE INDEX invitations_org_email_status_idx ON invitations (organization_id, email, status);
-- pending 초대 중복 방지 (대소문자 무시)
CREATE UNIQUE INDEX invitations_pending_email_uq ON invitations (organization_id, lower(email)) WHERE status = 'pending';

-- widgets
CREATE INDEX widgets_org_app_idx ON widgets (organization_id, application_id);
CREATE INDEX widgets_status_idx ON widgets (status);

-- target_symbols
CREATE INDEX target_symbols_org_app_idx ON target_symbols (organization_id, application_id);


-- ============================================================
-- 4. updated_at BEFORE UPDATE 트리거 (updated_at 컬럼 보유 테이블)
-- ============================================================

CREATE TRIGGER instruments_set_updated_at         BEFORE UPDATE ON instruments         FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER analysis_reports_set_updated_at    BEFORE UPDATE ON analysis_reports    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER organizations_set_updated_at       BEFORE UPDATE ON organizations       FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER super_admins_set_updated_at        BEFORE UPDATE ON super_admins        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER roles_set_updated_at               BEFORE UPDATE ON roles               FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER members_set_updated_at             BEFORE UPDATE ON members             FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER invitations_set_updated_at         BEFORE UPDATE ON invitations         FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER compliance_settings_set_updated_at BEFORE UPDATE ON compliance_settings FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER applications_set_updated_at        BEFORE UPDATE ON applications        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER widgets_set_updated_at             BEFORE UPDATE ON widgets             FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER target_symbols_set_updated_at      BEFORE UPDATE ON target_symbols      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
