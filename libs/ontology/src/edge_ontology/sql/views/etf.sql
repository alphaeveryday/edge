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
