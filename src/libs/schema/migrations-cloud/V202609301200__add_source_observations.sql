-- 분석 v2 원천 관측(ALPHA-1130): 매크로 5계열·재무 4지표·KIS 지수업종 분류.
-- 계약 정본은 docs/design/etf-data-storage-plan.md §10 이다. 여기 주석은 그 계약을 DB가 강제하는 지점만 적는다.
--
-- 세 테이블 모두 **추가 전용 판본 이력**이다. 한 행 = 한 수집 실행(raw_run_id)이 받은 한 관측의 판본.
-- 적재는 ON CONFLICT DO NOTHING 이라 같은 실행을 다시 적재해도 행이 늘지 않고, 늦게 끝난 옛 실행은
-- 자기 received_at 으로 이력의 제자리에 들어간다(최신값을 덮지 않는다). 기준시각 T 조회는
-- "available_at <= T 인 판본 중 가장 늦게 보인 것"이고, 아래 *_as_of 함수가 그 규칙의 정본이다.
--
-- 시각 네 축을 섞지 않는다:
--   observation_date/period_end  관측·회계 기간(원천 값)
--   rcept_date                   공급자 공개일(DART 접수일, 날짜만. 시각을 지어내지 않는다)
--   received_at                  EDGE 가 실제로 받은 시각(raw 실행)
--   available_at                 분석 가시시각. basis=received 면 received_at 과 같고,
--                                basis=provider_release_date 면 min(received_at, 공개일 다음날 00:00 KST)
-- 매크로·업종은 공급자가 공개시각을 주지 않아 basis=received 만 허용한다(2026-09-30 결정).
SET LOCAL lock_timeout = '3s';

