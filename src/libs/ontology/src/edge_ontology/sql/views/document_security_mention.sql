CREATE OR REPLACE VIEW ontology_view.document_security_mention AS
SELECT match.document_id::text AS document_id,
       match.entity_id::text AS entity_id,
       match.link_method::text AS link_method,
       match.matched_text::text AS matched_text
FROM public.document_entity AS match
JOIN public.news_document AS news ON news.document_id = match.document_id
JOIN public.equity_profile AS equity ON equity.instrument_id = match.entity_id
WHERE match.link_method = 'mention';
