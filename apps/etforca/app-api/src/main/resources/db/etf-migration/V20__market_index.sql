-- 홈 상단의 시장 지수, 파이프라인 일봉 종가의 동기화 사본
CREATE TABLE market_index (
    code        VARCHAR(10)    PRIMARY KEY,
    name        VARCHAR(20)    NOT NULL,
    price       NUMERIC(14,2)  NOT NULL,
    change_pct  NUMERIC(6,2)   NOT NULL,
    as_of       TIMESTAMPTZ    NOT NULL,
    synced_at   TIMESTAMPTZ    NOT NULL DEFAULT now()
);
