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
