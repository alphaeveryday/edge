CREATE OR REPLACE VIEW ontology_view.event_thread AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (context.summary)::text AS argument_summary,
       (concat_ws(' / ', record.event_type, COALESCE(context.summary,'No named arguments'), to_char(record.opened_at AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS') || ' UTC'))::text AS display_title
FROM (
SELECT (base.thread_id::text)::text AS id,
       (base.event_type_code)::text AS event_type,
       (NULL)::text AS current_stage,
       (base.opened_at)::timestamptz AS opened_at,
       (NULL)::timestamptz AS last_state_at
FROM public.event_thread AS base
) AS record
LEFT JOIN LATERAL (SELECT string_agg(COALESCE(NULLIF(e.display_name,''), NULLIF(a.mention_text,''), a.entity_id, 'Unresolved entity') || ' [' || a.role_code || ']', '; ' ORDER BY a.entity_id, a.role_code, a.mention_text) AS summary FROM (SELECT DISTINCT a.entity_id, a.role_code, a.mention_text FROM public.event_thread_link l JOIN public.event_argument a ON a.source_event_id=l.source_event_id WHERE l.thread_id=record.id) a LEFT JOIN public.entity e ON e.entity_id=a.entity_id) context ON true;
