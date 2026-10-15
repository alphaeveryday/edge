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
