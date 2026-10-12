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
