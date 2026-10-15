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
