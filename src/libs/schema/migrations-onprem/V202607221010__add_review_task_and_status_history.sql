-- ============================================================================
-- 검수 태스크 + 상태 변경 이력 — review_task, analysis_item_status_history
--
-- state-machine.md 가 예고한 review_tasks 의 실체화. 검수 의견이 현재 로그로만
-- 남고 영속되지 않는 구멍(ALPHA-437 잔여)을 이 테이블이 메운다.
-- 최종 검수 결과·상태 변경 이력은 온프렘 거주 한정 데이터다(data-residency.md).
--
-- Review Queue 자체는 여전히 물리 저장소가 아니다 — 큐는 analysis_item.status
-- = 'REVIEW_REQUIRED' 의 논리 작업함이고, review_task 는 개별 검수 건의
-- 결정·편집·의견을 담는 기록이다.
-- ============================================================================

SET search_path TO public;

CREATE TABLE review_task (
    review_task_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    analysis_item_id  TEXT NOT NULL REFERENCES analysis_item (explanation_result_id),
    status            VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- 배정 전 NULL. 결정 시점엔 앱 계층이 채운다.
    reviewer_id       BIGINT REFERENCES member (member_id),
    -- 검수 편집본(수정 승인·임시 저장) — 원문은 analysis_item 에 남고 여기엔 편집만.
    edited_headline   TEXT,
    edited_summary    TEXT,
    -- 검수 의견·반려 사유. 반려(REJECTED) 시 사유 필수는 앱 계층이 강제한다
    -- (ReviewController — blank 면 400).
    review_note       TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at        TIMESTAMPTZ,

    -- 어휘는 state-machine.md 확정값. CANCELLED = 검수 대기 중 정정·무효화가
    -- 도착해 태스크가 무의미해진 경우(리비전 분리 모델의 귀결).
    CONSTRAINT ck_review_task_status
        CHECK (status IN ('PENDING', 'APPROVED', 'EDITED_APPROVED', 'REJECTED', 'CANCELLED')),
    -- 결정(취소 포함)과 결정 시각은 함께 움직인다.
    CONSTRAINT ck_review_task_decided
        CHECK ((status = 'PENDING') = (decided_at IS NULL))
);

COMMENT ON TABLE review_task IS
'검수 태스크(state-machine.md review_tasks) — 결정·편집본·의견의 영속 기록(ALPHA-437). writer = tenant-console-api.';

-- 같은 항목에 열린 태스크는 1건만 — 동시 배정/중복 생성의 arbiter.
CREATE UNIQUE INDEX uq_review_task_open
    ON review_task (analysis_item_id)
    WHERE status = 'PENDING';

-- 상태 변경 이력 (append-only — UPDATE/DELETE 하지 않는다).
-- "콘텐츠 상태 변경 이력" 온프렘 보존 요구(data-residency.md)의 원장.
CREATE TABLE analysis_item_status_history (
    status_history_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    analysis_item_id   TEXT NOT NULL REFERENCES analysis_item (explanation_result_id),
    -- NULL = 최초 진입(수신).
    from_status        VARCHAR(20),
    to_status          VARCHAR(20) NOT NULL,
    -- SYSTEM = screening-worker 자동 분기·Cloud 이벤트 반영, MEMBER = 콘솔 결정.
    actor_type         VARCHAR(20) NOT NULL,
    actor_id           BIGINT REFERENCES member (member_id),
    reason             TEXT,
    occurred_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT ck_status_history_from
        CHECK (from_status IS NULL OR from_status IN
               ('RECEIVED', 'AUTO_PUBLISHED', 'REVIEW_REQUIRED', 'APPROVED',
                'REJECTED', 'BLOCKED', 'UNPUBLISHED', 'CORRECTED', 'INVALIDATED')),
    CONSTRAINT ck_status_history_to
        CHECK (to_status IN
               ('RECEIVED', 'AUTO_PUBLISHED', 'REVIEW_REQUIRED', 'APPROVED',
                'REJECTED', 'BLOCKED', 'UNPUBLISHED', 'CORRECTED', 'INVALIDATED')),
    CONSTRAINT ck_status_history_actor_type
        CHECK (actor_type IN ('SYSTEM', 'MEMBER')),
    -- 사람 액션이면 반드시 누가, 시스템 액션이면 actor 없음.
    CONSTRAINT ck_status_history_actor
        CHECK ((actor_type = 'MEMBER') = (actor_id IS NOT NULL))
);

COMMENT ON TABLE analysis_item_status_history IS
'analysis_item 상태 변경 이력(append-only) — 전이를 만든 모든 writer 가 전이와 같은 트랜잭션에서 기록한다.';

-- 항목별 이력 재현(민원·감사) 조회용.
CREATE INDEX ix_status_history_item ON analysis_item_status_history (analysis_item_id, occurred_at);
