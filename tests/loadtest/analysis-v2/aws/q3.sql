SELECT json_agg(x ORDER BY x.published_at NULLS LAST) AS j FROM (
  SELECT analysis_id, etf_code, status, published_at, previous_analysis_id IS NOT NULL AS has_previous, left(error_message, 80) AS error
  FROM outlook_analyses WHERE analysis_at = '2026-10-02T06:26:00Z' AND data_source = 'database') x
