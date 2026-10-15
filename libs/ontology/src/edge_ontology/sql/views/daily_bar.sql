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
