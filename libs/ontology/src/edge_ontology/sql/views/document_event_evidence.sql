CREATE OR REPLACE VIEW ontology_view.document_event_evidence AS
SELECT evidence.evidence_id::text AS evidence_id,
       assertion.document_id::text AS document_id,
       evidence.source_event_id::text AS source_event_id,
       CASE WHEN document.document_type = 'NEWS' AND EXISTS
                 (SELECT 1 FROM public.news_document news WHERE news.document_id=assertion.document_id)
            THEN 'NEWS'
            WHEN document.document_type = 'DISCLOSURE' AND EXISTS
                 (SELECT 1 FROM public.disclosure_document disclosure WHERE disclosure.document_id=assertion.document_id)
            THEN 'DISCLOSURE'
       END::text AS document_type,
       assertion.confidence::double precision AS extraction_confidence,
       evidence.assertion_id::text AS assertion_id,
       evidence.evidence_type::text AS evidence_type,
       evidence.evidence_text::text AS evidence_text
FROM public.event_evidence AS evidence
JOIN public.document_assertion AS assertion ON assertion.assertion_id = evidence.assertion_id
JOIN public.document AS document ON document.document_id = assertion.document_id;
