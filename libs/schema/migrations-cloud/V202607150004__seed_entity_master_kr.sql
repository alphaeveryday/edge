-- ============================================================================
-- 엔터티 마스터 시드 — KR 수집 유니버스 9종목 + KODEX 반도체
--
-- 왜 시드가 스키마에 있나: assertion_argument.entity_id 가 NOT NULL + FK → entity
-- ON DELETE RESTRICT 다. 즉 태깅의 핵심 산출물인 '역할'을 한 건이라도 적재하려면 entity 가
-- 먼저 존재해야 한다(disclosure_document.issuer_actor_id 도 같은 이유로 actor 를 요구).
-- 이 마스터는 '이 회사·이 종목이 존재한다' 수준의 참조 데이터라 구성종목 비중처럼 자주
-- 변하지 않는다. 그래서 별도 로더 없이 마이그레이션으로 둔다.
--
-- ID 체계는 ADR-0027 — <타입접두사>_<ULID> 불투명 서로게이트. 외부 식별자(ticker)는 ID 가
-- 아니라 속성이다. 티커가 바뀌면 instrument.ticker 만 UPDATE 하면 되고 이 ID 를 참조하는
-- FK 는 전부 살아 있다. market_code 는 MIC(ISO 10383) — KRX 유가증권시장 = XKRX.
--
-- entity 와 서브타입은 같은 ID 값을 쓴다 — FK 가 (actor_id, entity_type) →
-- (entity_id, entity_type) 라서 값이 같아야 한다.
--
-- 유니버스 출처: data-pipeline config/sources.toml 의 [targets].symbols 중 KR 9 종목
-- (= bigkinds_news.query_map 의 한국어 회사명) + krx_etf.etf_map 의 091160.
-- US 9 종목은 넣지 않는다 — ADR-0024 가 MVP 를 국내 ETF 로 확정했고 회사명 출처도 없다.
--
-- company_profile 은 선택이 아니다 — equity_profile.issuer_actor_id 와
-- disclosure_document.issuer_actor_id 의 FK 가 actor 가 아니라 **company_profile(actor_id)** 을
-- 가리킨다. 그래서 회사마다 company_profile 행이 있어야 주식도 공시도 붙는다.
-- dart_corp_code 는 OpenDART corpCode.xml 라이브 조회로 채웠다(2026-07-15, 118,490건 중 9/9
-- 매칭, DART corp_name 이 위 config 회사명과 전부 일치). 이게 공시 적재의 조인 키다.
--
-- 범위 밖(의도)
--   * etf_profile — etf_type VARCHAR(40) NOT NULL 인데 허용 어휘가 CHECK·문서 어디에도 없다
--     (ALPHA-361 modality 와 동종 결함). 값을 발명하면 그게 계약이 되므로 넣지 않는다.
--     instrument(ETF) 행만으로 이 ETF 를 참조하는 FK 는 전부 풀린다.
--   * KODEX 반도체 구성종목 35종 — 레이크에 KRX 데이터가 없다(ALPHA-336 이 SFN 미편입이라
--     실행된 적 없음). 상위 2종(삼성전자·SK하이닉스 = 60.96%)이 아래 9종에 이미 들어 있다.
--
-- 확장-수축 단계: 순수 확장(신규 행 INSERT). 기존 행을 건드리지 않는다.
--
-- Refs: ALPHA-362
-- ============================================================================

SET search_path TO public;

