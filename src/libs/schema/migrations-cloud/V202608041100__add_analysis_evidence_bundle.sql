-- 근거 묶음 (ALPHA-671).
--
-- 쉬운 설명(MTS 카드용 headline)에는 수치가 없다. 수치가 없으면 그 문장이 통계
-- 검정에서 나온 것인지 기사 서사에서 나온 것인지 나중에 구분할 수 없고, 구분할 수
-- 없으면 **서사를 검정 결과처럼 읽는다**. 그래서 주장 단위로 근거를 남긴다:
--
--   basis='statistical'  stats 에 그 가설의 검정 결과 (사건타입·구체화 슬롯·n·ATT·p·판정)
--   basis='narrative'    news_ids 에 조회한 뉴스 id 목록
--
-- bundle_id 는 **내용 sha1** 이다(id 생성을 모델에 맡기지 않는다는 규약, ADR-0027 의
-- 결정적 계열과 같은 정신). 재실행에 같은 id 가 나와야 산출물을 비교할 수 있다.
--
-- 이 표는 엔진 코드가 `CREATE TABLE IF NOT EXISTS` 로 만들고 있었다 - Flyway 밖의
-- 유일한 예외였다. 스키마 소유권을 원장으로 되돌린다.
CREATE TABLE IF NOT EXISTS analysis_evidence_bundle (
    bundle_id   TEXT        PRIMARY KEY,
    basis       TEXT        NOT NULL,
    cell        TEXT        NOT NULL,
    trade_date  DATE        NOT NULL,
    layer       TEXT,
    claim       TEXT        NOT NULL,
    news_ids    TEXT[]      NOT NULL DEFAULT '{}',
    stats       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_analysis_evidence_bundle_basis
        CHECK (basis IN ('statistical', 'narrative')),
    -- 근거 없는 묶음은 근거가 아니다. 서사는 뉴스 id 가, 통계는 검정 수치가 있어야 한다.
    CONSTRAINT ck_analysis_evidence_bundle_grounded
        CHECK ((basis = 'narrative'   AND cardinality(news_ids) > 0)
            OR (basis = 'statistical' AND stats <> '{}'::jsonb))
);

CREATE INDEX IF NOT EXISTS ix_analysis_evidence_bundle_cell
    ON analysis_evidence_bundle (cell, trade_date DESC);

COMMENT ON TABLE analysis_evidence_bundle IS
    '설명의 주장 단위 근거 묶음 - headline 의 각 주장이 {basis, bundle_id} 로 이것을 가리킨다 (ALPHA-671)';
COMMENT ON COLUMN analysis_evidence_bundle.bundle_id IS
    '내용 sha1 기반 결정적 id (ev_<16hex>) - 재실행 동일';
COMMENT ON COLUMN analysis_evidence_bundle.cell IS
    '분석 셀 식별자 - ETF 코드 또는 종목 티커';
COMMENT ON COLUMN analysis_evidence_bundle.stats IS
    'basis=statistical 일 때 그 가설의 검정 결과 (etype·slots·n·att·p·verdict·iset)';
COMMENT ON COLUMN analysis_evidence_bundle.news_ids IS
    'basis=narrative 일 때 근거가 된 뉴스 문서 id 목록 (재보도 제외·스레드 첫 보도)';
