CREATE OR REPLACE VIEW ontology_view.organization AS
SELECT base.actor_id::text AS id,
       entity.display_name::text AS name,
       base.actor_type::text AS actor_type,
       base.country_code::text AS country_code
FROM public.actor AS base
LEFT JOIN public.entity AS entity
  ON base.actor_id = entity.entity_id AND base.entity_type = entity.entity_type
WHERE base.actor_type IN ('GOVERNMENT', 'INSTITUTION');
