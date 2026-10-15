CREATE OR REPLACE VIEW ontology_view.news_article AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (representative.title)::text AS representative_title,
       (theme_e.display_name)::text AS theme_name
FROM (
SELECT (base.document_id::text)::text AS id,
       (document.title)::text AS title,
       (document.published_at)::timestamptz AS published_at,
       (document.available_at)::timestamptz AS available_at,
       (document.source_code)::text AS source_code,
       (document.source_document_id)::text AS source_document_id,
       (document.source_uri)::text AS source_uri,
       (base.lead_text)::text AS lead_text,
       (base.publisher)::text AS publisher,
       (base.lead_observed_at)::timestamptz AS lead_observed_at,
       (base.representative_document_id)::text AS representative_document_id,
       (base.theme_concept_id)::text AS theme_concept_id
FROM public.news_document AS base
LEFT JOIN public.document AS document ON base.document_id = document.document_id AND base.document_type = document.document_type
) AS record
LEFT JOIN public.document representative ON representative.document_id=record.representative_document_id AND representative.document_type='NEWS'
LEFT JOIN public.entity theme_e ON theme_e.entity_id=record.theme_concept_id;
