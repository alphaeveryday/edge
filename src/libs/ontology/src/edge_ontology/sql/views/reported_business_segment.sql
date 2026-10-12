CREATE OR REPLACE VIEW ontology_view.reported_business_segment AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (company_e.display_name)::text AS company_name,
       (document.title)::text AS document_title,
       (concept_e.display_name)::text AS concept_name,
       (concat_ws(' / ', COALESCE(NULLIF(company_e.display_name,''),disclosure.issuer_actor_id,'Unknown company'), record.segment_name, record.reported_period_text, document.title))::text AS display_title
FROM (
SELECT mapped.*,
       mapped.revenue_krw::text AS revenue_krw_decimal_text,
       mapped.revenue_total_krw::text AS revenue_total_krw_decimal_text
FROM (
SELECT (base.fact_id::text)::text AS id,
       (base.segment_name)::text AS segment_name,
       (base.concept_id)::text AS concept_id,
       (base.period_label)::text AS reported_period_text,
       (base.revenue_krw)::numeric AS revenue_krw,
       (base.revenue_share_pct)::double precision AS revenue_share_pct,
       (CASE base.share_basis WHEN 'REPORTED' THEN 'REPORTED' WHEN 'COMPUTED' THEN 'COMPUTED' WHEN 'RESCALED' THEN 'RESCALED' WHEN 'UNRELIABLE' THEN NULL END)::text AS share_calculation_method,
       (fact.document_id)::text AS document_id,
       (fact.available_at)::timestamptz AS available_at,
       (NULL)::date AS period_start,
       (NULL)::date AS period_end,
       (NULL)::text AS statement_basis,
       (NULL)::numeric AS revenue_total_krw,
       (NULL)::text AS revenue_total_scope
FROM public.business_segment_fact AS base
LEFT JOIN public.disclosure_fact AS fact ON base.fact_id = fact.fact_id AND base.fact_type = fact.fact_type
) AS mapped
) AS record
LEFT JOIN public.disclosure_document disclosure ON disclosure.document_id=record.document_id
LEFT JOIN public.entity company_e ON company_e.entity_id=disclosure.issuer_actor_id
LEFT JOIN public.document document ON document.document_id=disclosure.document_id AND document.document_type=disclosure.document_type
LEFT JOIN public.entity concept_e ON concept_e.entity_id=record.concept_id;
