-- ============================================================================
-- etf_profile.etf_type — NOT NULL 해제
--
-- 이 컬럼은 VARCHAR(40) NOT NULL 인데 **허용 어휘가 정의된 적이 없다.** CHECK 도, 문서도,
-- ADR 도 없다(V202607150001 도입 이후 전 마이그레이션 grep 0건). 값을 강제하면서 무엇이
-- 합법적인 값인지는 아무도 모르는 상태라, 이 제약은 만족 불가능하다.
--
-- ALPHA-361(document_assertion.modality_code, V202607150003)과 **동종 결함**이고, 이번엔
-- 하나가 아니라 **두 적재를 동시에** 막는다 — etf_holding_snapshot 과 etf_nav_daily 가 모두
-- etf_profile(instrument_id) 를 FK 로 참조하므로, etf_profile 행을 못 만들면 둘 다 INSERT
-- 불가다. 실제로 KODEX ETF 는 instrument(instrument_type='ETF') 행만 있고 프로필 행이 없다.
--
-- 어휘를 여기서 발명하지 않는 이유: 임의 값(INDEX·EQUITY·UNKNOWN 류)을 적재하기 시작하면
-- 그게 사실상 계약이 되고, 나중에 진짜 분류가 확정될 때 이미 적재된 행이 전부 오염된다.
-- ETF 분류(지수추종·섹터/테마·레버리지/인버스·액티브…)는 도메인 계약이라 소유자 합의가
-- 필요하고, 정하면 되돌리기 비싸다. 그래서 제약만 풀고 값은 비워 둔다.
--
-- 계약 확정 전까지 NULL 의 의미 = 'ETF 유형을 아직 분류하지 않음'(값이 없다는 사실 자체).
-- 어휘가 확정되면 CHECK 추가 + NOT NULL 복원을 별도 마이그레이션으로 낸다 (그 시점엔 적재된
-- 행의 백필이 선행).
--
-- CHECK 를 지금 걸지 않는 건 V202607150001 의 명명 규약과도 일치한다
--   — "codes/status/version values: VARCHAR with CHECK constraints **where stable**".
--   etf_type 은 stable 하지 않다(미정의).
--
-- V202607150001 을 그 자리에서 고치지 않는 이유: schema-migrate CD 가 dev RDS 에 이미
--   적용해 체크섬이 깨진다(ALPHA-361 과 동일 제약). 그래서 신규 마이그레이션으로 낸다.
--
-- 확장-수축 단계: 순수 확장(제약 완화). NOT NULL 해제는 기존 writer 를 깨뜨리지 않으며,
--   애초에 이 테이블에는 writer 코드가 존재한 적이 없다 (V202607150003 과 동일 근거).
--
-- Refs: ALPHA-378 (해제 대상 = ALPHA-379 구성종목 적재 · ALPHA-383 NAV 적재)
-- ============================================================================

SET search_path TO public;

ALTER TABLE etf_profile
    ALTER COLUMN etf_type DROP NOT NULL;

COMMENT ON COLUMN etf_profile.etf_type IS
'ETF 유형 분류. 허용 어휘 미확정(ALPHA-378) — 계약이 확정될 때까지 NULL 을 허용하며 임의 값을 생성하지 않는다. 어휘 확정 시 CHECK 추가 + NOT NULL 복원 예정.';
