-- ============================================================================
-- 노출 이력 — Exposure Log (ADR-0013: 조회 = 노출)
--
-- publication-api 가 200 응답 시점에 기록한다. 고객 식별은 증권사가 생성한
-- 해시뿐(원본 고객 ID/계좌 금지 — ADR-0013), 노출 이력은 온프렘에만 거주한다
-- (docs/domain/data-residency.md — Cloud 저장 금지 데이터).
-- ============================================================================

SET search_path TO public;

CREATE TABLE exposure_log (
    exposure_log_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    publication_id    BIGINT NOT NULL REFERENCES publication (publication_id),
    customer_hash     VARCHAR(128) NOT NULL,
    channel           VARCHAR(20) NOT NULL,
    -- 감사 재현용 스냅샷 — "어느 시점에 어떤 문구가 노출되었는지"(state-machine.md).
    -- 노출 재현이 이 테이블의 존재 이유이므로 결측을 허용하지 않는다.
    summary_snapshot  TEXT NOT NULL,
    exposed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_exposure_log_channel
        CHECK (channel IN ('MTS', 'HTS', 'INTERNAL'))
);

COMMENT ON TABLE exposure_log IS
'노출 이력(조회=노출, ADR-0013) — 온프렘 거주 한정(data-residency.md). writer = publication-api.';

-- 게시분별 노출 감사 조회용.
CREATE INDEX ix_exposure_log_publication ON exposure_log (publication_id, exposed_at);
