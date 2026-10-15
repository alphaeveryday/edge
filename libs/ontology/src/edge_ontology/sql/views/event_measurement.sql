CREATE OR REPLACE VIEW ontology_view.event_measurement AS
SELECT jsonb_build_array(m.source_event_id::text, m.measure_ord::text)::text AS id,
       m.source_event_id::text AS source_event_id,
       m.measure_ord::smallint AS measurement_ordinal,
       m.role_code::text AS metric_code,
       m.surface::text AS reported_text,
       m.value::numeric AS value,
       m.unit::text AS unit,
       m.basis::text AS period_basis,
       m.value_source::text AS value_source,
       m.parse_flag::text AS parse_status,
       m.group_ord::smallint AS argument_group,
       m.dart_rcept_no::text AS receipt_number,
       e.available_at::timestamptz AS available_at,
       concat_ws(' / ',m.role_code,m.surface,e.event_date::text)::text AS display_title,
       m.value::text AS value_decimal
FROM public.event_measure m
JOIN public.source_event e ON e.source_event_id=m.source_event_id;
