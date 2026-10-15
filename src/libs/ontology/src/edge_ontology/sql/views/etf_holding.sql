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
