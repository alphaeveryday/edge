-- ============================================================================
-- 서빙 요청 메트릭 — serving_request_metric (ALPHA-501)
--
-- publication-api 요청의 수·상태·에러 코드 원장. Dashboard 트래픽·에러율
-- (ALPHA-128)의 데이터 소스다 — 요청당 1행(raw)을 남기고 기간별 집계는 조회
-- 시점에 시간 인덱스로 수행한다(데모 트래픽 규모에서 사전 집계는 과잉).
--
-- exposure_log 와 분리하는 이유: exposure_log 는 publication FK 가 걸린 200
-- 노출 전용 감사 원장이라 204·400·404·5xx 요청을 담을 수 없다. 이 테이블은
-- 관측(볼륨·에러율) 용도로, 고객 식별자·문구를 싣지 않는다(PII 없음 — route 는
-- 원시 URI 가 아니라 매핑 패턴으로 카디널리티를 통제한다).
-- ============================================================================

SET search_path TO public;

CREATE TABLE serving_request_metric (
    request_metric_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    method             VARCHAR(10) NOT NULL,
    -- MVC 매핑 패턴(예: /api/v1/explanations/{etfTicker}) — 미매핑 요청은 'UNMATCHED'.
    route              VARCHAR(150) NOT NULL,
    status_code        SMALLINT NOT NULL,
    -- 실패 응답(4xx·5xx)의 도메인 에러 코드(SERV*·COMMON*) — 성공·204 는 NULL.
    error_code         VARCHAR(20),
    occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_serving_request_metric_status
        CHECK (status_code BETWEEN 100 AND 599),
    -- 성공 행에 에러 코드가 실리는 모순 + 빈/공백 코드(NULL 과 별도 집계되는 유령
    -- 어휘)를 차단. 역방향(실패면 코드 필수)은 걸지 않는다 — 비JSON 에러 응답 등
    -- 코드를 알 수 없는 실패에서 NULL 이 정직한 값이다.
    CONSTRAINT ck_serving_request_metric_error_code
        CHECK (error_code IS NULL
               OR (status_code >= 400 AND btrim(error_code, E' \t\n\r') <> ''))
);

COMMENT ON TABLE serving_request_metric IS
'publication-api 요청 메트릭 원장(ALPHA-501) — writer = publication-api(요청 필터), reader = tenant-console-api(Dashboard 집계, ALPHA-128). 관측 용도라 고객 식별자·노출 문구를 싣지 않는다(감사는 exposure_log 소관).';

-- Dashboard 기간별 집계(시간 버킷 × 상태) 조회용.
CREATE INDEX ix_serving_request_metric_occurred
    ON serving_request_metric (occurred_at);
