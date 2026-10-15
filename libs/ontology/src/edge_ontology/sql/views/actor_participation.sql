CREATE OR REPLACE VIEW ontology_view.actor_participation AS
SELECT argument.event_argument_id::text AS participation_id,
       argument.entity_id::text AS actor_id,
       argument.source_event_id::text AS source_event_id,
       CASE WHEN actor.actor_type = 'COMPANY' AND EXISTS
                 (SELECT 1 FROM public.company_profile company WHERE company.actor_id=argument.entity_id)
            THEN 'COMPANY'
            WHEN actor.actor_type IN ('GOVERNMENT', 'INSTITUTION') THEN actor.actor_type
       END::text AS actor_type,
       argument.role_code::text AS role_code,
       argument.mention_text::text AS mentioned_name,
       argument.group_ord::smallint AS argument_group
FROM public.event_argument AS argument
LEFT JOIN public.actor AS actor ON actor.actor_id = argument.entity_id;
