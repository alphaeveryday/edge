-- Dedicated local database only; test records are rolled back.
\set ON_ERROR_STOP on
BEGIN;
INSERT INTO movement_analyses (analysis_id, etf_code, analysis_at, trading_date)
VALUES ('test-movement', '091160', '2026-09-21T10:00:00+09', '2026-09-21');
INSERT INTO outlook_analyses (analysis_id, etf_code, analysis_at)
VALUES ('test-outlook', '091160', '2026-09-21T08:30:00+09');
INSERT INTO tool_definitions (tool_id, function_name, version, description, source_names)
VALUES ('test-tool', 'sum_flow', '1', 'Sum finalized investor net amounts.', ARRAY['mock_flow']);
INSERT INTO tool_runs (tool_run_id, tool_id, movement_analysis_id, arguments, output, status, finished_at)
VALUES ('test-run', 'test-tool', 'test-movement', '{"days":5}',
        '{"tool_run_id":"test-run","result":{"amount_krw":18}}', 'completed', now());
INSERT INTO outlook_items (row_id, analysis_id, item_id, section, position, title_keyword, bullets)
VALUES ('test-detail', 'test-outlook', 'topic-1', 'detail', 0, 'Title',
        '[{"sentence":"Original","is_updated":false}]');
INSERT INTO outlook_items (row_id, analysis_id, item_id, section, change_type, position, title_keyword)
VALUES ('test-deletion', 'test-outlook', 'removed-topic', 'update', 'deleted', 0, 'Deleted topic');

DO $$
DECLARE rejected integer := 0;
BEGIN
  -- An audit run belongs to exactly one analysis, never both or neither.
  BEGIN
    INSERT INTO tool_runs (tool_run_id, tool_id) VALUES ('orphan', 'test-tool');
  EXCEPTION WHEN check_violation THEN rejected := rejected + 1; END;
  BEGIN
    INSERT INTO tool_runs (tool_run_id, tool_id, movement_analysis_id, outlook_analysis_id)
    VALUES ('both', 'test-tool', 'test-movement', 'test-outlook');
  EXCEPTION WHEN check_violation THEN rejected := rejected + 1; END;
  BEGIN
    INSERT INTO tool_runs (tool_run_id, tool_id, movement_analysis_id)
    VALUES ('missing-parent', 'test-tool', 'not-an-analysis');
  EXCEPTION WHEN foreign_key_violation THEN rejected := rejected + 1; END;
  -- A missing assessment must not introduce a sixth sticker state.
  BEGIN
    INSERT INTO outlook_factors (row_id, analysis_id, type, sticker, sentence)
    VALUES ('invalid-sticker', 'test-outlook', '차트', '판단유보', 'Unavailable');
  EXCEPTION WHEN check_violation THEN rejected := rejected + 1; END;
  -- Two body topics cannot occupy the same display position.
  BEGIN
    INSERT INTO outlook_items (row_id, analysis_id, item_id, section, position, title_keyword, bullets)
    VALUES ('duplicate', 'test-outlook', 'topic-2', 'detail', 0, 'Title', '[]');
  EXCEPTION WHEN unique_violation THEN rejected := rejected + 1; END;
  BEGIN
    UPDATE movement_analyses SET selected_item_ids = ARRAY['1','2','3','4','5','6']
    WHERE analysis_id = 'test-movement';
  EXCEPTION WHEN check_violation THEN rejected := rejected + 1; END;
  IF rejected <> 6 THEN RAISE EXCEPTION 'Expected 6 constraint rejections, got %', rejected; END IF;
  IF (SELECT output->'result'->>'amount_krw' FROM tool_runs WHERE tool_run_id='test-run') <> '18'
  THEN RAISE EXCEPTION 'Stored tool result changed'; END IF;
  RAISE NOTICE 'PASS: 6 constraint rejections, audit roundtrip, body and deletion storage';
END $$;
ROLLBACK;
