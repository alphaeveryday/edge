CREATE OR REPLACE VIEW ontology_view.etf_price_explanation AS
SELECT a.analysis_id::text AS id,e.id::text AS etf_instrument_id,
       a.analysis_at::timestamptz AS analysis_at,a.published_at::timestamptz AS available_at,
       a.summary::text AS summary,a.previous_analysis_id::text AS previous_report_id,
       concat_ws(' / ',e.name,'movement',a.analysis_at::text)::text AS display_title,
       ARRAY(SELECT t.tool_run_id::text FROM public.tool_runs t WHERE t.movement_analysis_id=a.analysis_id AND t.status='completed' ORDER BY t.tool_run_id)::text[] AS evidence_run_ids
FROM public.movement_analyses a
LEFT JOIN ontology_view.etf e ON e.ticker=a.etf_code AND e.market_code='XKRX'
WHERE a.status='completed' AND a.data_source='database' AND a.published_at IS NOT NULL AND a.withdrawn_at IS NULL;
