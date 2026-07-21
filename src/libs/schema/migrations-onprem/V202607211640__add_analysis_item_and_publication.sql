-- ============================================================================
-- 온프렘 서빙 도메인 — analysis_item(상태기계) + publication(게시 grain)
--
-- state-machine.md ERD 의 상태 분기 테이블 실체화 (baseline 이 예고한 후속).
-- 온프렘 DB 는 테넌트별 물리 격리(ADR-0011)라 tenant_id 를 두지 않는다.
--
-- 정정 리비전 모델(state-machine.md 확정, 2026-07-13): 정정은 상태 왕복이 아니라
-- 리비전 분리 — 구 리비전은 CORRECTED 로 종결하고, 정정분은 supersedes_item_id 로
-- 원본을 참조하는 새 행으로 REVIEW_REQUIRED 진입한다.
-- ============================================================================

SET search_path TO public;

-- 분석 항목 — Compliance(Screening) 점검 결과의 상태를 담는 온프렘 원장.
-- PK 는 Cloud 가 발번한 explanation_result_id 를 그대로 쓴다(도메인 ID 재사용)
-- — 번들 재수신 시 이 키로 멱등 upsert 가 수렴한다.
CREATE TABLE analysis_item (
    explanation_result_id  TEXT PRIMARY KEY,
    etf_instrument_id      TEXT NOT NULL,
    -- 서빙 키(publication-api 는 ticker 로 조회). 현행 번들 계약엔 ticker 가 없어
    -- NULL 허용 — 경계면에 ticker 추가가 확정되면(별도 결정) 값이 공급된다.
    -- 게시(publication insert)는 ticker 를 요구하므로 결측 항목은 게시될 수 없다.
    etf_ticker             VARCHAR(20),
    etf_name               TEXT,
    trade_date             DATE NOT NULL,
    explanation_as_of      TIMESTAMPTZ NOT NULL,
    -- 도메인 확장축(ERD): MVP 는 가격 변동 설명만.
    analysis_type          VARCHAR(30) NOT NULL DEFAULT 'PRICE_MOVEMENT',
    -- 설명 방식(cloud explanation_result.explanation_type 과 동일 어휘).
    explanation_type       VARCHAR(30) NOT NULL,
    summary                TEXT NOT NULL,
    headline               TEXT,
    confidence_level       VARCHAR(20),
    primary_thread_id      TEXT,
    -- walking skeleton: 근거는 JSONB 로 통째 보존 — 별도 evidence 테이블은 후속 확장 지점.
    evidences              JSONB,
    -- 리비전 체인(정정): 정정분이 구 리비전을 참조한다.
    supersedes_item_id     TEXT REFERENCES analysis_item (explanation_result_id),
    correction_reason      TEXT,
    -- 수신 원본 추적 — received_bundle 의 entry cursor (감사 재현용).
    source_cursor          BIGINT,
    status                 VARCHAR(20) NOT NULL DEFAULT 'RECEIVED',
    received_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_analysis_item_status
        CHECK (status IN ('RECEIVED', 'AUTO_PUBLISHED', 'REVIEW_REQUIRED', 'APPROVED',
                          'REJECTED', 'BLOCKED', 'UNPUBLISHED', 'CORRECTED', 'INVALIDATED')),
    CONSTRAINT ck_analysis_item_analysis_type
        CHECK (analysis_type IN ('PRICE_MOVEMENT')),
    CONSTRAINT ck_analysis_item_explanation_type
        CHECK (explanation_type IN ('PRICE_ONLY', 'EVENT_SUPPORTED', 'MIXED', 'UNCERTAIN')),
    CONSTRAINT ck_analysis_item_confidence
        CHECK (confidence_level IS NULL OR confidence_level IN ('HIGH', 'MEDIUM', 'LOW')),
    -- 리비전 체인은 비순환이 전제 — 최소한 자기참조는 스키마가 차단한다.
    CONSTRAINT ck_analysis_item_no_self_supersede
        CHECK (supersedes_item_id IS NULL OR supersedes_item_id <> explanation_result_id)
);

-- writer 는 상태 전이별로 겹치지 않게 분담한다(전이 소유 분리 — 같은 전이를 두
-- 모듈이 쓰는 일이 없다): screening-worker = 수신·자동 분기(RECEIVED,
-- AUTO_PUBLISHED, REVIEW_REQUIRED, BLOCKED)와 Cloud 이벤트 반영(CORRECTED,
-- INVALIDATED, 정정에 따른 UNPUBLISHED), tenant-console-api = 검수 결정
-- (REVIEW_REQUIRED → APPROVED | REJECTED)뿐.
COMMENT ON TABLE analysis_item IS
'분석 항목 상태 원장(state-machine.md) — PK = Cloud 발번 explanation_result_id(멱등 upsert 키). writer 는 전이별 분담: screening-worker(수신·자동 분기·Cloud 이벤트 반영), tenant-console-api(검수 결정 전이만).';

-- 검수 큐(REVIEW_REQUIRED 목록) 조회용.
CREATE INDEX ix_analysis_item_status ON analysis_item (status);

-- 게시 원장 — Published Store. 서빙(publication-api)의 유일한 조회 소스.
CREATE TABLE publication (
    publication_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    analysis_item_id       TEXT NOT NULL REFERENCES analysis_item (explanation_result_id),
    -- 게시 grain 컬럼(부분 유니크 대상) — analysis_item 에서 게시 시점 복사.
    etf_ticker             VARCHAR(20) NOT NULL,
    trade_date             DATE NOT NULL,
    status                 VARCHAR(20) NOT NULL DEFAULT 'PUBLISHED',
    published_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    unpublished_at         TIMESTAMPTZ,

    CONSTRAINT ck_publication_status
        CHECK (status IN ('PUBLISHED', 'UNPUBLISHED', 'INVALIDATED'))
);

COMMENT ON TABLE publication IS
'게시 원장(Published Store) — publication-api 서빙 소스. writer 는 전이별 분담: screening-worker(자동 게시·무효화·정정 UNPUBLISHED), tenant-console-api(검수 승인 재발행만).';

-- 온프렘 게시 grain 은 고객 노출 단위 — 같은 (ticker, trade_date) 에 PUBLISHED 1건만.
-- cloud 의 3컬럼 grain(instrument, trade_date, as_of)과 의도적으로 다르다: 온프렘은
-- 고객 화면에 ETF·거래일당 하나의 설명만 노출하며, 새 기준시각 결과는 구본을
-- UNPUBLISHED 로 전이한 뒤 재게시한다(리비전 모델과 동일한 교체 규율).
CREATE UNIQUE INDEX uq_publication_published_grain
    ON publication (etf_ticker, trade_date)
    WHERE status = 'PUBLISHED';
