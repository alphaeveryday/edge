-- 미 국채 10년 공급자 교체(ALPHA-1136): FMP `treasury-rates` year10 → FRED `DGS10`.
-- 같은 계열이다(FRED DGS10 = 연준 H.15 10년 CMT = 재무부 일별 par yield curve 10년, 설계 §10) — series_id·단위는 그대로.
-- 확장 단계만: FRED 튜플을 더하고 FMP 튜플은 남긴다. 옛 FMP 판본 행(있다면)이 계속 유효해야 하고,
-- 코드가 이 마이그레이션보다 먼저 배포돼도 FMP 행을 쓰던 옛 이미지가 깨지지 않는다. FMP 튜플 제거는
-- 옛 행이 없다는 것이 확인된 뒤의 별도 수축 단계다.
-- 문자열은 sources/macro_series.py SERIES 의 source_series 와 같다.
SET LOCAL lock_timeout = '3s';
ALTER TABLE macro_observation DROP CONSTRAINT ck_macro_observation_series;
ALTER TABLE macro_observation ADD CONSTRAINT ck_macro_observation_series CHECK (
    (series_id, unit, source_vendor, source_series) IN (
        ('usd_krw', 'KRW_per_USD', 'ecos', 'ECOS 731Y003/D/0000003'),
        ('us_10y_yield', 'percent', 'fmp', 'FMP treasury-rates year10'),
        ('us_10y_yield', 'percent', 'fred', 'FRED DGS10'),
        ('kr_10y_yield', 'percent', 'ecos', 'ECOS 817Y002/D/010210000'),
        ('kr_cpi_yoy', 'percent', 'kosis', 'KOSIS 101/DT_1J22042 T03 objL1=0'),
        ('brent_spot_usd', 'USD_per_barrel', 'eia', 'EIA petroleum/pri/spt RBRTE')));
