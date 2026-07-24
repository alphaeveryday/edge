-- 투자자별 매매동향 원장 신설 (ALPHA-385).
--
-- canonical/market_data/investor_flow_daily(WIDE) 를 담는 물리 테이블. normalize_investor
-- (ALPHA-482)가 확정한 표준 어휘 13종 × (순매수 수량·대금) = 26 컬럼을 그대로 미러한다.
-- grain 은 **금융상품·거래일**(개별주식 단위 — ETF 자체가 아니라 구성종목의 수급이다).
-- FK 는 price_daily 와 같이 instrument(instrument_id) — etf_profile 이 아니다(개별주식이라
-- ETF 프로파일 선행이 필요 없다). 새 테이블은 계약이다(ADR-0005).
--
-- 어휘 미확정 컬럼에 NOT NULL 을 걸지 않는다(ALPHA-361·378 이 그렇게 해서 적재가 두 번
-- 막혔다). headline 3종(개인·외국인·기관계)만 NOT NULL — canonical 이 필수로 보장하는 어휘다.
-- 기관 세부 10종은 nullable(KIS 가 특정 종목의 세부를 안 줄 수 있어 canonical 도 null 관용).
--
-- 순매수 수량·대금 모두 BIGINT: canonical 은 두 값 다 int64 다(정제가 대금을 백만원→원으로
-- 환산해 정수로 저장). 일별 순매수 대금은 조 단위(~1e13)라 BIGINT(~9.2e18) 에 여유롭게 들어가고,
-- int64 원본을 정확히 미러한다(NUMERIC 왕복 없음). 순매수는 음수가 정상이라 값 부호 CHECK 는
-- 걸지 않는다. BIGINT 는 NaN/Inf 가 불가능해 유한성 CHECK 도 불필요하다(price_daily 와 다른 점).

CREATE TABLE investor_flow_daily (
    instrument_id       TEXT NOT NULL,
    trade_date          DATE NOT NULL,

    -- headline 3종 — canonical 필수 어휘(개인·외국인·기관계). 수량·대금 모두 NOT NULL.
    net_qty_individual          BIGINT NOT NULL,  -- 개인 순매수 수량(주)
    net_val_individual          BIGINT NOT NULL,  -- 개인 순매수 대금(원)
    net_qty_foreign             BIGINT NOT NULL,  -- 외국인
    net_val_foreign             BIGINT NOT NULL,
    net_qty_institution_total   BIGINT NOT NULL,  -- 기관계
    net_val_institution_total   BIGINT NOT NULL,

    -- 기관 세부 10종 — 선택(canonical 이 null 관용). NOT NULL 금지(ALPHA-361·378 교훈).
    net_qty_financial_invest    BIGINT,  -- 증권(금융투자)
    net_val_financial_invest    BIGINT,
    net_qty_investment_trust    BIGINT,  -- 투신
    net_val_investment_trust    BIGINT,
    net_qty_pension             BIGINT,  -- 연기금등(기금)
    net_val_pension             BIGINT,
    net_qty_private_fund        BIGINT,  -- 사모
    net_val_private_fund        BIGINT,
    net_qty_bank                BIGINT,  -- 은행
    net_val_bank                BIGINT,
    net_qty_insurance           BIGINT,  -- 보험
    net_val_insurance           BIGINT,
    net_qty_merchant_bank       BIGINT,  -- 종금
    net_val_merchant_bank       BIGINT,
    net_qty_other_corp          BIGINT,  -- 기타법인
    net_val_other_corp          BIGINT,
    net_qty_other_org           BIGINT,  -- 기타단체
    net_val_other_org           BIGINT,
    net_qty_other               BIGINT,  -- 기타
    net_val_other               BIGINT,

    available_at        TIMESTAMPTZ NOT NULL,
    data_version        VARCHAR(50) NOT NULL,

    PRIMARY KEY (instrument_id, trade_date)
);

COMMENT ON TABLE investor_flow_daily IS
'금융상품·거래일 grain의 투자자별 순매수(수량·대금) 원장. 개별주식 단위이며 순매수는 음수가 정상이다.';

CREATE INDEX ix_investor_flow_daily_trade_date ON investor_flow_daily (trade_date);

ALTER TABLE investor_flow_daily
    ADD CONSTRAINT fk_investor_flow_daily_instrument
    FOREIGN KEY (instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE CASCADE;
