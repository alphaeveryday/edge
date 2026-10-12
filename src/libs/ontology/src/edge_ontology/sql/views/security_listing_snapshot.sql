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
