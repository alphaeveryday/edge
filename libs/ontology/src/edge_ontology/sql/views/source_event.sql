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
