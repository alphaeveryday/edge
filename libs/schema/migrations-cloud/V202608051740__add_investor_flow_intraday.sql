-- 장중 투자자 추정(가집계) 원장 신설 (ALPHA-768).
--
-- canonical/market_data/investor_flow_intraday(normalize_investor_estimate)를 담는 물리 테이블.
-- investor_flow_daily(ALPHA-385)와 **별도 테이블**이다 — 값이 벤더 가집계 추정(`*_fake_*`)이라
-- EOD 확정치와 어긋나고, 한 표에 섞으면 소비자가 어느 쪽을 본 것인지 사후에 구분할 수 없다
-- (.dev/etf-flow-collection-plan.md §2.5). 새 테이블은 계약이다(ADR-0005).
--
-- grain 은 **금융상품·거래일·슬롯**이다. KIS 는 하루 4~5 스냅샷을 주므로(외국인 09:30·11:20·
-- 13:20·14:30 / 기관 10:00·11:20·13:20·14:30) 일별 PK 를 그대로 베끼면 마지막 슬롯이 앞 슬롯을
-- 덮어 **장중 추이가 사라진다** — 이 데이터셋의 존재 이유가 그 추이다.
--
-- asof_slot 이 TEXT 인 이유: 원본 필드 `bsop_hour_gb` 의 도메인이 미관측이다("0930" 같은 시각인지
-- "1"~"5" 코드인지). TIME 으로 파싱하면 형식을 잘못 가정해 정체성 키를 오염시키는데, 이 소스는
-- 소급 재조회가 없어(날짜 파라미터 자체가 없다) 사후 정정이 불가하다. 실측으로 좁혀지기 전까지
-- 원문을 보존한다.
--
-- 컬럼이 수량 3개뿐인 이유: 벤더가 외국인·기관·합계의 가집계 **수량**만 준다. 개인·기관 세분·
-- 순매수 대금은 이 API 에 없다 — 없는 값을 위한 자리를 미리 파두지 않는다. `_est` 접미사는
-- 표면에서 잠정임이 읽히게 하는 장치다(EOD 확정 컬럼과 이름이 같으면 구분이 사라진다).
-- 셋 다 NOT NULL: canonical 이 셋 전부를 필수로 보장하고, 하나라도 없으면 그 행에 남는 관측이 없다.
--
-- BIGINT: canonical 이 int64 다. 순매수는 음수가 정상이라 부호 CHECK 를 걸지 않고, BIGINT 는
-- NaN/Inf 가 불가능해 유한성 CHECK 도 불필요하다(investor_flow_daily 와 같은 근거).

CREATE TABLE investor_flow_intraday (
    instrument_id           TEXT NOT NULL,
    trade_date              DATE NOT NULL,
    asof_slot               TEXT NOT NULL,  -- 벤더 원문(bsop_hour_gb) — 도메인 미관측이라 무변형 보존

    net_qty_foreign_est     BIGINT NOT NULL,  -- 외국인 추정 순매수 수량(주). 가집계=잠정
    net_qty_institution_est BIGINT NOT NULL,  -- 기관 추정(세분 없음 — 벤더 미제공)
    net_qty_total_est       BIGINT NOT NULL,  -- 벤더 가집계 합

    available_at            TIMESTAMPTZ NOT NULL,
    data_version            VARCHAR(50) NOT NULL,

    PRIMARY KEY (instrument_id, trade_date, asof_slot)
);

COMMENT ON TABLE investor_flow_intraday IS
'금융상품·거래일·슬롯 grain의 장중 투자자 추정 순매수(가집계). EOD 확정(investor_flow_daily)과 어긋날 수 있는 잠정치다.';

CREATE INDEX ix_investor_flow_intraday_trade_date ON investor_flow_intraday (trade_date);

ALTER TABLE investor_flow_intraday
    ADD CONSTRAINT fk_investor_flow_intraday_instrument
    FOREIGN KEY (instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE CASCADE;
