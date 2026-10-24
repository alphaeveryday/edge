-- 구성종목 스냅샷 상태에 원천 현금 행의 비중 합을 더한다(ALPHA-1244).
-- 적재는 주식 행만 etf_holding_snapshot 에 싣고 현금 행은 버린다. 현금이 음수(커버드콜·차입)이거나
-- 반올림이 겹치면 주식만의 합이 1을 넘는데, 분석 엔진은 그 초과분을 설명할 값이 없었다(ALPHA-1162).
-- 행이 아니라 상태 열인 이유: etf_holding_snapshot 은 구성종목 FK·비중 0..1 CHECK 가 있고 읽는 곳이 여럿이다.
-- 확장 단계다 — NULL 허용 컬럼만 더하므로 기존 reader·writer 는 그대로 돈다. 채우는 코드는 후속 PR 이다.
-- 분석 워커와 가격 판정이 매 실행 이 표를 읽으므로 락 대기 상한을 건다.
SET LOCAL lock_timeout = '5s';

ALTER TABLE etf_holding_snapshot_status
    ADD COLUMN cash_weight_ratio NUMERIC
        CONSTRAINT ck_etf_holding_snapshot_status_cash_finite
        CHECK (cash_weight_ratio > '-Infinity'::NUMERIC AND cash_weight_ratio < 'Infinity'::NUMERIC);

COMMENT ON COLUMN etf_holding_snapshot_status.cash_weight_ratio IS
'canonical 현금(CASH) 행 비중의 합(비율, 음수 가능). NULL = 확인하지 못함(자산유형이 없는 2026-08-24 이전 파티션 등). 현금 행 자체는 etf_holding_snapshot 에 적재하지 않는다.';