-- 회사(actor) — entity 서브타입 ACTOR
INSERT INTO entity (entity_id, entity_type, display_name, status) VALUES
    ('actor_01KXJB6W2EF90P8Z5BNQ9EH9D9', 'ACTOR', '삼성전자',       'ACTIVE'),
    ('actor_01KXJB6W2E1FNS0VV6V4TG8Z0P', 'ACTOR', 'SK하이닉스',     'ACTIVE'),
    ('actor_01KXJB6W2EVDH4RJ59935GP13M', 'ACTOR', '삼성전기',       'ACTIVE'),
    ('actor_01KXJB6W2EHW4C91VEHR0PNH1Q', 'ACTOR', 'SK스퀘어',       'ACTIVE'),
    ('actor_01KXJB6W2EWVPQTH37X4MD8TQQ', 'ACTOR', 'LG에너지솔루션', 'ACTIVE'),
    ('actor_01KXJB6W2E75T2PRNQAHRBC7YT', 'ACTOR', '삼성물산',       'ACTIVE'),
    ('actor_01KXJB6W2ENXWFX8ZVX3SXYM1S', 'ACTOR', '삼성생명',       'ACTIVE'),
    ('actor_01KXJB6W2EEBSXNR7R8N148QXE', 'ACTOR', 'KB금융',         'ACTIVE'),
    ('actor_01KXJB6W2ES8EZXW6AVYY289YK', 'ACTOR', '신한지주',       'ACTIVE');

