-- 원천이 종가만 주는 기간의 일봉. 시가·고가·저가는 없으면 NULL.
ALTER TABLE etf_candle
    ALTER COLUMN open DROP NOT NULL,
    ALTER COLUMN high DROP NOT NULL,
    ALTER COLUMN low DROP NOT NULL;
