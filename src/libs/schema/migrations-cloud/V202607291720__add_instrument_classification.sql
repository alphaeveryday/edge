-- 금융상품 산업분류·시가총액 원장 신설 — 인과 설계 하네스의 준거집단 재료.
--
-- 왜 필요한가. 인과 귀속의 핵심 설계는 **비교군**이다: 원인이 닿는 집단과 닿지 않는
-- 집단을 갈라야 하고, 그 경계는 대개 산업이다("같은 날 같은 산업의 사건 없는 종목").
-- 지금 클라우드에는 그 경계를 그릴 컬럼이 없다 — entity 는 display_name 까지,
-- equity_profile 은 발행회사까지다. 그래서 준거집단을 만들 수 없고, 만들 수 없으면
-- 처치·대조 대비가 성립하지 않아 인과 주장 자체가 불가능하다.
--
-- 로컬에서 정규화해 둔 산업 맵(FMP: ticker·sector·industry·market_cap·상장시장)을
-- 클라우드로 올린다. 다른 원천이 붙을 수 있으므로 source 를 남긴다.
--
-- grain 은 **금융상품·분류시점**이다. 산업분류는 리밸런싱·재상장·사업재편으로 바뀌고,
-- 바뀐 뒤의 분류로 과거 준거집단을 만들면 미래를 보는 것이 된다. 그래서 단일행 마스터가
-- 아니라 as_of_date 를 키에 넣는다. 조회는 `as_of_date <= 대상일` 중 최신 1건이다.
--
-- sector·industry 를 코드가 아니라 원문 텍스트로 둔다. 준거집단은 문자열 동일성으로
-- 묶이고(같은 industry 인가), 우리는 아직 표준 분류체계(GICS 등)에 매핑하지 않았다.
-- 없는 매핑을 코드처럼 보이게 만들면 정합성을 가정하게 된다 — 원문임을 이름으로 밝힌다.
--
-- market_cap 은 균형검정(SMD)의 공변량이다. 통화는 instrument.currency_code 를 따른다 —
-- 여기 다시 두면 두 값이 갈라질 수 있다.
--
-- NOT NULL 은 instrument_id·as_of_date·source·available_at 에만 건다. 분류가 없는
-- 종목(신규상장·비상장 등)은 행이 없거나 sector/industry 가 null 인 것이 정상이고,
-- 여기에 NOT NULL 을 걸면 적재가 막힌다. 결측을 빈 문자열로 대체하지 않는다 —
-- '' 는 하나의 산업으로 묶여 준거집단을 조용히 오염시킨다.

CREATE TABLE instrument_classification (
    instrument_id        TEXT NOT NULL,
    as_of_date           DATE NOT NULL,
    sector_name          TEXT,
    industry_name        TEXT,
    market_cap           NUMERIC(30, 4),
    listing_market       VARCHAR(30),
    is_primary_share     BOOLEAN,
    source               VARCHAR(40) NOT NULL,
    available_at         TIMESTAMPTZ NOT NULL,
    data_version         VARCHAR(50) NOT NULL,

    PRIMARY KEY (instrument_id, as_of_date),

    CONSTRAINT ck_instrument_classification_market_cap
        CHECK (market_cap IS NULL
               OR (market_cap >= 0 AND market_cap < 'Infinity'::NUMERIC)),
    CONSTRAINT ck_instrument_classification_names_not_blank
        CHECK ((sector_name IS NULL OR btrim(sector_name) <> '')
               AND (industry_name IS NULL OR btrim(industry_name) <> ''))
);

COMMENT ON TABLE instrument_classification IS
'금융상품·분류시점 grain의 산업분류·시가총액 원장. 준거집단(비교군) 구성의 재료다. '
'sector_name·industry_name 은 원천 원문 텍스트이며 표준 분류체계 코드가 아니다.';

CREATE INDEX ix_instrument_classification_industry
    ON instrument_classification (industry_name, as_of_date);
CREATE INDEX ix_instrument_classification_sector
    ON instrument_classification (sector_name, as_of_date);
CREATE INDEX ix_instrument_classification_available_at
    ON instrument_classification (available_at);

ALTER TABLE instrument_classification
    ADD CONSTRAINT fk_instrument_classification_instrument
    FOREIGN KEY (instrument_id)
    REFERENCES instrument (instrument_id)
    ON DELETE CASCADE;
