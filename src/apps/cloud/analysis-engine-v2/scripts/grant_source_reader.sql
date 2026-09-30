-- 원천 보유량 조사와 v2 연결을 위한 전용 reader 권한. writer 권한은 변경하지 않는다.
-- 실제 분석의 판본·시점 선택은 기존 *_as_of 함수를 사용한다.
GRANT SELECT ON TABLE public.macro_observation, public.market_series,
    public.financial_metric, public.financial_report_version,
    public.sector_classification TO edge_analysis_v2_reader;

GRANT EXECUTE ON FUNCTION
    public.macro_observations_as_of(TIMESTAMPTZ, TEXT, INTEGER),
    public.financial_quarters_as_of(TIMESTAMPTZ, TEXT),
    public.sector_classification_as_of(TIMESTAMPTZ, TEXT[]),
    public.etf_constituent_source_coverage(TEXT, TIMESTAMPTZ),
    public.source_observation_freshness()
TO edge_analysis_v2_reader;