INSERT INTO actor (actor_id, actor_type, country_code) VALUES
    ('actor_01KXJB6W2EF90P8Z5BNQ9EH9D9', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2E1FNS0VV6V4TG8Z0P', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2EVDH4RJ59935GP13M', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2EHW4C91VEHR0PNH1Q', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2EWVPQTH37X4MD8TQQ', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2E75T2PRNQAHRBC7YT', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2ENXWFX8ZVX3SXYM1S', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2EEBSXNR7R8N148QXE', 'COMPANY', 'KR'),
    ('actor_01KXJB6W2ES8EZXW6AVYY289YK', 'COMPANY', 'KR');

-- 회사 상세 — equity_profile·disclosure_document 의 issuer FK 가 이 테이블을 가리킨다.
-- profile_as_of_date 는 corpCode.xml 을 조회한 날이다(이 매핑이 참인 시점).
INSERT INTO company_profile (actor_id, dart_corp_code, profile_as_of_date) VALUES
    ('actor_01KXJB6W2EF90P8Z5BNQ9EH9D9', '00126380', DATE '2026-07-15'),  -- 삼성전자
    ('actor_01KXJB6W2E1FNS0VV6V4TG8Z0P', '00164779', DATE '2026-07-15'),  -- SK하이닉스
    ('actor_01KXJB6W2EVDH4RJ59935GP13M', '00126371', DATE '2026-07-15'),  -- 삼성전기
    ('actor_01KXJB6W2EHW4C91VEHR0PNH1Q', '01596425', DATE '2026-07-15'),  -- SK스퀘어
    ('actor_01KXJB6W2EWVPQTH37X4MD8TQQ', '01515323', DATE '2026-07-15'),  -- LG에너지솔루션
    ('actor_01KXJB6W2E75T2PRNQAHRBC7YT', '00149655', DATE '2026-07-15'),  -- 삼성물산
    ('actor_01KXJB6W2ENXWFX8ZVX3SXYM1S', '00126256', DATE '2026-07-15'),  -- 삼성생명
    ('actor_01KXJB6W2EEBSXNR7R8N148QXE', '00688996', DATE '2026-07-15'),  -- KB금융
    ('actor_01KXJB6W2ES8EZXW6AVYY289YK', '00382199', DATE '2026-07-15');  -- 신한지주

-- 주식(instrument/EQUITY) — 회사와 별개 엔터티다. display_name 에 '보통주'를 붙여
-- 발행회사(actor)와 눈으로 구분되게 한다(우선주는 별도 instrument 가 된다).
INSERT INTO entity (entity_id, entity_type, display_name, status) VALUES
    ('inst_01KXJB6W2EFQRP1D5TBRF0EBEK', 'INSTRUMENT', '삼성전자 보통주',       'ACTIVE'),
    ('inst_01KXJB6W2ERJ3HAQFK2H8Y8AKX', 'INSTRUMENT', 'SK하이닉스 보통주',     'ACTIVE'),
    ('inst_01KXJB6W2E4SPQ09HX6VH5XRSM', 'INSTRUMENT', '삼성전기 보통주',       'ACTIVE'),
    ('inst_01KXJB6W2EX1AZB27YQ2XGBGW4', 'INSTRUMENT', 'SK스퀘어 보통주',       'ACTIVE'),
    ('inst_01KXJB6W2EMGWVBG9MM9YCGR0R', 'INSTRUMENT', 'LG에너지솔루션 보통주', 'ACTIVE'),
    ('inst_01KXJB6W2ECXEKRAW7AF5VNV6M', 'INSTRUMENT', '삼성물산 보통주',       'ACTIVE'),
    ('inst_01KXJB6W2EX7JBB6CBYDYAKK7E', 'INSTRUMENT', '삼성생명 보통주',       'ACTIVE'),
    ('inst_01KXJB6W2EBWE03CE464MBDFZB', 'INSTRUMENT', 'KB금융 보통주',         'ACTIVE'),
    ('inst_01KXJB6W2ENJ8Q1Y3KJX849K65', 'INSTRUMENT', '신한지주 보통주',       'ACTIVE'),
    ('inst_01KXJB6W2EFJF0AGPMWG967ZSZ', 'INSTRUMENT', 'KODEX 반도체',          'ACTIVE');

INSERT INTO instrument (instrument_id, market_code, ticker, instrument_type, currency_code) VALUES
    ('inst_01KXJB6W2EFQRP1D5TBRF0EBEK', 'XKRX', '005930', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2ERJ3HAQFK2H8Y8AKX', 'XKRX', '000660', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2E4SPQ09HX6VH5XRSM', 'XKRX', '009150', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2EX1AZB27YQ2XGBGW4', 'XKRX', '402340', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2EMGWVBG9MM9YCGR0R', 'XKRX', '373220', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2ECXEKRAW7AF5VNV6M', 'XKRX', '028260', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2EX7JBB6CBYDYAKK7E', 'XKRX', '032830', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2EBWE03CE464MBDFZB', 'XKRX', '105560', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2ENJ8Q1Y3KJX849K65', 'XKRX', '055550', 'EQUITY', 'KRW'),
    ('inst_01KXJB6W2EFJF0AGPMWG967ZSZ', 'XKRX', '091160', 'ETF',    'KRW');

-- 주식 → 발행회사 연결. 9종 모두 보통주다(우선주는 수집 유니버스에 없다).
INSERT INTO equity_profile (instrument_id, issuer_actor_id, share_class_code) VALUES
    ('inst_01KXJB6W2EFQRP1D5TBRF0EBEK', 'actor_01KXJB6W2EF90P8Z5BNQ9EH9D9', 'COMMON'),
    ('inst_01KXJB6W2ERJ3HAQFK2H8Y8AKX', 'actor_01KXJB6W2E1FNS0VV6V4TG8Z0P', 'COMMON'),
    ('inst_01KXJB6W2E4SPQ09HX6VH5XRSM', 'actor_01KXJB6W2EVDH4RJ59935GP13M', 'COMMON'),
    ('inst_01KXJB6W2EX1AZB27YQ2XGBGW4', 'actor_01KXJB6W2EHW4C91VEHR0PNH1Q', 'COMMON'),
    ('inst_01KXJB6W2EMGWVBG9MM9YCGR0R', 'actor_01KXJB6W2EWVPQTH37X4MD8TQQ', 'COMMON'),
    ('inst_01KXJB6W2ECXEKRAW7AF5VNV6M', 'actor_01KXJB6W2E75T2PRNQAHRBC7YT', 'COMMON'),
    ('inst_01KXJB6W2EX7JBB6CBYDYAKK7E', 'actor_01KXJB6W2ENXWFX8ZVX3SXYM1S', 'COMMON'),
    ('inst_01KXJB6W2EBWE03CE464MBDFZB', 'actor_01KXJB6W2EEBSXNR7R8N148QXE', 'COMMON'),
    ('inst_01KXJB6W2ENJ8Q1Y3KJX849K65', 'actor_01KXJB6W2ES8EZXW6AVYY289YK', 'COMMON');