CREATE TABLE macro_observation (
    series_id          TEXT NOT NULL,
    observation_date   DATE NOT NULL,          -- 일별=관측일, 월별(kr_cpi_yoy)=기준월 1일
    value              NUMERIC NOT NULL,       -- 공급자 소수 자릿수 그대로(반올림하지 않는다)
    unit               TEXT NOT NULL,
    source_vendor      TEXT NOT NULL,
    source_series      TEXT NOT NULL,          -- 공급자 계열 식별자(예 ECOS 817Y002/D/010210000)
    received_at        TIMESTAMPTZ NOT NULL,
    available_at       TIMESTAMPTZ NOT NULL,
    availability_basis TEXT NOT NULL,
    raw_run_id         TEXT NOT NULL,
    raw_key            TEXT NOT NULL,
    raw_sha256         TEXT NOT NULL,
    canonical_run_id   TEXT NOT NULL,
    artifact_key       TEXT NOT NULL,
    artifact_sha256    TEXT NOT NULL,
    loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, observation_date, raw_run_id),
    -- 계열·단위·공급자·공급자 계열 식별자는 한 묶음이다. 단위가 다른 값이 같은 계열에 섞이면 비교 툴의 %p·% 판정이
    -- 틀리고, 같은 공급자의 다른 계열(예 ECOS 0000013 = 02:00 종가)이 설정 실수로 들어오면 적재에서 막혀야 한다.
    -- 문자열은 sources/macro_series.py SERIES 의 source_series 와 같다(계열을 바꾸면 여기도 바꾼다 — 그게 의도다).
    CONSTRAINT ck_macro_observation_series CHECK ((series_id, unit, source_vendor, source_series) IN (
        ('usd_krw', 'KRW_per_USD', 'ecos', 'ECOS 731Y003/D/0000003'),
        ('us_10y_yield', 'percent', 'fmp', 'FMP treasury-rates year10'),
        ('kr_10y_yield', 'percent', 'ecos', 'ECOS 817Y002/D/010210000'),
        ('kr_cpi_yoy', 'percent', 'kosis', 'KOSIS 101/DT_1J22042 T03 objL1=0'),
        ('brent_spot_usd', 'USD_per_barrel', 'eia', 'EIA petroleum/pri/spt RBRTE'))),
    CONSTRAINT ck_macro_observation_month CHECK (
        series_id <> 'kr_cpi_yoy' OR extract(day FROM observation_date) = 1),
    CONSTRAINT ck_macro_observation_price CHECK (
        series_id NOT IN ('usd_krw', 'brent_spot_usd') OR value > 0),
    -- 유한성 관용구(V202607150001 주석): NaN 은 PostgreSQL 에서 최댓값이라 부등호 하나로는 안 막힌다.
    CONSTRAINT ck_macro_observation_finite CHECK (
        value > '-Infinity'::NUMERIC AND value < 'Infinity'::NUMERIC),
    CONSTRAINT ck_macro_observation_basis CHECK (
        availability_basis = 'received' AND available_at = received_at),
    -- 관측 기간이 끝나기 전에 받은 값(진행 중 세션·미완 월)은 확정 관측이 아니다.
    CONSTRAINT ck_macro_observation_complete CHECK (
        CASE WHEN series_id = 'kr_cpi_yoy'
             THEN (observation_date + INTERVAL '1 month')::date <= (received_at AT TIME ZONE 'Asia/Seoul')::date
             ELSE observation_date < (received_at AT TIME ZONE 'Asia/Seoul')::date END),
    CONSTRAINT ck_macro_observation_sha CHECK (
        raw_sha256 ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_macro_observation_visible ON macro_observation (series_id, available_at);

COMMENT ON TABLE macro_observation IS
'매크로 원천 관측의 추가 전용 판본 이력(ALPHA-1130). 한 행=한 수집 실행이 받은 한 관측일 값. 현재값·시점 조회는 macro_observations_as_of 를 쓴다.';

CREATE TABLE financial_metric (
    corp_code          TEXT NOT NULL,          -- DART 고유번호 8자리
    instrument_code    TEXT NOT NULL,          -- KRX 단축코드 6자리
    fiscal_year        SMALLINT NOT NULL,
    fiscal_period      TEXT NOT NULL,          -- Q1·Q2·Q3·Q4(분기·기말 시점)·FY(연간 누적)
    period_end         DATE NOT NULL,
    metric             TEXT NOT NULL,
    period_kind        TEXT NOT NULL,          -- QUARTER=해당 3개월, CUMULATIVE=사업연도 개시~기말, POINT=기말 시점
    fs_basis           TEXT NOT NULL,          -- CFS 연결 · OFS 별도
    derivation         TEXT NOT NULL,          -- REPORTED 공시 원값 · FY_MINUS_9M · EQUITY_OVER_SHARES
    value              NUMERIC NOT NULL,       -- 원값은 공급자 자릿수 그대로, BPS 는 소수 6자리 반올림(formula 에 명시)
    unit               TEXT NOT NULL,
    formula            TEXT,
    inputs             JSONB NOT NULL,         -- 계산·원값의 근거 줄(접수번호·보고서·계정·금액 필드·값)
    rcept_no           TEXT NOT NULL,          -- 이 값의 공개를 정한 접수번호(유도값은 입력 중 가장 늦은 것)
    rcept_date         DATE,                   -- DART 공시목록 접수일. 목록에서 확인 못 하면 NULL
    received_at        TIMESTAMPTZ NOT NULL,
    available_at       TIMESTAMPTZ NOT NULL,
    availability_basis TEXT NOT NULL,
    raw_run_id         TEXT NOT NULL,
    raw_key            TEXT NOT NULL,
    raw_sha256         TEXT NOT NULL,
    canonical_run_id   TEXT NOT NULL,
    artifact_key       TEXT NOT NULL,
    artifact_sha256    TEXT NOT NULL,
    loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corp_code, fiscal_year, fiscal_period, metric, period_kind, fs_basis, raw_run_id),
    CONSTRAINT ck_financial_metric_codes CHECK (
        corp_code ~ '^[0-9]{8}$' AND instrument_code ~ '^[0-9A-Z]{6}$' AND rcept_no ~ '^[0-9]{14}$'),
    CONSTRAINT ck_financial_metric_inputs CHECK (jsonb_typeof(inputs) = 'array'),   -- 근거 줄 목록(정제가 쓰는 형태)
    CONSTRAINT ck_financial_metric_period CHECK (fiscal_period IN ('Q1', 'Q2', 'Q3', 'Q4', 'FY')),
    CONSTRAINT ck_financial_metric_finite CHECK (
        value > '-Infinity'::NUMERIC AND value < 'Infinity'::NUMERIC),
    -- 12월 결산만 싣는다(수집이 비12월 결산을 unsupported_reports 로 거른다) — 기말은 회계연도·기간에서 정해진다.
    CONSTRAINT ck_financial_metric_period_end CHECK (period_end = CASE fiscal_period
        WHEN 'Q1' THEN make_date(fiscal_year, 3, 31) WHEN 'Q2' THEN make_date(fiscal_year, 6, 30)
        WHEN 'Q3' THEN make_date(fiscal_year, 9, 30) ELSE make_date(fiscal_year, 12, 31) END),
    -- 지표·단위·기간 종류는 한 쌍이다. 누적·분기·시점 값이 한 열에서 섞이지 않게 DB가 막는다.
    CONSTRAINT ck_financial_metric_shape CHECK ((metric, unit, period_kind) IN (
        ('eps_basic', 'KRW_per_share', 'QUARTER'), ('eps_basic', 'KRW_per_share', 'CUMULATIVE'),
        ('eps_diluted', 'KRW_per_share', 'QUARTER'), ('eps_diluted', 'KRW_per_share', 'CUMULATIVE'),
        ('revenue', 'KRW', 'QUARTER'), ('revenue', 'KRW', 'CUMULATIVE'),
        ('operating_income', 'KRW', 'QUARTER'), ('operating_income', 'KRW', 'CUMULATIVE'),
        ('bps', 'KRW_per_share', 'POINT'), ('bps_total_shares', 'KRW_per_share', 'POINT'))),
    CONSTRAINT ck_financial_metric_fy CHECK (fiscal_period <> 'FY' OR period_kind = 'CUMULATIVE'),
    CONSTRAINT ck_financial_metric_basis CHECK (fs_basis IN ('CFS', 'OFS')),
    -- 공시 원값에 없는 Q4 3개월 값은 유도만 가능하고, 유도는 그 형태로만 존재한다.
    CONSTRAINT ck_financial_metric_derivation CHECK (
        (derivation = 'REPORTED' AND metric NOT IN ('bps', 'bps_total_shares')
            AND NOT (fiscal_period = 'Q4' AND period_kind = 'QUARTER'))
        OR (derivation = 'FY_MINUS_9M' AND fiscal_period = 'Q4' AND period_kind = 'QUARTER'
            AND formula IS NOT NULL)
        OR (derivation = 'EQUITY_OVER_SHARES' AND metric IN ('bps', 'bps_total_shares') AND formula IS NOT NULL)),
    CONSTRAINT ck_financial_metric_availability CHECK (
        (availability_basis = 'received' AND available_at = received_at)
        OR (availability_basis = 'provider_release_date' AND rcept_date IS NOT NULL
            AND available_at = LEAST(received_at,
                ((rcept_date + 1)::timestamp AT TIME ZONE 'Asia/Seoul')))),
    CONSTRAINT ck_financial_metric_sha CHECK (
        raw_sha256 ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_financial_metric_visible ON financial_metric (instrument_code, available_at);

COMMENT ON TABLE financial_metric IS
'DART 정기보고서 재무 지표의 추가 전용 판본 이력(ALPHA-1130). 공시 원값과 유도값(Q4=FY−9M·BPS)을 derivation 으로 가른다. 분기 조회는 financial_quarters_as_of 를 쓴다.';
COMMENT ON COLUMN financial_metric.available_at IS
'provider_release_date: DART 접수일 다음날 00:00 KST(시각 미제공 — 날짜 경계만)와 실제 수신 중 이른 쪽. received: 접수일을 확인 못 한 판본은 수신시각부터 보인다.';

-- 보고서 판본(ALPHA-1130 §10.4): 회사·연도·보고서·연결/별도 × 수집 실행마다 한 줄. 지표 행과 독립이라
-- "확인했지만 쓸 지표 없음"(CONFIRMED, metrics=[])·"일부만"(rejected≠[])·"확정 못 함"(UNCONFIRMED — HTTP 오류·
-- 파손·다른 보고서 응답)이 갈린다. 진행 중·중단된 실행은 여기 없다. 조회는 최신 CONFIRMED 판본만 권위로 본다.
CREATE TABLE financial_report_version (
    corp_code          TEXT NOT NULL,
    instrument_code    TEXT NOT NULL,
    fiscal_year        SMALLINT NOT NULL,
    reprt_code         TEXT NOT NULL,          -- 11013·11012·11014·11011
    report_period      TEXT NOT NULL,          -- Q1·Q2·Q3·Q4(사업보고서 — Q4 유도 행의 판본)
    fs_basis           TEXT NOT NULL,
    status             TEXT NOT NULL,          -- CONFIRMED·UNCONFIRMED
    rcept_no           TEXT,                   -- 응답이 실은 접수번호(확정 판본만)
    rcept_date         DATE,
    metrics            JSONB NOT NULL,         -- 이 실행이 만든 지표 "metric/period_kind/fiscal_period" 목록
    rejected           JSONB NOT NULL,         -- 만들지 못한 지표와 사유 [{metric, reasons}]
    detail             JSONB NOT NULL,         -- {statement, statement_detail, shares, shares_detail}
    received_at        TIMESTAMPTZ NOT NULL,
    available_at       TIMESTAMPTZ NOT NULL,
    availability_basis TEXT NOT NULL,
    raw_run_id         TEXT NOT NULL,
    raw_key            TEXT NOT NULL,
    raw_sha256         TEXT NOT NULL,
    canonical_run_id   TEXT NOT NULL,
    artifact_key       TEXT NOT NULL,
    artifact_sha256    TEXT NOT NULL,
    loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corp_code, fiscal_year, reprt_code, fs_basis, raw_run_id),
    CONSTRAINT ck_financial_report_version_codes CHECK (
        corp_code ~ '^[0-9]{8}$' AND instrument_code ~ '^[0-9A-Z]{6}$'
        AND (rcept_no IS NULL OR rcept_no ~ '^[0-9]{14}$')),
    CONSTRAINT ck_financial_report_version_report CHECK (
        (reprt_code, report_period) IN (('11013', 'Q1'), ('11012', 'Q2'), ('11014', 'Q3'), ('11011', 'Q4'))),
    CONSTRAINT ck_financial_report_version_status CHECK (status IN ('CONFIRMED', 'UNCONFIRMED')),
    CONSTRAINT ck_financial_report_version_basis CHECK (fs_basis IN ('CFS', 'OFS')),
    CONSTRAINT ck_financial_report_version_json CHECK (
        jsonb_typeof(metrics) = 'array' AND jsonb_typeof(rejected) = 'array' AND jsonb_typeof(detail) = 'object'),
    CONSTRAINT ck_financial_report_version_availability CHECK (
        (availability_basis = 'received' AND available_at = received_at)
        OR (availability_basis = 'provider_release_date' AND rcept_date IS NOT NULL
            AND available_at = LEAST(received_at,
                ((rcept_date + 1)::timestamp AT TIME ZONE 'Asia/Seoul')))),
    CONSTRAINT ck_financial_report_version_sha CHECK (
        raw_sha256 ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_financial_report_version_visible ON financial_report_version (instrument_code, available_at);

COMMENT ON TABLE financial_report_version IS
'DART 정기보고서 응답의 실행별 판본(ALPHA-1130). 지표 행(financial_metric)과 독립 — 지표가 0개여도 확인된 실행은 한 줄이 남고, 확정 못 한 실행(UNCONFIRMED)은 옛 확정값을 무효화하지 않는다. 지표 행과 같은 트랜잭션으로 적재된다.';

CREATE TABLE sector_classification (
    market             TEXT NOT NULL,          -- KOSPI·KOSDAQ(KIS 종목 마스터 파일 구분)
    instrument_code    TEXT NOT NULL,          -- KIS 마스터 단축코드
    standard_code      TEXT NOT NULL,          -- 표준코드(ISIN)
    name_kr            TEXT NOT NULL,
    security_group     TEXT NOT NULL,          -- 증권그룹구분코드(ST 주식·EF ETF 등, 원문)
    as_of_date         DATE NOT NULL,          -- 마스터를 받은 KST 날짜. 원천은 현재값만 준다
    large_code         TEXT,                   -- NULL = 원천 0000(분류 없음)
    large_name         TEXT,
    medium_code        TEXT,
    medium_name        TEXT,
    small_code         TEXT,
    small_name         TEXT,
    raw_large_code     TEXT NOT NULL,          -- 원문 4자리 보존(0000 포함)
    raw_medium_code    TEXT NOT NULL,
    raw_small_code     TEXT NOT NULL,
    taxonomy           TEXT NOT NULL,
    received_at        TIMESTAMPTZ NOT NULL,
    available_at       TIMESTAMPTZ NOT NULL,
    availability_basis TEXT NOT NULL,
    raw_run_id         TEXT NOT NULL,
    raw_key            TEXT NOT NULL,
    raw_sha256         TEXT NOT NULL,
    canonical_run_id   TEXT NOT NULL,
    artifact_key       TEXT NOT NULL,
    artifact_sha256    TEXT NOT NULL,
    loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (market, instrument_code, as_of_date, raw_run_id),
    CONSTRAINT ck_sector_classification_market CHECK (market IN ('KOSPI', 'KOSDAQ')),
    -- KIS 지수업종 체계만 담는다. WICS·GICS·KRX 업종지수 코드와 섞지 않는다.
    CONSTRAINT ck_sector_classification_taxonomy CHECK (taxonomy = 'KIS_INDEX_SECTOR'),
    CONSTRAINT ck_sector_classification_raw CHECK (
        raw_large_code ~ '^[0-9]{4}$' AND raw_medium_code ~ '^[0-9]{4}$' AND raw_small_code ~ '^[0-9]{4}$'),
    CONSTRAINT ck_sector_classification_none CHECK (
        (raw_large_code = '0000') = (large_code IS NULL)
        AND (raw_medium_code = '0000') = (medium_code IS NULL)
        AND (raw_small_code = '0000') = (small_code IS NULL)
        AND (large_code IS NULL OR large_code = raw_large_code)
        AND (medium_code IS NULL OR medium_code = raw_medium_code)
        AND (small_code IS NULL OR small_code = raw_small_code)),
    CONSTRAINT ck_sector_classification_as_of CHECK (
        as_of_date = (received_at AT TIME ZONE 'Asia/Seoul')::date),
    CONSTRAINT ck_sector_classification_basis CHECK (
        availability_basis = 'received' AND available_at = received_at),
    CONSTRAINT ck_sector_classification_sha CHECK (
        raw_sha256 ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX ix_sector_classification_visible ON sector_classification (instrument_code, available_at);

COMMENT ON TABLE sector_classification IS
'KIS 종목 마스터의 지수업종 대·중·소분류 스냅샷(ALPHA-1130). 원천이 현재값만 주므로 as_of_date 이전 분류는 없다(복원하지 않는다). 조회는 sector_classification_as_of.';

-- ── 소비 조회 계약 ────────────────────────────────────────────────────────────
-- 반환 행은 근거(raw_run_id·raw_key·canonical_run_id·artifact_key)를 함께 싣는다. 결측을 0으로 채우지 않는다.

CREATE FUNCTION macro_observations_as_of(p_analysis_at TIMESTAMPTZ, p_series TEXT, p_limit INTEGER DEFAULT 21)
RETURNS TABLE (
    series_id TEXT, observation_date DATE, reference_period TEXT, value NUMERIC, unit TEXT,
    received_at TIMESTAMPTZ, available_at TIMESTAMPTZ, availability_basis TEXT,
    source_vendor TEXT, source_series TEXT, raw_run_id TEXT, raw_key TEXT,
    canonical_run_id TEXT, artifact_key TEXT)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_analysis_at IS NULL OR p_series IS NULL OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 400 THEN
        RAISE EXCEPTION 'macro_observations_as_of: analysis_at·series 필수, limit 1..400';
    END IF;
    RETURN QUERY
    SELECT v.series_id, v.observation_date,
           CASE WHEN v.series_id = 'kr_cpi_yoy' THEN to_char(v.observation_date, 'YYYY-MM') END,
           v.value, v.unit, v.received_at, v.available_at, v.availability_basis,
           v.source_vendor, v.source_series, v.raw_run_id, v.raw_key, v.canonical_run_id, v.artifact_key
    FROM (
        SELECT DISTINCT ON (m.observation_date) m.*
        FROM macro_observation m
        WHERE m.series_id = p_series AND m.available_at <= p_analysis_at
        ORDER BY m.observation_date, m.available_at DESC, m.received_at DESC, m.raw_run_id DESC
    ) v
    ORDER BY v.observation_date DESC
    LIMIT p_limit;
END $$;

COMMENT ON FUNCTION macro_observations_as_of(TIMESTAMPTZ, TEXT, INTEGER) IS
'기준시각에 보였던 계열의 최근 N개 관측(관측일 내림차순). 관측일마다 available_at<=T 판본 중 가장 늦게 보인 것 하나. 최근 공개 관측 요구(예 2개)와 툴 조회 한도(21)는 호출자가 p_limit 로 고른다.';

CREATE FUNCTION financial_quarters_as_of(p_analysis_at TIMESTAMPTZ, p_instrument_code TEXT)
RETURNS TABLE (
    instrument_code TEXT, corp_code TEXT, fiscal_year SMALLINT, period TEXT, period_end DATE,
    fs_basis TEXT, eps NUMERIC, eps_derivation TEXT, bps NUMERIC, bps_total_shares NUMERIC, bps_note TEXT,
    revenue NUMERIC, revenue_derivation TEXT, operating_income NUMERIC, operating_income_derivation TEXT,
    available_at TIMESTAMPTZ, rcept_nos TEXT[], raw_run_ids TEXT[],
    version_raw_run_id TEXT, version_received_at TIMESTAMPTZ, version_rejected JSONB, shares_status TEXT,
    latest_unconfirmed_at TIMESTAMPTZ, version_status TEXT)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_analysis_at IS NULL OR p_instrument_code IS NULL THEN
        RAISE EXCEPTION 'financial_quarters_as_of: analysis_at·instrument_code 필수';
    END IF;
    RETURN QUERY
    WITH versions AS (
        SELECT v.* FROM financial_report_version v
        WHERE v.instrument_code = p_instrument_code AND v.available_at <= p_analysis_at
    ), confirmed AS (
        -- 보고서(회사·연도·보고기간·기준)마다 가장 늦게 **받은** 확정 판본 하나가 권위다. 확정 못 한 실행
        -- (UNCONFIRMED)은 이 선택에 끼지 않는다 — 일시적 API 실패가 정상 확정값을 무효화하지 않는다.
        -- 늦게 끝난 옛 실행은 수신시각이 앞서므로 최신 확정을 덮지 못한다.
        SELECT DISTINCT ON (v.corp_code, v.fiscal_year, v.report_period, v.fs_basis) v.*
        FROM versions v WHERE v.status = 'CONFIRMED'
        ORDER BY v.corp_code, v.fiscal_year, v.report_period, v.fs_basis, v.received_at DESC, v.raw_run_id DESC
    ), basis AS (
        -- 연결 우선. 그 시점까지 지표가 있는 연결 판본이 **한 번이라도** 있던 회사는 연결(2026-09-30 결정) — 최신 판본이
        -- 비어도 이력이 기준을 정한다(빈 최신 CFS 가 별도 값으로 갈아타지 않게). 빈 연결 판본(013)만 있으면 연결 증거가 아니다.
        SELECT CASE WHEN bool_or(v.status = 'CONFIRMED' AND v.fs_basis = 'CFS' AND jsonb_array_length(v.metrics) > 0)
                    THEN 'CFS' ELSE 'OFS' END AS fs_basis
        FROM versions v
    ), attempted AS (
        -- 확정 판본이 (회사 기준으로) 없는 보고기간: 확정 못 한 시도(UNCONFIRMED)만 있어도 소비자가 "확인 시도가
        -- 실패했다"를 보게 가장 늦은 시도를 행으로 낸다(값 전부 NULL). 회사 기준의 시도가 없으면 다른 기준의 시도라도
        -- 낸다(첫 연결 요청이 한도·점검으로 멈춰 별도 요청이 없던 실행). 시도 자체가 없으면 행이 없다.
        SELECT DISTINCT ON (v.corp_code, v.fiscal_year, v.report_period) v.*
        FROM versions v CROSS JOIN basis b
        WHERE v.status = 'UNCONFIRMED' AND NOT EXISTS (
            SELECT 1 FROM confirmed c WHERE c.corp_code = v.corp_code AND c.fiscal_year = v.fiscal_year
              AND c.report_period = v.report_period AND c.fs_basis = b.fs_basis)
        ORDER BY v.corp_code, v.fiscal_year, v.report_period, (v.fs_basis = b.fs_basis) DESC,
                 v.received_at DESC, v.raw_run_id DESC
    ), chosen AS (
        SELECT c.* FROM confirmed c JOIN basis b ON b.fs_basis = c.fs_basis
        UNION ALL
        SELECT a.* FROM attempted a
    ), metric AS (
        SELECT f.*, CASE WHEN f.fiscal_period = 'FY' THEN 'Q4' ELSE f.fiscal_period END AS report_period
        FROM financial_metric f
        WHERE f.instrument_code = p_instrument_code AND f.available_at <= p_analysis_at
    )
    -- 확정 판본이 한 줄이면 분기 행도 한 줄이다 — 그 실행이 만든 지표만 값이 있고 나머지는 NULL.
    -- 옛 판본의 값으로 채우지 않는다(지표가 0개인 정정 판본이 옛 값을 최신처럼 남기지 않는다).
    SELECT p_instrument_code, c.corp_code, c.fiscal_year,
           c.fiscal_year::text || '-' || c.report_period,
           -- 기말은 저장된 지표 행의 것을 쓰고, 지표가 없는 판본만 12월 결산 달력으로(ck_financial_metric_period_end 가 같은 규칙).
           COALESCE(min(m.period_end) FILTER (WHERE m.fiscal_period <> 'FY'),
                    CASE c.report_period WHEN 'Q1' THEN make_date(c.fiscal_year, 3, 31) WHEN 'Q2' THEN make_date(c.fiscal_year, 6, 30)
                                         WHEN 'Q3' THEN make_date(c.fiscal_year, 9, 30) ELSE make_date(c.fiscal_year, 12, 31) END),
           c.fs_basis,
           max(m.value) FILTER (WHERE m.metric = 'eps_basic' AND m.period_kind = 'QUARTER'),
           max(m.derivation) FILTER (WHERE m.metric = 'eps_basic' AND m.period_kind = 'QUARTER'),
           max(m.value) FILTER (WHERE m.metric = 'bps'),
           max(m.value) FILTER (WHERE m.metric = 'bps_total_shares'),
           -- bps 가 빈 이유: 정제가 통상 BPS 근거 줄에 남긴 판정(common_bps)을 읽는다(파손이 정책 차단보다 먼저).
           -- 통상 BPS 도 없으면: 분모 응답을 정상으로 받았으면(ok·empty) 확정된 부재, 아니면 확정 못 한 것.
           CASE WHEN max(m.value) FILTER (WHERE m.metric = 'bps') IS NULL THEN
                CASE WHEN max(m.value) FILTER (WHERE m.metric = 'bps_total_shares') IS NULL
                     THEN CASE WHEN c.detail->>'shares' IN ('ok', 'empty') THEN 'BPS_ABSENT_IN_LATEST_VERSION'
                               ELSE 'BPS_UNCONFIRMED' END
                     WHEN bool_or(EXISTS (
                              SELECT 1 FROM jsonb_array_elements(m.inputs) i
                              WHERE i->>'common_bps' = 'bps_blocked_preferred_shares'))
                          FILTER (WHERE m.metric = 'bps_total_shares')
                     THEN 'PREFERRED_SHARES_PRESENT' ELSE 'COMMON_SHARE_BPS_UNAVAILABLE' END END,
           max(m.value) FILTER (WHERE m.metric = 'revenue' AND m.period_kind = 'QUARTER'),
           max(m.derivation) FILTER (WHERE m.metric = 'revenue' AND m.period_kind = 'QUARTER'),
           max(m.value) FILTER (WHERE m.metric = 'operating_income' AND m.period_kind = 'QUARTER'),
           max(m.derivation) FILTER (WHERE m.metric = 'operating_income' AND m.period_kind = 'QUARTER'),
           GREATEST(c.available_at, COALESCE(max(m.available_at), c.available_at)),
           array_agg(DISTINCT m.rcept_no ORDER BY m.rcept_no) FILTER (WHERE m.rcept_no IS NOT NULL),
           array_agg(DISTINCT m.raw_run_id ORDER BY m.raw_run_id) FILTER (WHERE m.raw_run_id IS NOT NULL),
           c.raw_run_id, c.received_at, c.rejected, c.detail->>'shares',
           -- 확정 판본 뒤에 확정 못 한 확인이 있었으면 그 시각 — 옛 값을 주고 있음을 소비자가 안다.
           -- 확정이 한 번도 없는 보고서(status=UNCONFIRMED 행)는 그 시도 자체의 시각.
           (SELECT max(u.received_at) FROM versions u
             WHERE u.status = 'UNCONFIRMED' AND u.corp_code = c.corp_code AND u.fiscal_year = c.fiscal_year
               AND u.report_period = c.report_period AND u.fs_basis = c.fs_basis AND u.received_at >= c.received_at),
           c.status
    FROM chosen c
    -- 미확정 시도 행은 값이 전부 NULL 이어야 한다 — 정제가 미확정 응답의 지표를 만들지 않지만 조회도 같은 규칙을 건다.
    LEFT JOIN metric m ON c.status = 'CONFIRMED' AND m.corp_code = c.corp_code AND m.fiscal_year = c.fiscal_year
                       AND m.report_period = c.report_period AND m.fs_basis = c.fs_basis AND m.raw_run_id = c.raw_run_id
    GROUP BY c.corp_code, c.fiscal_year, c.report_period, c.fs_basis, c.raw_run_id, c.received_at, c.available_at,
             c.rejected, c.detail, c.status
    ORDER BY c.fiscal_year, c.report_period;
END $$;

COMMENT ON FUNCTION financial_quarters_as_of(TIMESTAMPTZ, TEXT) IS
'기준시각에 보였던 분기 재무. 행 단위는 **확정 보고서 판본**(financial_report_version, 보고서마다 가장 늦게 받은 CONFIRMED) — 그 실행이 만든 지표만 값이 있고 빠진 지표는 NULL(옛 판본 값으로 채우지 않는다). 확정 못 한 뒤 실행은 latest_unconfirmed_at 으로만 드러난다. 확정이 한 번도 없는 보고서는 가장 늦은 UNCONFIRMED 시도가 값 NULL·version_status=UNCONFIRMED 행으로 나온다(시도가 없으면 행 없음). EPS·매출·영업이익은 해당 분기 3개월 값(Q4는 FY−9M 유도, *_derivation), bps 는 보통주 1주 기준(우선주 없는 회사만), bps_total_shares 는 통상 관행. bps_note: PREFERRED_SHARES_PRESENT=정책 차단(팀 결정 대상) · COMMON_SHARE_BPS_UNAVAILABLE=종류별 주식수 파손 · BPS_ABSENT_IN_LATEST_VERSION=분모 응답은 정상인데 BPS 없음 · BPS_UNCONFIRMED=분모 응답 실패(재수집 대상). version_rejected 에 그 판본이 못 만든 지표와 사유가 있다.';

CREATE FUNCTION sector_classification_as_of(p_analysis_at TIMESTAMPTZ, p_instrument_codes TEXT[])
RETURNS TABLE (
    instrument_code TEXT, found BOOLEAN, market TEXT, as_of_date DATE,
    large_code TEXT, large_name TEXT, medium_code TEXT, medium_name TEXT, small_code TEXT, small_name TEXT,
    available_at TIMESTAMPTZ, raw_run_id TEXT, raw_key TEXT, canonical_run_id TEXT)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_analysis_at IS NULL OR p_instrument_codes IS NULL THEN
        RAISE EXCEPTION 'sector_classification_as_of: analysis_at·instrument_codes 필수';
    END IF;
    RETURN QUERY
    SELECT c.code, s.instrument_code IS NOT NULL, s.market, s.as_of_date,
           s.large_code, s.large_name, s.medium_code, s.medium_name, s.small_code, s.small_name,
           s.available_at, s.raw_run_id, s.raw_key, s.canonical_run_id
    FROM unnest(p_instrument_codes) AS c(code)
    LEFT JOIN LATERAL (
        SELECT x.* FROM sector_classification x
        WHERE x.instrument_code = c.code AND x.available_at <= p_analysis_at
        ORDER BY x.available_at DESC, x.raw_run_id DESC
        LIMIT 1
    ) s ON true
    ORDER BY c.code;
END $$;

COMMENT ON FUNCTION sector_classification_as_of(TIMESTAMPTZ, TEXT[]) IS
'기준시각에 보였던 가장 최근 분류 스냅샷. found=false 는 그 시점까지 받은 스냅샷에 종목이 없음(분류 없음 0000 과 다르다 — 그건 found=true 에 코드 NULL).';

-- 한 ETF의 기준시각 구성종목(당시 유효 스냅샷)과 원천별 확보 여부. 현재 구성종목을 과거에 소급하지 않는다.
CREATE FUNCTION etf_constituent_source_coverage(p_etf_ticker TEXT, p_analysis_at TIMESTAMPTZ)
RETURNS TABLE (
    holdings_trade_date DATE, constituent_ticker TEXT, market_code TEXT, weight_ratio DOUBLE PRECISION,
    has_sector_classification BOOLEAN, sector_as_of_date DATE,
    eps_quarters INTEGER, latest_eps_period TEXT, latest_bps_period TEXT)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    IF p_etf_ticker IS NULL OR p_analysis_at IS NULL THEN
        RAISE EXCEPTION 'etf_constituent_source_coverage: etf_ticker·analysis_at 필수';
    END IF;
    RETURN QUERY
    WITH etf AS (
        SELECT i.instrument_id FROM instrument i
        WHERE i.ticker = p_etf_ticker AND i.market_code IN ('XKRX', 'XKOS')
    ), snapshot AS (
        -- load_price_triggers._latest_good_holdings 와 같은 good 판정 + 기준시각 이전에 보인 스냅샷만.
        SELECT st.etf_instrument_id, st.trade_date, st.data_version
        FROM etf_holding_snapshot_status st JOIN etf ON etf.instrument_id = st.etf_instrument_id
        WHERE st.trade_date <= (p_analysis_at AT TIME ZONE 'Asia/Seoul')::date
          AND 2 * st.valid_row_count >= st.input_row_count
          AND EXISTS (SELECT 1 FROM etf_holding_snapshot h
                      WHERE h.etf_instrument_id = st.etf_instrument_id AND h.trade_date = st.trade_date
                        AND h.data_version = st.data_version AND h.available_at <= p_analysis_at)
        ORDER BY st.trade_date DESC
        LIMIT 1
    ), members AS (
        SELECT s.trade_date, i.ticker, i.market_code, h.weight_ratio
        FROM snapshot s
        JOIN etf_holding_snapshot h ON h.etf_instrument_id = s.etf_instrument_id
         AND h.trade_date = s.trade_date AND h.data_version = s.data_version
        JOIN instrument i ON i.instrument_id = h.constituent_instrument_id
    )
    SELECT m.trade_date, m.ticker::text, m.market_code::text, m.weight_ratio,
           sc.found, sc.as_of_date,
           (SELECT count(*)::integer FROM financial_quarters_as_of(p_analysis_at, m.ticker::text) q
             WHERE q.eps IS NOT NULL),
           (SELECT max(q.period) FROM financial_quarters_as_of(p_analysis_at, m.ticker::text) q
             WHERE q.eps IS NOT NULL),
           (SELECT max(q.period) FROM financial_quarters_as_of(p_analysis_at, m.ticker::text) q
             WHERE q.bps IS NOT NULL)
    FROM members m
    LEFT JOIN LATERAL sector_classification_as_of(p_analysis_at, ARRAY[m.ticker::text]) sc ON true
    ORDER BY m.ticker;
END $$;

COMMENT ON FUNCTION etf_constituent_source_coverage(TEXT, TIMESTAMPTZ) IS
'기준시각에 유효했던 ETF 구성종목 스냅샷의 종목별 업종·재무 확보 여부. 스냅샷이 없으면 0행이다(현재 구성으로 대체하지 않는다). 한계: etf_holding_snapshot(_status) 는 ETF·날짜당 한 판본이라(기존 적재가 같은 키를 덮어쓴다) 정정 스냅샷이 오면 그 날짜의 이전 구성은 복원되지 않고 available_at 이 뒤로 밀린다 — 정정 전 기준시각은 그 날짜를 건너뛰어 앞 스냅샷을 본다. 구성종목 판본 이력은 이 계약 밖(holdings 레인 소관).';

-- 신선도(§6): API 성공은 신선의 증거가 아니다. 판정에 쓸 사실만 나란히 내놓는다 — 원장의 마지막 적재 성공,
-- 데이터 자체의 마지막 수신·최신 관측일. 공급자 게시 캘린더가 없으므로(ECOS 는 KRX 휴장일에도 값을 내고,
-- DART 접수는 회사마다, KIS 마스터는 거래일 기준이나 공식 캘린더 미확보) status 는 항상 UNKNOWN 이다 —
-- '어제 값이 있어야 한다'는 기대는 여기서 만들지 않는다. 판정은 호출자가 basis 를 보고 한다.
CREATE FUNCTION source_observation_freshness()
RETURNS TABLE (
    dataset TEXT, series_id TEXT, latest_observation_date DATE, last_received_at TIMESTAMPTZ,
    last_load_fulfilled_at TIMESTAMPTZ, last_load_data_status TEXT,
    freshness_status TEXT, freshness_reason TEXT, basis TEXT)
LANGUAGE sql STABLE AS $$
    WITH loads AS (
        SELECT t.task_key, max(t.fulfilled_at) AS fulfilled_at,
               (array_agg(t.data_status ORDER BY t.fulfilled_at DESC))[1] AS data_status
        FROM ops_expected_task t
        WHERE t.task_key IN ('LOAD_MACRO', 'LOAD_FINANCIAL_METRIC', 'LOAD_SECTOR') AND t.task_outcome = 'FULFILLED'
        GROUP BY t.task_key
    ), facts AS (
        SELECT 'macro_observation'::text AS dataset, m.series_id, max(m.observation_date) AS latest,
               max(m.received_at) AS received, 'LOAD_MACRO'::text AS task_key,
               'observation_date=공급자 관측일(일별) 또는 기준월 1일(CPI). 캘린더 없음: ECOS 는 휴장일에도 값이 있다'::text AS basis
        FROM macro_observation m GROUP BY m.series_id
        UNION ALL
        -- 재무는 판본 표로 센다 — 확인했지만 지표가 0개인 실행도 "적재됨"이다(지표 표만 보면 그 실행이 사라진다).
        SELECT 'financial_metric', NULL,
               max(CASE v.report_period WHEN 'Q1' THEN make_date(v.fiscal_year, 3, 31) WHEN 'Q2' THEN make_date(v.fiscal_year, 6, 30)
                                        WHEN 'Q3' THEN make_date(v.fiscal_year, 9, 30) ELSE make_date(v.fiscal_year, 12, 31) END)
                   FILTER (WHERE v.status = 'CONFIRMED'),
               max(v.received_at), 'LOAD_FINANCIAL_METRIC',
               'period_end=확정 판본 중 가장 늦은 보고 기말(지표 0개 판본 포함). 접수 시점은 회사·보고서마다 달라 기대 관측일이 없다'
        FROM financial_report_version v
        UNION ALL
        SELECT 'sector_classification', NULL, max(s.as_of_date), max(s.received_at), 'LOAD_SECTOR',
               'as_of_date=마스터를 받은 KST 날짜(원천은 현재값만). 공식 게시 캘린더 미확보'
        FROM sector_classification s
    )
    SELECT f.dataset, f.series_id, f.latest, f.received, l.fulfilled_at, l.data_status,
           'UNKNOWN', 'NO_PROVIDER_CALENDAR', f.basis
    FROM facts f LEFT JOIN loads l ON l.task_key = f.task_key
    WHERE f.latest IS NOT NULL
    ORDER BY f.dataset, f.series_id
$$;

COMMENT ON FUNCTION source_observation_freshness() IS
'원천 관측 데이터셋별 신선도 사실(마지막 적재 성공·마지막 수신·최신 관측일). status 는 공급자 캘린더가 없어 항상 UNKNOWN — 판정은 호출자 몫. 행이 없는 데이터셋=적재 0건.';

-- ── v2 읽기 경로(ALPHA-1130 §5) ─────────────────────────────────────────────────
-- v2 는 이 다섯 함수로만 원천을 읽는다. 함수는 소유자 권한으로 돌고(SECURITY DEFINER) 테이블 자체는
-- 열지 않는다 — writer 역할의 테이블 권한은 그대로 0 이다(tests/analysis_v2_writer.sql).
-- search_path 고정: DEFINER 함수가 호출자의 경로에서 같은 이름의 객체를 집지 않게. pg_temp 를 명시적으로 뒤에 두지
-- 않으면 임시 스키마가 먼저 검색된다(PostgreSQL 문서 'Writing SECURITY DEFINER Functions Safely').
ALTER FUNCTION macro_observations_as_of(TIMESTAMPTZ, TEXT, INTEGER) SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION financial_quarters_as_of(TIMESTAMPTZ, TEXT) SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION sector_classification_as_of(TIMESTAMPTZ, TEXT[]) SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION etf_constituent_source_coverage(TEXT, TIMESTAMPTZ) SECURITY DEFINER SET search_path = public, pg_temp;
ALTER FUNCTION source_observation_freshness() SECURITY DEFINER SET search_path = public, pg_temp;
REVOKE EXECUTE ON FUNCTION
    macro_observations_as_of(TIMESTAMPTZ, TEXT, INTEGER), financial_quarters_as_of(TIMESTAMPTZ, TEXT),
    sector_classification_as_of(TIMESTAMPTZ, TEXT[]), etf_constituent_source_coverage(TEXT, TIMESTAMPTZ),
    source_observation_freshness()
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
    macro_observations_as_of(TIMESTAMPTZ, TEXT, INTEGER), financial_quarters_as_of(TIMESTAMPTZ, TEXT),
    sector_classification_as_of(TIMESTAMPTZ, TEXT[]), etf_constituent_source_coverage(TEXT, TIMESTAMPTZ),
    source_observation_freshness()
TO edge_analysis_v2_writer;
