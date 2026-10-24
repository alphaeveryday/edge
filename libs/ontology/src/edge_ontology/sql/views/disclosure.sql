CREATE OR REPLACE VIEW ontology_view.disclosure AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (issuer_e.display_name)::text AS issuer_name,
       (COALESCE(NULLIF(record.title,''), concat_ws(' / ', COALESCE(NULLIF(issuer_e.display_name,''), record.issuer_id, 'Unknown issuer'),record.disclosure_type,record.report_date::text,record.source_document_id)))::text AS display_title
FROM (
SELECT (base.document_id::text)::text AS id,
       (base.issuer_actor_id)::text AS issuer_id,
       (base.disclosure_type)::text AS disclosure_type,
       (base.report_date)::date AS report_date,
       (document.title)::text AS title,
       (document.published_at)::timestamptz AS published_at,
       (document.available_at)::timestamptz AS available_at,
       (document.source_document_id)::text AS source_document_id,
       (document.source_uri)::text AS source_uri
FROM public.disclosure_document AS base
LEFT JOIN public.document AS document ON base.document_id = document.document_id AND base.document_type = document.document_type
) AS record
LEFT JOIN public.entity issuer_e ON issuer_e.entity_id=record.issuer_id;
