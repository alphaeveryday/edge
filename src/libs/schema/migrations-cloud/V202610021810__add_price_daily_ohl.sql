-- 일별 가격 원장에 시가·고가·저가를 추가한다(ALPHA-1148).
-- canonical price_daily 는 시가·고가·저가를 이미 싣는데 이 표에 컬럼이 없어 DB 로 옮기지 못했다.
-- 확장 단계다 — NULL 허용 컬럼만 더하므로 기존 reader·writer 는 그대로 돈다. 채우는 코드는 후속 PR 이다.
-- price_daily 는 장중 분 레인이 전일 종가를 읽는 표라 락 대기 상한을 건다(README "락 예산").
SET LOCAL lock_timeout = '3s';

ALTER TABLE price_daily
    ADD COLUMN open_price NUMERIC(24, 8),
    ADD COLUMN high_price NUMERIC(24, 8),
    ADD COLUMN low_price  NUMERIC(24, 8);

-- ck_price_daily_values 의 종가와 같은 규칙(양수·유한)만 건다. 고가 >= 저가 같은 열 사이 관계는
-- 걸지 않는다 — 과거 이력 원천(DataGuide 2006-10~ 869만 행)에 종가가 고가·저가 범위를 벗어난
-- 행이 204건 있고(거래량 없는 날, 2026-10-02 실측), 그 값을 원장이 거부하면 그날 종가까지 잃는다.
ALTER TABLE price_daily
    ADD CONSTRAINT ck_price_daily_ohl
        CHECK (
            (open_price IS NULL OR (open_price > 0 AND open_price < 'Infinity'::NUMERIC))
            AND (high_price IS NULL OR (high_price > 0 AND high_price < 'Infinity'::NUMERIC))
            AND (low_price IS NULL OR (low_price > 0 AND low_price < 'Infinity'::NUMERIC))
        );

COMMENT ON COLUMN price_daily.open_price IS '시가(원주가). 수정주가 아님.';
COMMENT ON COLUMN price_daily.high_price IS '고가(원주가). 수정주가 아님.';
COMMENT ON COLUMN price_daily.low_price IS '저가(원주가). 수정주가 아님.';
