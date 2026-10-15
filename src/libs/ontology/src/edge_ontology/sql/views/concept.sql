CREATE OR REPLACE VIEW ontology_view.concept AS
-- Display context is additive; source identities, values and row scope are unchanged.
SELECT record.*,
       (parentConcept_e.display_name)::text AS parent_concept_name
FROM (
SELECT (base.concept_id::text)::text AS id,
       (entity.display_name)::text AS name,
       (base.concept_type)::text AS concept_type,
       (base.parent_concept_id)::text AS parent_concept_id
FROM public.concept AS base
LEFT JOIN public.entity AS entity ON base.concept_id = entity.entity_id AND base.entity_type = entity.entity_type
) AS record
LEFT JOIN public.entity parentConcept_e ON parentConcept_e.entity_id=record.parent_concept_id;
