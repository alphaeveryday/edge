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
