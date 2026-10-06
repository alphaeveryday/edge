CREATE OR REPLACE VIEW ontology_view.company AS
SELECT base.actor_id::text AS id,
       entity.display_name::text AS name,
       actor.country_code::text AS country_code,
       base.dart_corp_code::text AS dart_corp_code,
       base.profile_as_of_date::date AS profile_as_of_date
FROM public.company_profile AS base
LEFT JOIN public.actor AS actor
  ON base.actor_id = actor.actor_id AND base.actor_type = actor.actor_type
LEFT JOIN public.entity AS entity
  ON actor.actor_id = entity.entity_id AND actor.entity_type = entity.entity_type;
