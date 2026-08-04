-- 분석 관측 계보에 분봉 트리거 축 추가 (ALPHA-709, expand-only).
--
-- etf_contribution_observation 은 일 단위 price_movement_trigger 로 NOT NULL FK 였다 —
-- 분봉 트리거(minute_price_trigger, ALPHA-708) 소비를 영속하려면 FK 위반이라 막힌다.
-- 정본을 바꾸지 않고 **축을 병렬 추가**한다: 두 입력은 파생 관계가 아니라 서로 다른
-- 판정(일수익률 proxy vs 장중 시가 대비)이고, 관측 한 행은 정확히 한 트리거에서 온다.
ALTER TABLE etf_contribution_observation
    ALTER COLUMN price_movement_trigger_id DROP NOT NULL;

ALTER TABLE etf_contribution_observation
    ADD COLUMN minute_price_trigger_id TEXT;

ALTER TABLE etf_contribution_observation
    ADD CONSTRAINT fk_etf_contribution_minute_trigger
    FOREIGN KEY (minute_price_trigger_id)
    REFERENCES minute_price_trigger (trigger_id);

-- 관측은 정확히 한 트리거에서 온다 — 둘 다 NULL(근거 없는 관측)도 둘 다 값(이중
-- 계보)도 막는다. 기존 행은 전부 일 단위 값이 있어 그대로 통과한다.
ALTER TABLE etf_contribution_observation
    ADD CONSTRAINT ck_etf_contribution_one_trigger
    CHECK (num_nonnulls(price_movement_trigger_id, minute_price_trigger_id) = 1);

-- 일 단위 UNIQUE(price_movement_trigger_id)와 대칭 — 같은 분봉 트리거의 관측은 한 행
-- (재실행 멱등의 근거이기도 하다: stable_id("cob", trigger_id) 가 같은 행을 다시 쓴다)
CREATE UNIQUE INDEX uq_etf_contribution_minute_trigger
    ON etf_contribution_observation (minute_price_trigger_id)
    WHERE minute_price_trigger_id IS NOT NULL;

COMMENT ON COLUMN etf_contribution_observation.minute_price_trigger_id IS
'분봉 트리거 축 (ALPHA-709). price_movement_trigger_id(일 단위)와 정확히 하나만 값을 갖는다 — 축이 다른 두 입력이지 정본 이원화가 아니다.';
