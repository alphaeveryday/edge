-- dataset commit outbox — DB commit 과 SQS publish 의 원자성 경계 (ALPHA-661, v0.7 10.6).
--
-- canonical/window/job 확정과 **같은 DB transaction** 에서 event 를 INSERT 하고, 독립
-- Relay 만 SQS 에 publish 한다. DB commit 뒤 process 가 죽어도 event 는 NEW 로 남아
-- 유실이 없다. Relay 는 event_type/destination 만으로 결정적으로 routing 하며 business
-- logic 을 갖지 않는다 (v0.7 11절).
--
-- event_id 는 결정적이다(고정 필드 순서 UTF-8 JSON array·UTC RFC3339 Z·lowercase sha256):
--   news  initial = "NewsExtractionRequested:" + job_id + ":0"
--   price initial = "PriceWindowCommitted:"    + job_id + ":0"
--   수동 redrive 만 ":<redrive_generation>" 을 올리고, correction 은 data generation 을
--   올린 새 job/event 를 만든다. INSERT 는 ON CONFLICT (event_id) DO NOTHING —
--   realtime/backfill destination 이 달라도 같은 논리 사건의 일반 재전달은 같은 event_id.
--
-- status 는 NEW/PUBLISHED/DEAD 3개다. "claim 중"은 status 가 아니라
-- claimed_by/claim_expires_at 으로 표현한다 — claim 을 status 로 두면 Relay crash 시
-- CLAIMED 로 영구 고착되는 상태가 생기고, expiry 재청구 로직이 status 전이와 얽힌다.
-- 지속 발행 실패는 운영자가 조회 가능한 DEAD 로 격리한다 (v0.7 11.1).

SET search_path TO public;

CREATE TABLE dataset_commit_outbox (
    event_id         TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    destination      TEXT NOT NULL,
    aggregate_id     TEXT NOT NULL,
    generation       INTEGER NOT NULL,
    payload          JSONB NOT NULL,
    status           TEXT NOT NULL DEFAULT 'NEW',
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ,
    claimed_by       TEXT,
    claim_expires_at TIMESTAMPTZ,
    published_at     TIMESTAMPTZ,
    last_error       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (event_id),
    CONSTRAINT ck_outbox_status CHECK (status IN ('NEW','PUBLISHED','DEAD'))
);

-- Relay 의 batch claim 은 미발행분만 본다. claim 중(NEW + 미만료 claim_expires_at)
-- 재스캔 배제까지 인덱스로 풀지는 PR 2C 의 실제 claim 쿼리와 함께 조정한다 —
-- 신규 인덱스 추가는 forward-only migration 으로 부담이 없다.
CREATE INDEX idx_outbox_pending
    ON dataset_commit_outbox (next_attempt_at, created_at)
    WHERE status = 'NEW';

COMMENT ON TABLE dataset_commit_outbox IS
'canonical commit 과 같은 transaction 에 쓰이는 outbox. 독립 Relay 만 SQS publish 하고 성공 시 published_at 을 남긴다. event_id 는 결정적(v0.7 10.6 유도식) — 일반 재전달은 같은 ID, 수동 redrive 만 redrive_generation 증가.';
