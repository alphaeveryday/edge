-- 온톨로지 객체 뷰(ontology_view)를 Flyway 로 관리한다 (ALPHA-1448).
--
-- 그래프 DB(PuppyGraph, infra/terraform/modules/ontology-graph)가 읽는 뷰 30개는 2026-10-05 파일럿 때
-- 손으로 적용돼 Flyway 밖에 있었다. 그래서 테이블을 바꾸는 마이그레이션이 뷰를 모른 채 돌았고(뷰가 깨지거나
-- 뷰 의존성 때문에 ALTER 가 막힌다), 저장소만으로 DB 를 재현할 수 없었다.
--
-- 본문은 libs/ontology/src/edge_ontology/sql/views/*.sql 을 의존 순서대로 그대로 옮긴 것이다. 2026-10-24 dev 의
-- 뷰 30개와 pg_get_viewdef 기준으로 정의가 같음을 확인했다 — 이미 있는 dev 에서는 같은 정의로 다시
-- 만들 뿐이고(소유자 edge = Flyway 접속 주체, 기존 권한은 CREATE OR REPLACE 가 보존), 새 DB 에서는 새로 만든다.
-- 뷰를 바꿀 때는 views/*.sql 과 새 마이그레이션의 CREATE OR REPLACE 를 같은 PR 에서 함께 바꾼다
-- (libs/ontology/tests/test_ontology_view_migrations.py 가 어긋나면 실패한다).
--
-- 뷰에만 ACCESS EXCLUSIVE 를 잡는다(원본 테이블은 아니다). 장중에 그래프가 뷰를 읽고 있으면 기다리지 않고 실패한다.
SET LOCAL lock_timeout = '3s';

CREATE SCHEMA IF NOT EXISTS ontology_view;

-- views/actor_participation.sql
CREATE OR REPLACE VIEW ontology_view.actor_participation AS
SELECT argument.event_argument_id::text AS participation_id,
       argument.entity_id::text AS actor_id,
       argument.source_event_id::text AS source_event_id,
       CASE WHEN actor.actor_type = 'COMPANY' AND EXISTS
                 (SELECT 1 FROM public.company_profile company WHERE company.actor_id=argument.entity_id)
            THEN 'COMPANY'
            WHEN actor.actor_type IN ('GOVERNMENT', 'INSTITUTION') THEN actor.actor_type
       END::text AS actor_type,
       argument.role_code::text AS role_code,
       argument.mention_text::text AS mentioned_name,
       argument.group_ord::smallint AS argument_group
FROM public.event_argument AS argument
LEFT JOIN public.actor AS actor ON actor.actor_id = argument.entity_id;

-- views/cash_distribution.sql
CREATE OR REPLACE VIEW ontology_view.cash_distribution AS
SELECT json_build_array(d.market_code, d.ticker, d.source_vendor, d.source_event_key)::text AS id,
       i.instrument_id,
       ep.instrument_id AS etf_instrument_id,
       eq.instrument_id AS equity_instrument_id,
       d.ticker, d.market_code, d.source_vendor, d.source_event_key,
       d.record_date, d.ex_date, d.pay_date, d.amount_per_share,
       d.amount_per_share::text AS amount_decimal_text,
       d.currency, d.distribution_kind, d.confirmation, d.available_at,
       d.raw_key, d.raw_sha256, d.raw_run_id
FROM public.cash_distribution_latest d
LEFT JOIN public.instrument i ON i.market_code = d.market_code AND i.ticker = d.ticker
LEFT JOIN public.etf_profile ep ON ep.instrument_id = i.instrument_id AND i.instrument_type = 'ETF'
LEFT JOIN public.equity_profile eq ON eq.instrument_id = i.instrument_id AND i.instrument_type = 'EQUITY';

-- views/company.sql
CREATE OR REPLACE VIEW ontology_view.company AS
SELECT base.actor_id::text AS id,
       entity.display_name::text AS name,
       actor.country_code::text AS country_code,
       base.dart_corp_code::text AS dart_corp_code,
       base.profile_as_of_date::date AS profile_as_of_date
FROM public.company_profile AS base
LEFT JOIN public.actor AS actor
  ON base.actor_id = actor.actor_id AND base.actor_type = actor.actor_type
LEFT JOIN public.entity AS entity
  ON actor.actor_id = entity.entity_id AND actor.entity_type = entity.entity_type;

-- views/concept.sql
CREATE OR REPLACE VIEW ontology_view.concept AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (parentConcept_e.display_name)::text AS parent_concept_name
FROM (
SELECT (base.concept_id::text)::text AS id,
       (entity.display_name)::text AS name,
       (base.concept_type)::text AS concept_type,
       (base.parent_concept_id)::text AS parent_concept_id
FROM public.concept AS base
LEFT JOIN public.entity AS entity ON base.concept_id = entity.entity_id AND base.entity_type = entity.entity_type
) AS record
LEFT JOIN public.entity parentConcept_e ON parentConcept_e.entity_id=record.parent_concept_id;

-- views/daily_bar.sql
CREATE OR REPLACE VIEW ontology_view.daily_bar AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(security_e.display_name,''), record.instrument_id, 'Unknown security') || COALESCE(' (' || security_i.ticker || ' / ' || security_i.market_code || ')',''), 'Daily prices', record.trade_date::text))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.open_price::text AS open_price_decimal_text,
       mapped.high_price::text AS high_price_decimal_text,
       mapped.low_price::text AS low_price_decimal_text,
       mapped.close_price::text AS close_price_decimal_text,
       mapped.adjusted_close_price::text AS adjusted_close_price_decimal_text,
       mapped.turnover_value::text AS turnover_value_decimal_text
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.instrument_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.instrument_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.instrument_id::text)::text || ',' || to_json(base.trade_date::text)::text || ']')::text AS id,
       (base.instrument_id)::text AS instrument_id,
       (base.trade_date)::date AS trade_date,
       (NULL)::numeric AS open_price,
       (NULL)::numeric AS high_price,
       (NULL)::numeric AS low_price,
       (base.close_price)::numeric AS close_price,
       (base.adjusted_close_price)::numeric AS adjusted_close_price,
       (base.simple_return)::double precision AS simple_return,
       (base.log_return)::double precision AS log_return,
       (base.volume)::bigint AS volume,
       (base.turnover_value)::numeric AS turnover_value,
       (base.price_basis)::text AS price_basis,
       (base.available_at)::timestamptz AS available_at
FROM public.price_daily AS base
) AS mapped
) AS mapped
) AS record
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.instrument_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/daily_investor_flow.sql
CREATE OR REPLACE VIEW ontology_view.daily_investor_flow AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(security_e.display_name,''), record.instrument_id, 'Unknown security') || COALESCE(' (' || security_i.ticker || ' / ' || security_i.market_code || ')',''), 'Daily investor flow', record.trade_date::text))::text AS display_title
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.instrument_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.instrument_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.instrument_id::text)::text || ',' || to_json(base.trade_date::text)::text || ']')::text AS id,
       (base.instrument_id)::text AS instrument_id,
       (base.trade_date)::date AS trade_date,
       (base.net_qty_individual)::bigint AS net_qty_individual,
       (base.net_val_individual)::bigint AS net_val_individual,
       (base.net_qty_foreign)::bigint AS net_qty_foreign,
       (base.net_val_foreign)::bigint AS net_val_foreign,
       (base.net_qty_institution_total)::bigint AS net_qty_institution_total,
       (base.net_val_institution_total)::bigint AS net_val_institution_total,
       (base.available_at)::timestamptz AS available_at
FROM public.investor_flow_daily AS base
) AS mapped
) AS record
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.instrument_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/daily_nav.sql
CREATE OR REPLACE VIEW ontology_view.daily_nav AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (etf_e.display_name)::text AS etf_name,
       (etf_i.ticker)::text AS etf_ticker,
       (etf_i.market_code)::text AS etf_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(etf_e.display_name,''), record.etf_instrument_id, 'Unknown security') || COALESCE(' (' || etf_i.ticker || ' / ' || etf_i.market_code || ')',''), 'NAV', record.trade_date::text))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.nav::text AS nav_decimal_text
FROM (
SELECT ('[' || to_json(base.etf_instrument_id::text)::text || ',' || to_json(base.trade_date::text)::text || ']')::text AS id,
       (base.etf_instrument_id)::text AS etf_instrument_id,
       (base.trade_date)::date AS trade_date,
       (base.nav)::numeric AS nav,
       (base.available_at)::timestamptz AS available_at
FROM public.etf_nav_daily AS base
) AS mapped
) AS record
LEFT JOIN public.instrument etf_i ON etf_i.instrument_id=record.etf_instrument_id
LEFT JOIN public.entity etf_e ON etf_e.entity_id=etf_i.instrument_id AND etf_e.entity_type=etf_i.entity_type;

-- views/disclosure.sql
CREATE OR REPLACE VIEW ontology_view.disclosure AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (issuer_e.display_name)::text AS issuer_name,
       (COALESCE(NULLIF(record.title,''), concat_ws(' / ', COALESCE(NULLIF(issuer_e.display_name,''), record.issuer_id, 'Unknown issuer'),record.disclosure_type,record.report_date::text,record.source_document_id)))::text AS display_title
FROM (
SELECT (base.document_id::text)::text AS id,
       (base.issuer_actor_id)::text AS issuer_id,
       (base.disclosure_type)::text AS disclosure_type,
       (base.report_date)::date AS report_date,
       (document.title)::text AS title,
       (document.published_at)::timestamptz AS published_at,
       (document.available_at)::timestamptz AS available_at,
       (document.source_document_id)::text AS source_document_id,
       (document.source_uri)::text AS source_uri
FROM public.disclosure_document AS base
LEFT JOIN public.document AS document ON base.document_id = document.document_id AND base.document_type = document.document_type
) AS record
LEFT JOIN public.entity issuer_e ON issuer_e.entity_id=record.issuer_id;

-- views/document_event_evidence.sql
CREATE OR REPLACE VIEW ontology_view.document_event_evidence AS
SELECT evidence.evidence_id::text AS evidence_id,
       assertion.document_id::text AS document_id,
       evidence.source_event_id::text AS source_event_id,
       CASE WHEN document.document_type = 'NEWS' AND EXISTS
                 (SELECT 1 FROM public.news_document news WHERE news.document_id=assertion.document_id)
            THEN 'NEWS'
            WHEN document.document_type = 'DISCLOSURE' AND EXISTS
                 (SELECT 1 FROM public.disclosure_document disclosure WHERE disclosure.document_id=assertion.document_id)
            THEN 'DISCLOSURE'
       END::text AS document_type,
       assertion.confidence::double precision AS extraction_confidence,
       evidence.assertion_id::text AS assertion_id,
       evidence.evidence_type::text AS evidence_type,
       evidence.evidence_text::text AS evidence_text
FROM public.event_evidence AS evidence
JOIN public.document_assertion AS assertion ON assertion.assertion_id = evidence.assertion_id
JOIN public.document AS document ON document.document_id = assertion.document_id;

-- views/document_security_mention.sql
CREATE OR REPLACE VIEW ontology_view.document_security_mention AS
SELECT match.document_id::text AS document_id,
       match.entity_id::text AS entity_id,
       match.link_method::text AS link_method,
       match.matched_text::text AS matched_text
FROM public.document_entity AS match
JOIN public.news_document AS news ON news.document_id = match.document_id
JOIN public.equity_profile AS equity ON equity.instrument_id = match.entity_id
WHERE match.link_method = 'mention';

-- views/equity.sql
CREATE OR REPLACE VIEW ontology_view.equity AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (issuer_e.display_name)::text AS issuer_name
FROM (
SELECT base.instrument_id::text AS id,
       entity.display_name::text AS name,
       base.issuer_actor_id::text AS issuer_id,
       base.share_class_code::text AS share_class,
       instrument.ticker::text AS ticker,
       instrument.market_code::text AS market_code,
       instrument.currency_code::text AS currency_code,
       base.profile_as_of_date::date AS profile_as_of_date
FROM public.equity_profile AS base
LEFT JOIN public.instrument AS instrument
  ON base.instrument_id = instrument.instrument_id AND base.instrument_type = instrument.instrument_type
LEFT JOIN public.entity AS entity
  ON instrument.instrument_id = entity.entity_id AND instrument.entity_type = entity.entity_type
) AS record
LEFT JOIN public.entity issuer_e ON issuer_e.entity_id=record.issuer_id;

-- views/etf.sql
CREATE OR REPLACE VIEW ontology_view.etf AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (benchmark.series_name)::text AS benchmark_name,
       (theme_e.display_name)::text AS theme_name
FROM (
SELECT (base.instrument_id::text)::text AS id,
       (entity.display_name)::text AS name,
       (instrument.ticker)::text AS ticker,
       (instrument.market_code)::text AS market_code,
       (instrument.currency_code)::text AS currency_code,
       (base.etf_type)::text AS etf_type,
       (base.tracking_market_series_id)::text AS tracking_market_series_id,
       (base.primary_theme_concept_id)::text AS primary_theme_concept_id,
       (base.asset_manager_name)::text AS asset_manager_name,
       (base.leverage_multiplier)::double precision AS leverage_multiplier,
       (base.currency_hedged)::boolean AS currency_hedged,
       (base.total_expense_ratio)::double precision AS total_expense_ratio,
       (base.profile_as_of_date)::date AS profile_as_of_date,
       (SELECT series.market_series_id FROM public.market_series series WHERE series.market_series_id=base.tracking_market_series_id AND series.series_type='INDEX')::text AS benchmark_index_id
FROM public.etf_profile AS base
LEFT JOIN public.instrument AS instrument ON base.instrument_id = instrument.instrument_id AND base.instrument_type = instrument.instrument_type
LEFT JOIN public.entity AS entity ON instrument.instrument_id = entity.entity_id AND instrument.entity_type = entity.entity_type
) AS record
LEFT JOIN public.market_series benchmark ON benchmark.market_series_id=record.benchmark_index_id
LEFT JOIN public.entity theme_e ON theme_e.entity_id=record.primary_theme_concept_id;

-- views/etf_holding.sql
CREATE OR REPLACE VIEW ontology_view.etf_holding AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (etf_e.display_name)::text AS etf_name,
       (etf_i.ticker)::text AS etf_ticker,
       (etf_i.market_code)::text AS etf_market_code,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(etf_e.display_name,''), record.etf_instrument_id, 'Unknown security') || COALESCE(' (' || etf_i.ticker || ' / ' || etf_i.market_code || ')',''), COALESCE(NULLIF(security_e.display_name,''), record.constituent_instrument_id, 'Unknown security') || COALESCE(' (' || security_i.ticker || ' / ' || security_i.market_code || ')',''), record.trade_date::text))::text AS display_title
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.constituent_instrument_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.constituent_instrument_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.etf_instrument_id::text)::text || ',' || to_json(base.constituent_instrument_id::text)::text || ',' || to_json(base.trade_date::text)::text || ']')::text AS id,
       (base.etf_instrument_id)::text AS etf_instrument_id,
       (base.constituent_instrument_id)::text AS constituent_instrument_id,
       (base.trade_date)::date AS trade_date,
       (base.weight_ratio)::double precision AS weight_ratio,
       (base.available_at)::timestamptz AS available_at
FROM public.etf_holding_snapshot AS base
) AS mapped
) AS record
LEFT JOIN public.instrument etf_i ON etf_i.instrument_id=record.etf_instrument_id
LEFT JOIN public.entity etf_e ON etf_e.entity_id=etf_i.instrument_id AND etf_e.entity_type=etf_i.entity_type
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.constituent_instrument_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/etf_outlook_report.sql
CREATE OR REPLACE VIEW ontology_view.etf_outlook_report AS
SELECT a.analysis_id::text AS id,e.id::text AS etf_instrument_id,
       a.analysis_at::timestamptz AS analysis_at,a.published_at::timestamptz AS available_at,
       a.summary::text AS summary,a.previous_analysis_id::text AS previous_report_id,
       concat_ws(' / ',e.name,'outlook',a.analysis_at::text)::text AS display_title,
       ARRAY(SELECT t.tool_run_id::text FROM public.tool_runs t WHERE t.outlook_analysis_id=a.analysis_id AND t.status='completed' ORDER BY t.tool_run_id)::text[] AS evidence_run_ids
FROM public.outlook_analyses a
LEFT JOIN ontology_view.etf e ON e.ticker=a.etf_code AND e.market_code='XKRX'
WHERE a.status='completed' AND a.data_source='database' AND a.published_at IS NOT NULL;

-- views/etf_price_explanation.sql
CREATE OR REPLACE VIEW ontology_view.etf_price_explanation AS
SELECT a.analysis_id::text AS id,e.id::text AS etf_instrument_id,
       a.analysis_at::timestamptz AS analysis_at,a.published_at::timestamptz AS available_at,
       a.summary::text AS summary,a.previous_analysis_id::text AS previous_report_id,
       concat_ws(' / ',e.name,'movement',a.analysis_at::text)::text AS display_title,
       ARRAY(SELECT t.tool_run_id::text FROM public.tool_runs t WHERE t.movement_analysis_id=a.analysis_id AND t.status='completed' ORDER BY t.tool_run_id)::text[] AS evidence_run_ids
FROM public.movement_analyses a
LEFT JOIN ontology_view.etf e ON e.ticker=a.etf_code AND e.market_code='XKRX'
WHERE a.status='completed' AND a.data_source='database' AND a.published_at IS NOT NULL AND a.withdrawn_at IS NULL;

-- views/event_measurement.sql
CREATE OR REPLACE VIEW ontology_view.event_measurement AS
SELECT jsonb_build_array(m.source_event_id::text, m.measure_ord::text)::text AS id,
       m.source_event_id::text AS source_event_id,
       m.measure_ord::smallint AS measurement_ordinal,
       m.role_code::text AS metric_code,
       m.surface::text AS reported_text,
       m.value::numeric AS value,
       m.unit::text AS unit,
       m.basis::text AS period_basis,
       m.value_source::text AS value_source,
       m.parse_flag::text AS parse_status,
       m.group_ord::smallint AS argument_group,
       m.dart_rcept_no::text AS receipt_number,
       e.available_at::timestamptz AS available_at,
       concat_ws(' / ',m.role_code,m.surface,e.event_date::text)::text AS display_title,
       m.value::text AS value_decimal
FROM public.event_measure m
JOIN public.source_event e ON e.source_event_id=m.source_event_id;

-- views/event_thread.sql
CREATE OR REPLACE VIEW ontology_view.event_thread AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (context.summary)::text AS argument_summary,
       (concat_ws(' / ', record.event_type, COALESCE(context.summary,'No named arguments'), to_char(record.opened_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') || ' UTC'))::text AS display_title
FROM (
SELECT (base.thread_id::text)::text AS id,
       (base.event_type_code)::text AS event_type,
       (NULL)::text AS current_stage,
       (base.opened_at)::timestamptz AS opened_at,
       (NULL)::timestamptz AS last_state_at
FROM public.event_thread AS base
) AS record
LEFT JOIN LATERAL (SELECT string_agg(COALESCE(NULLIF(e.display_name,''), NULLIF(a.mention_text,''), a.entity_id, 'Unresolved entity') || ' [' || a.role_code || ']', '; ' ORDER BY a.entity_id, a.role_code, a.mention_text) AS summary FROM (SELECT DISTINCT a.entity_id, a.role_code, a.mention_text FROM public.event_thread_link l JOIN public.event_argument a ON a.source_event_id=l.source_event_id WHERE l.thread_id=record.id) a LEFT JOIN public.entity e ON e.entity_id=a.entity_id) context ON true;

-- views/exchange_security_classification.sql
CREATE OR REPLACE VIEW ontology_view.exchange_security_classification AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(security_e.display_name,''), NULLIF(record.name_kr,''), record.instrument_code, 'Unknown security'), 'Exchange classification', record.collected_on::text, record.market, record.taxonomy))::text AS display_title
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.security_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.security_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.market::text)::text || ',' || to_json(base.instrument_code::text)::text || ',' || to_json(base.as_of_date::text)::text || ',' || to_json(base.raw_run_id::text)::text || ']')::text AS id,
       (base.market)::text AS market,
       (base.instrument_code)::text AS instrument_code,
       (base.standard_code)::text AS standard_code,
       (base.name_kr)::text AS name_kr,
       (base.security_group)::text AS security_group,
       (base.as_of_date)::date AS collected_on,
       (base.large_code)::text AS large_code,
       (base.large_name)::text AS large_name,
       (base.medium_code)::text AS medium_code,
       (base.medium_name)::text AS medium_name,
       (base.small_code)::text AS small_code,
       (base.small_name)::text AS small_name,
       (base.taxonomy)::text AS taxonomy,
       (base.available_at)::timestamptz AS available_at,
       (SELECT i.instrument_id FROM public.instrument i WHERE i.ticker=base.instrument_code AND i.market_code=CASE base.market WHEN 'KOSPI' THEN 'XKRX' WHEN 'KOSDAQ' THEN 'XKOS' END AND i.instrument_type IN ('EQUITY','ETF'))::text AS security_id
FROM public.sector_classification AS base
) AS mapped
) AS record
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.security_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/financial_metric.sql
CREATE OR REPLACE VIEW ontology_view.financial_metric AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (company_e.display_name)::text AS company_name,
       (concat_ws(' / ', COALESCE(NULLIF(company_e.display_name,''), record.company_id, 'Unknown company'), record.fiscal_year::text, record.fs_basis, record.fiscal_period, record.period_kind, record.metric, record.derivation, to_char(record.received_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US') || ' UTC'))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.value::text AS value_decimal_text
FROM (
SELECT ('[' || to_json(base.corp_code::text)::text || ',' || to_json(base.fiscal_year::text)::text || ',' || to_json(base.fiscal_period::text)::text || ',' || to_json(base.metric::text)::text || ',' || to_json(base.period_kind::text)::text || ',' || to_json(base.fs_basis::text)::text || ',' || to_json(base.raw_run_id::text)::text || ']')::text AS id,
       (base.corp_code)::text AS corp_code,
       (base.instrument_code)::text AS instrument_code,
       (base.fiscal_year)::smallint AS fiscal_year,
       (base.fiscal_period)::text AS fiscal_period,
       (base.period_end)::date AS period_end,
       (base.metric)::text AS metric,
       (base.period_kind)::text AS period_kind,
       (base.fs_basis)::text AS fs_basis,
       (base.derivation)::text AS derivation,
       (base.value)::numeric AS value,
       (base.unit)::text AS unit,
       (base.formula)::text AS formula,
       (base.rcept_no)::text AS rcept_no,
       (base.available_at)::timestamptz AS available_at,
       (base.received_at)::timestamptz AS received_at,
       (base.availability_basis)::text AS availability_basis,
       (base.inputs)::text AS calculation_inputs,
       (SELECT c.actor_id FROM public.company_profile c WHERE c.dart_corp_code=base.corp_code)::text AS company_id,
       (SELECT '[' || to_json(r.corp_code::text)::text || ',' || to_json(r.fiscal_year::text)::text || ',' || to_json(r.reprt_code::text)::text || ',' || to_json(r.fs_basis::text)::text || ',' || to_json(r.raw_run_id::text)::text || ']' FROM public.financial_report_version r WHERE r.corp_code=base.corp_code AND r.fiscal_year=base.fiscal_year AND r.fs_basis=base.fs_basis AND r.raw_run_id=base.raw_run_id AND r.reprt_code=CASE base.fiscal_period WHEN 'Q1' THEN '11013' WHEN 'Q2' THEN '11012' WHEN 'Q3' THEN '11014' WHEN 'Q4' THEN '11011' WHEN 'FY' THEN '11011' END AND (base.rcept_no IS NULL OR r.rcept_no=base.rcept_no))::text AS reporting_context_id,
       (SELECT '[' || to_json(r.corp_code::text)::text || ',' || to_json(r.fiscal_year::text)::text || ',' || to_json(r.reprt_code::text)::text || ',' || to_json(r.fs_basis::text)::text || ',' || to_json(r.raw_run_id::text)::text || ']' FROM public.financial_report_version r WHERE r.corp_code=base.corp_code AND r.fiscal_year=base.fiscal_year AND r.fs_basis=base.fs_basis AND r.raw_run_id=base.raw_run_id AND r.reprt_code=CASE base.fiscal_period WHEN 'Q1' THEN '11013' WHEN 'Q2' THEN '11012' WHEN 'Q3' THEN '11014' WHEN 'Q4' THEN '11011' WHEN 'FY' THEN '11011' END AND base.derivation='REPORTED' AND r.rcept_no=base.rcept_no)::text AS reported_snapshot_id
FROM public.financial_metric AS base
) AS mapped
) AS record
LEFT JOIN public.entity company_e ON company_e.entity_id=record.company_id;

-- views/financial_report_snapshot.sql
CREATE OR REPLACE VIEW ontology_view.financial_report_snapshot AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (company_e.display_name)::text AS company_name,
       (concat_ws(' / ', COALESCE(NULLIF(company_e.display_name,''), record.company_id, 'Unknown company'), record.fiscal_year::text, record.fs_basis, record.report_coverage, record.receipt_number, to_char(record.received_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS.US') || ' UTC'))::text AS display_title
FROM (
SELECT ('[' || to_json(base.corp_code::text)::text || ',' || to_json(base.fiscal_year::text)::text || ',' || to_json(base.reprt_code::text)::text || ',' || to_json(base.fs_basis::text)::text || ',' || to_json(base.raw_run_id::text)::text || ']')::text AS id,
       (base.corp_code)::text AS corp_code,
       (base.instrument_code)::text AS instrument_code,
       (base.fiscal_year)::smallint AS fiscal_year,
       (base.reprt_code)::text AS report_type_code,
       (CASE base.report_period WHEN 'Q1' THEN 'Q1' WHEN 'Q2' THEN 'H1' WHEN 'Q3' THEN '9M' WHEN 'Q4' THEN 'FY' END)::text AS report_coverage,
       (base.fs_basis)::text AS fs_basis,
       (base.rcept_no)::text AS receipt_number,
       (base.rcept_date)::date AS receipt_date,
       (base.available_at)::timestamptz AS available_at,
       (base.received_at)::timestamptz AS received_at,
       (base.availability_basis)::text AS availability_basis,
       (SELECT c.actor_id FROM public.company_profile c WHERE c.dart_corp_code=base.corp_code)::text AS company_id
FROM public.financial_report_version AS base
) AS record
LEFT JOIN public.entity company_e ON company_e.entity_id=record.company_id;

-- views/intraday_investor_flow.sql
CREATE OR REPLACE VIEW ontology_view.intraday_investor_flow AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(security_e.display_name,''), record.instrument_id, 'Unknown security') || COALESCE(' (' || security_i.ticker || ' / ' || security_i.market_code || ')',''), 'Intraday investor flow', record.trade_date::text, record.reporting_slot_code))::text AS display_title
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.instrument_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.instrument_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.instrument_id::text)::text || ',' || to_json(base.trade_date::text)::text || ',' || to_json(base.asof_slot::text)::text || ']')::text AS id,
       (base.instrument_id)::text AS instrument_id,
       (base.trade_date)::date AS trade_date,
       (base.asof_slot)::text AS reporting_slot_code,
       (base.net_qty_foreign_est)::bigint AS foreign_net_purchase_quantity_estimate,
       (base.net_qty_institution_est)::bigint AS institutional_net_purchase_quantity_estimate,
       (base.net_qty_total_est)::bigint AS foreign_and_institutional_net_purchase_quantity_estimate,
       (base.available_at)::timestamptz AS available_at,
       (NULL)::timestamptz AS estimate_as_of
FROM public.investor_flow_intraday AS base
) AS mapped
) AS record
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.instrument_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/macro_observation.sql
CREATE OR REPLACE VIEW ontology_view.macro_observation AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (CASE record.series_id WHEN 'usd_krw' THEN 'USD/KRW exchange rate' WHEN 'us_10y_yield' THEN 'US 10-year Treasury yield' WHEN 'kr_10y_yield' THEN 'Korea 10-year government bond yield' WHEN 'kr_cpi_yoy' THEN 'Korea CPI year-on-year' WHEN 'brent_spot_usd' THEN 'Brent spot price' END)::text AS series_name,
       (concat_ws(' / ', COALESCE((CASE record.series_id WHEN 'usd_krw' THEN 'USD/KRW exchange rate' WHEN 'us_10y_yield' THEN 'US 10-year Treasury yield' WHEN 'kr_10y_yield' THEN 'Korea 10-year government bond yield' WHEN 'kr_cpi_yoy' THEN 'Korea CPI year-on-year' WHEN 'brent_spot_usd' THEN 'Brent spot price' END),record.series_id), record.observation_date::text, record.source_vendor, record.source_series))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.value::text AS value_decimal_text
FROM (
SELECT ('[' || to_json(base.series_id::text)::text || ',' || to_json(base.observation_date::text)::text || ',' || to_json(base.raw_run_id::text)::text || ']')::text AS id,
       (base.series_id)::text AS series_id,
       (base.observation_date)::date AS observation_date,
       (base.value)::numeric AS value,
       (base.unit)::text AS unit,
       (base.source_vendor)::text AS source_vendor,
       (base.source_series)::text AS source_series,
       (base.available_at)::timestamptz AS available_at,
       (CASE base.series_id WHEN 'usd_krw' THEN ARRAY['KR','US']::text[] WHEN 'us_10y_yield' THEN ARRAY['US']::text[] WHEN 'kr_10y_yield' THEN ARRAY['KR']::text[] WHEN 'kr_cpi_yoy' THEN ARRAY['KR']::text[] WHEN 'brent_spot_usd' THEN ARRAY['GLOBAL']::text[] END)::text[] AS regions,
       (CASE base.series_id WHEN 'usd_krw' THEN 'EXCHANGE_RATE' WHEN 'us_10y_yield' THEN 'INTEREST_RATE' WHEN 'kr_10y_yield' THEN 'INTEREST_RATE' WHEN 'kr_cpi_yoy' THEN 'INFLATION' WHEN 'brent_spot_usd' THEN 'COMMODITY' END)::text AS indicator_type
FROM public.macro_observation AS base
) AS mapped
) AS record
;

-- views/market_capitalization.sql
CREATE OR REPLACE VIEW ontology_view.market_capitalization AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(security_e.display_name,''), record.instrument_id, 'Unknown security') || COALESCE(' (' || security_i.ticker || ' / ' || security_i.market_code || ')',''), 'Market capitalization record', record.snapshot_date::text))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.amount::text AS amount_decimal_text
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.instrument_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.instrument_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.instrument_id::text)::text || ',' || to_json(base.as_of_date::text)::text || ']')::text AS id,
       (base.instrument_id)::text AS instrument_id,
       (base.as_of_date)::date AS snapshot_date,
       (NULL)::numeric AS amount,
       (base.source)::text AS source,
       (base.available_at)::timestamptz AS available_at,
       (NULL)::text AS currency_code,
       (NULL)::text AS capitalization_scope,
       (NULL)::date AS valuation_date
FROM public.instrument_classification AS base
) AS mapped
) AS mapped
) AS record
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.instrument_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/market_index.sql
CREATE OR REPLACE VIEW ontology_view.market_index AS
SELECT (base.market_series_id::text)::text AS id,
       (base.series_name)::text AS series_name,
       (base.source_code)::text AS source_code,
       (base.source_series_id)::text AS source_series_id,
       (base.market_code)::text AS market_code,
       (base.currency_code)::text AS currency_code,
       (NULL)::text[] AS regions
FROM public.market_series AS base
WHERE base.series_type = 'INDEX';

-- views/news_article.sql
CREATE OR REPLACE VIEW ontology_view.news_article AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (representative.title)::text AS representative_title,
       (theme_e.display_name)::text AS theme_name
FROM (
SELECT (base.document_id::text)::text AS id,
       (document.title)::text AS title,
       (document.published_at)::timestamptz AS published_at,
       (document.available_at)::timestamptz AS available_at,
       (document.source_code)::text AS source_code,
       (document.source_document_id)::text AS source_document_id,
       (document.source_uri)::text AS source_uri,
       (base.lead_text)::text AS lead_text,
       (base.publisher)::text AS publisher,
       (base.lead_observed_at)::timestamptz AS lead_observed_at,
       (base.representative_document_id)::text AS representative_document_id,
       (base.theme_concept_id)::text AS theme_concept_id
FROM public.news_document AS base
LEFT JOIN public.document AS document ON base.document_id = document.document_id AND base.document_type = document.document_type
) AS record
LEFT JOIN public.document representative ON representative.document_id=record.representative_document_id AND representative.document_type='NEWS'
LEFT JOIN public.entity theme_e ON theme_e.entity_id=record.theme_concept_id;

-- views/organization.sql
CREATE OR REPLACE VIEW ontology_view.organization AS
SELECT base.actor_id::text AS id,
       entity.display_name::text AS name,
       base.actor_type::text AS actor_type,
       base.country_code::text AS country_code
FROM public.actor AS base
LEFT JOIN public.entity AS entity
  ON base.actor_id = entity.entity_id AND base.entity_type = entity.entity_type
WHERE base.actor_type IN ('GOVERNMENT', 'INSTITUTION');

-- views/reported_business_segment.sql
CREATE OR REPLACE VIEW ontology_view.reported_business_segment AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (company_e.display_name)::text AS company_name,
       (document.title)::text AS document_title,
       (concept_e.display_name)::text AS concept_name,
       (concat_ws(' / ', COALESCE(NULLIF(company_e.display_name,''),disclosure.issuer_actor_id,'Unknown company'), record.segment_name, record.reported_period_text, document.title))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.revenue_krw::text AS revenue_krw_decimal_text,
       mapped.revenue_total_krw::text AS revenue_total_krw_decimal_text
FROM (
SELECT (base.fact_id::text)::text AS id,
       (base.segment_name)::text AS segment_name,
       (base.concept_id)::text AS concept_id,
       (base.period_label)::text AS reported_period_text,
       (base.revenue_krw)::numeric AS revenue_krw,
       (base.revenue_share_pct)::double precision AS revenue_share_pct,
       (CASE base.share_basis WHEN 'REPORTED' THEN 'REPORTED' WHEN 'COMPUTED' THEN 'COMPUTED' WHEN 'RESCALED' THEN 'RESCALED' WHEN 'UNRELIABLE' THEN NULL END)::text AS share_calculation_method,
       (fact.document_id)::text AS document_id,
       (fact.available_at)::timestamptz AS available_at,
       (NULL)::date AS period_start,
       (NULL)::date AS period_end,
       (NULL)::text AS statement_basis,
       (NULL)::numeric AS revenue_total_krw,
       (NULL)::text AS revenue_total_scope
FROM public.business_segment_fact AS base
LEFT JOIN public.disclosure_fact AS fact ON base.fact_id = fact.fact_id AND base.fact_type = fact.fact_type
) AS mapped
) AS record
LEFT JOIN public.disclosure_document disclosure ON disclosure.document_id=record.document_id
LEFT JOIN public.entity company_e ON company_e.entity_id=disclosure.issuer_actor_id
LEFT JOIN public.document document ON document.document_id=disclosure.document_id AND document.document_type=disclosure.document_type
LEFT JOIN public.entity concept_e ON concept_e.entity_id=record.concept_id;

-- views/security_industry_classification.sql
CREATE OR REPLACE VIEW ontology_view.security_industry_classification AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(security_e.display_name,''), record.instrument_id, 'Unknown security') || COALESCE(' (' || security_i.ticker || ' / ' || security_i.market_code || ')',''), 'Industry classification', record.snapshot_date::text, record.sector_name, record.industry_name))::text AS display_title
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.instrument_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.instrument_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.instrument_id::text)::text || ',' || to_json(base.as_of_date::text)::text || ']')::text AS id,
       (base.instrument_id)::text AS instrument_id,
       (base.as_of_date)::date AS snapshot_date,
       (base.sector_name)::text AS sector_name,
       (base.industry_name)::text AS industry_name,
       (base.source)::text AS source,
       (base.available_at)::timestamptz AS available_at,
       (NULL)::date AS effective_from
FROM public.instrument_classification AS base
) AS mapped
) AS record
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.instrument_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/security_listing_snapshot.sql
CREATE OR REPLACE VIEW ontology_view.security_listing_snapshot AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (security_e.display_name)::text AS security_name,
       (security_i.ticker)::text AS security_ticker,
       (security_i.market_code)::text AS security_market_code,
       (concat_ws(' / ', COALESCE(NULLIF(security_e.display_name,''), record.instrument_id, 'Unknown security') || COALESCE(' (' || security_i.ticker || ' / ' || security_i.market_code || ')',''), 'Listing snapshot', record.snapshot_date::text))::text AS display_title
FROM (
SELECT mapped.*,
       CASE WHEN EXISTS (SELECT 1 FROM public.equity_profile ep WHERE ep.instrument_id=mapped.instrument_id) THEN 'EQUITY' WHEN EXISTS (SELECT 1 FROM public.etf_profile et WHERE et.instrument_id=mapped.instrument_id) THEN 'ETF' END::text AS security_type
FROM (
SELECT ('[' || to_json(base.instrument_id::text)::text || ',' || to_json(base.as_of_date::text)::text || ']')::text AS id,
       (base.instrument_id)::text AS instrument_id,
       (base.as_of_date)::date AS snapshot_date,
       (base.listing_market)::text AS listing_market,
       (base.is_primary_share)::boolean AS is_primary_share,
       (base.source)::text AS source,
       (base.available_at)::timestamptz AS available_at
FROM public.instrument_classification AS base
) AS mapped
) AS record
LEFT JOIN public.instrument security_i ON security_i.instrument_id=record.instrument_id
LEFT JOIN public.entity security_e ON security_e.entity_id=security_i.instrument_id AND security_e.entity_type=security_i.entity_type;

-- views/source_event.sql
CREATE OR REPLACE VIEW ontology_view.source_event AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (context.summary)::text AS argument_summary,
       (concat_ws(' / ', record.event_type, COALESCE(context.summary,'No named arguments'), record.event_date::text))::text AS display_title
FROM (
SELECT base.source_event_id::text AS id,
       base.event_type_code::text AS event_type,
       base.predicate_code::text AS predicate,
       base.source_class::text AS source_class,
       base.event_date::date AS event_date,
       base.available_at::timestamptz AS available_at,
       base.lifecycle_stage::text AS lifecycle_stage
FROM public.source_event AS base
) AS record
LEFT JOIN LATERAL (SELECT string_agg(COALESCE(NULLIF(e.display_name,''), NULLIF(a.mention_text,''), a.entity_id, 'Unresolved entity') || ' [' || a.role_code || ']', '; ' ORDER BY a.event_argument_id) AS summary FROM (SELECT a.event_argument_id, a.entity_id, a.role_code, a.mention_text FROM public.event_argument a WHERE a.source_event_id=record.id) a LEFT JOIN public.entity e ON e.entity_id=a.entity_id) context ON true;

-- 읽기 권한. agent_ro(db-query 의 읽기전용 롤, V202607300001)는 늘 있다. ontology_graph_ro(그래프 DB 의
-- 접속 계정)는 비밀번호가 있는 로그인 롤이라 Flyway 밖에서 만들며, 로컬·CI 에는 없으므로 있을 때만 부여한다.
GRANT USAGE ON SCHEMA ontology_view TO agent_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ontology_view TO agent_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA ontology_view GRANT SELECT ON TABLES TO agent_ro;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ontology_graph_ro') THEN
    GRANT USAGE ON SCHEMA ontology_view TO ontology_graph_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA ontology_view TO ontology_graph_ro;
    ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA ontology_view GRANT SELECT ON TABLES TO ontology_graph_ro;
  END IF;
END
$$;
