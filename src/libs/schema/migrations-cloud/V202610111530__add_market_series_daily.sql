-- 시장 시계열의 일별 값 원장을 추가하고 코스피·코스닥 종합지수를 마스터에 등록한다(ALPHA-1260).
--
-- 왜 필요한가: 코스피·코스닥 일봉을 읽을 표가 없었다. price_daily 는 FK 가 instrument 이고
-- instrument_type 은 ETF·EQUITY 뿐이라(ck_instrument_type) 지수를 받으려면 지수를 종목으로
-- 등록해야 한다. market_series 는 지수·환율·금리 마스터로 이미 있지만 값을 담는 표가 없었다.
--
-- grain 은 시계열·거래일이다. 값의 출처는 canonical/market_data/sector_index_daily 이고 그
-- dataset 은 시가·고가·저가·종가만 싣는다 — 그래서 거래량·거래대금 컬럼은 두지 않는다.
-- 이 파일은 표와 마스터 2행만 만든다. 값을 옮기는 적재 스텝은 후속 PR 이다.
--
-- 컬럼 이름이 price 가 아니라 value 인 이유: market_series 는 지수(포인트)·환율·금리를 함께
-- 담는 마스터라 가격이 아닌 값이 들어온다.

CREATE TABLE market_series_daily (
    market_series_id    TEXT NOT NULL,
    trade_date          DATE NOT NULL,
    open_value          NUMERIC(24, 8),
    high_value          NUMERIC(24, 8),
    low_value           NUMERIC(24, 8),
    close_value         NUMERIC(24, 8) NOT NULL,
    available_at        TIMESTAMPTZ NOT NULL,
    data_version        VARCHAR(50) NOT NULL,

    PRIMARY KEY (market_series_id, trade_date),

    CONSTRAINT fk_market_series_daily_series
        FOREIGN KEY (market_series_id)
        REFERENCES market_series (market_series_id)
        ON DELETE CASCADE,

    -- NaN 만 막는다. ±Infinity 는 자릿수를 지정한 NUMERIC(24, 8) 이 타입에서 거부하므로 남는
    -- 비유한 값은 NaN 뿐이다. 부호는 걸지 않는다 — 금리 계열은 0 이하가 정상값이라 price_daily 의
    -- 양수 규칙을 그대로 옮기면 그 시계열을 받지 못한다.
    CONSTRAINT ck_market_series_daily_values
        CHECK (
            close_value IS DISTINCT FROM 'NaN'::NUMERIC
            AND open_value IS DISTINCT FROM 'NaN'::NUMERIC
            AND high_value IS DISTINCT FROM 'NaN'::NUMERIC
            AND low_value IS DISTINCT FROM 'NaN'::NUMERIC
        )
);

COMMENT ON TABLE market_series_daily IS
'시장 시계열·거래일 grain의 일별 값 원장. 지수는 포인트 값이며 거래량·거래대금은 싣지 않는다.';
COMMENT ON COLUMN market_series_daily.close_value IS '종가(지수는 포인트).';
COMMENT ON COLUMN market_series_daily.open_value IS '시가. 일중 값이 없는 시계열은 NULL.';
COMMENT ON COLUMN market_series_daily.high_value IS '고가. 일중 값이 없는 시계열은 NULL.';
COMMENT ON COLUMN market_series_daily.low_value IS '저가. 일중 값이 없는 시계열은 NULL.';
COMMENT ON COLUMN market_series_daily.available_at IS '이 값을 알게 된 시각(수집 시각).';
COMMENT ON COLUMN market_series_daily.data_version IS '이 행을 마지막으로 확정한 적재 실행 ID.';

-- 종합지수 2종. source_series_id 는 KRX 업종코드이고 canonical sector_index_daily 의 code 와
-- 같은 값이다 — 적재 스텝이 (source_code, source_series_id) 로 시계열을 찾는다.
-- market_series_id 는 ADR-0027 과 같은 불투명 ID 다(접두사 series_ + ULID). 지수는 통화가 아닌
-- 포인트 값이라 currency_code 는 비운다.
INSERT INTO market_series
    (market_series_id, series_type, series_name, source_code, source_series_id, market_code)
VALUES
    ('series_01M4MS4NN3VS1DEZABKSZHS88G', 'INDEX', '코스피', 'KRX', '1001', 'XKRX'),
    ('series_01M4MS4NN3B60J6VZ2Q6GDY47B', 'INDEX', '코스닥', 'KRX', '2001', 'XKOS');
