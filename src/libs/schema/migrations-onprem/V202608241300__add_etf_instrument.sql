-- ============================================================================
-- 온프렘 종목 마스터 — etf_instrument
--
-- publication-api 의 상장 여부 판별(404)을 설정 allowlist(PUBLICATION_KNOWN_TICKERS)에서
-- DB 로 옮기는 기반. 행 존재 = 상장. 데이터 소유는 증권사 환경이다 — 파이프라인이
-- 동기화하지 않고, 증권사 내부 종목 마스터에서 채워지는 것을 전제한다(데모는 시드).
-- cloud etf_instrument_id 를 두지 않는 이유: 증권사 마스터는 cloud 발번 ID 를 모른다.
-- ============================================================================

SET search_path TO public;

CREATE TABLE etf_instrument (
    etf_ticker  VARCHAR(20) PRIMARY KEY,
    etf_name    TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE etf_instrument IS
'온프렘 종목 마스터 — 행 존재 = 상장(publication-api 404 판별 소스). 증권사 환경 소유 데이터, 파이프라인 미동기화(로컬/데모는 seed-local-onprem 시드).';
