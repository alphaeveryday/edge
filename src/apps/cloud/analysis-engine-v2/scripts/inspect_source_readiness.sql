-- 전용 reader 접속에서 실행. 결과는 조회시각의 보유량이며 미래/과거 가시성 보장이 아니다.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '15s';
SELECT current_database(), current_user, now() AS checked_at,
       current_setting('transaction_read_only') AS read_only;

SELECT 'macro_observation' AS dataset, count(*) AS rows FROM public.macro_observation
UNION ALL SELECT 'market_series', count(*) FROM public.market_series
UNION ALL SELECT 'financial_metric', count(*) FROM public.financial_metric
UNION ALL SELECT 'financial_report_version', count(*) FROM public.financial_report_version
UNION ALL SELECT 'sector_classification', count(*) FROM public.sector_classification;

SELECT series_id, unit, source_vendor, count(DISTINCT observation_date) AS observations,
       min(observation_date) AS first_date, max(observation_date) AS last_date,
       min(available_at) AS first_available_at, max(available_at) AS last_available_at
FROM public.macro_observation GROUP BY series_id, unit, source_vendor ORDER BY series_id;

SELECT metric, period_kind, fs_basis, count(*) AS rows,
       count(DISTINCT instrument_code) AS instruments,
       min(period_end) AS first_period, max(period_end) AS last_period
FROM public.financial_metric GROUP BY metric, period_kind, fs_basis
ORDER BY metric, period_kind, fs_basis;

SELECT * FROM public.etf_constituent_source_coverage('091160', now());
SELECT * FROM public.source_observation_freshness();
COMMIT;
