-- 데이터 파이프라인 운영 원장 MVP — expected_task 중심 (ALPHA-530).
--
-- SFN/ECS 실행을 **사후 복구 가능하게 관측**하는 projection 이다. Step Functions 실행 이력은
-- 만료되므로 운영 정본을 여기 남긴다. 이 원장은 파이프라인 실행을 **제어하지 않는다**(관측만) —
-- 완전성 불충족이 downstream 을 차단하지 않는다(ADR-0030 "관측만, 차단 안 함"). data_status 는
-- future gate(ALPHA-452/453)의 정본이 아니라 관측값이며, `gate_decision` 물리 컬럼을 두지 않는다.
--
-- **핵심 질문**: 원래 실행돼야 했지만 아예 시작되지 않은 작업은 무엇인가. 이를 위해 Planner 가
-- 실제 실행 **전에** pipeline_run + expected_task 를 원자적으로 남기고, Reconciler 가 예정과
-- 실제(SFN/ECS 증거)를 대조한다 — "attempt 행 없음"만으로 MISSED 를 단정하지 않는다.
--
-- **상태 4축을 섞지 않는다**(스펙 §3.2):
--   plan_status               DUE·SKIPPED                         (원래 실행 대상이었나)
--   task_outcome              PENDING·FULFILLED·FAILED·BLOCKED·MISSED  (논리 작업의 최종 귀결)
--   task_attempt.execution_status  RUNNING·SUCCEEDED·FAILED·TIMED_OUT  (물리 실행 시도 하나)
--   data_status               UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID  (산출 데이터)
--   STALLED 는 저장 상태가 아니라 RUNNING+시간초과로 파생하는 health projection 이다(컬럼 없음).
-- 교차 테이블 불변식(SKIPPED→attempt 금지 등)은 SQL CHECK 로 무리하게 넣지 않고 서비스·
-- Reconciler·테스트로 보장한다(스펙 §3.2). 여기 CHECK 는 단일 컬럼 어휘 검증에 한정한다.
--
-- **artifact_manifest 테이블을 두지 않는 근거**(스펙 §4): 기존 S3 로그
-- (operations_archive/collection_logs·data_quality_logs)가 이미 (dataset, date, run_id) 로
-- 건수·실패목록·canonical_written 을 담아 artifact/data 상태를 복원한다. 요약 완전성은
-- expected_task.completeness(JSONB, 큰 목록은 S3 URI)로 싣고, 상세는 그 S3 로그가 정본이다.
--
-- ID 는 ADR-0027 계열(TEXT, ULID 또는 결정적 해시). run_key·execution_name·dedupe_key 로 멱등을
-- 잡는다. FK 는 원장 내부로만 건다(public 도메인 테이블 수명주기와 결합하지 않는다 — 원장은 옆에
-- 붙는 관측 계층이라 도메인 행이 지워져도 운영 이력은 남아야 한다).

SET search_path TO public;

-- ── pipeline_run — 하나의 논리적 파이프라인 실행 ─────────────────────────
CREATE TABLE ops_pipeline_run (
    pipeline_run_id        TEXT NOT NULL,
    -- 스케줄 슬롯 멱등키(예: 'daily:2026-07-24'). Planner 재기동이 같은 슬롯을 두 번 만들지
    -- 않게 하는 정본. run_key 가 곧 "이 슬롯은 한 번만 계획된다"는 계약이다.
    run_key                TEXT NOT NULL,
    pipeline_type          TEXT NOT NULL,
    schedule_slot          TEXT,
    trading_date           DATE,
    hard_deadline_at       TIMESTAMPTZ,
    catalog_version        TEXT,          -- 배포 Git SHA(카탈로그 재현)
    catalog_content_hash   TEXT,          -- 실제 카탈로그 내용의 결정적 해시
    image_digest           TEXT,          -- 실행 이미지 digest(있으면)
    -- 결정적 execution name(pipeline_run_id 기반) — SFN StartExecution 멱등의 근거.
    execution_name         TEXT NOT NULL,
    input_hash             TEXT,          -- SFN 입력의 결정적 직렬화 해시
    -- 계산된 조회용 ARN — **locator 일 뿐 실행 존재의 증거가 아니다**. 실제 확인 전엔 신뢰 금지.
    expected_execution_arn TEXT,
    -- 실제 존재가 확인된 뒤에만 채운다(DescribeExecution 성공).
    sfn_execution_arn      TEXT,
    -- PLANNING→LAUNCHED / LAUNCH_FAILED / LAUNCH_CONFLICT / LAUNCH_UNKNOWN(불분명, Reconciler 확인)
    launch_status          TEXT NOT NULL DEFAULT 'PLANNING',
    -- RUNNING·SUCCEEDED·FAILED·TIMED_OUT·ABORTED·UNKNOWN (Reconciler 가 DescribeExecution 으로 동기화)
    orchestration_status   TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (pipeline_run_id),
    CONSTRAINT uq_ops_pipeline_run_key UNIQUE (run_key),
    CONSTRAINT uq_ops_pipeline_run_execution_name UNIQUE (execution_name),
    CONSTRAINT ck_ops_pipeline_run_launch CHECK (
        launch_status IN ('PLANNING','LAUNCHED','LAUNCH_FAILED','LAUNCH_CONFLICT','LAUNCH_UNKNOWN')
    ),
    CONSTRAINT ck_ops_pipeline_run_orch CHECK (
        orchestration_status IS NULL OR orchestration_status IN
        ('RUNNING','SUCCEEDED','FAILED','TIMED_OUT','ABORTED','UNKNOWN')
    )
);

COMMENT ON TABLE ops_pipeline_run IS
'논리적 파이프라인 실행 1건. run_key 로 슬롯 멱등, execution_name 으로 SFN 멱등. expected_execution_arn 은 locator 일 뿐 실행 증거가 아니다 — sfn_execution_arn 은 확인 뒤에만 채운다.';

-- ── expectation_snapshot — 기대 universe 재현 ─────────────────────────
-- 과거 날짜 재실행 시 현재가 아니라 당시 스냅샷과 대조하려면 기대 집합을 실행 시점에 고정해야
-- 한다(스펙 §6). 가격 유니버스는 수십 종(ETF 31·holdings 파생)이라 작은 집합 → JSONB inline.
-- 큰 집합을 위한 storage_uri/content_hash 도 둔다(스펙 §4: 크면 immutable S3 + hash). S3 를
-- 쓰면 lifecycle 만료 대상이 아니어야 한다(운영 문서에 보존 정책 명시).
CREATE TABLE ops_expectation_snapshot (
    expectation_snapshot_id TEXT NOT NULL,
    pipeline_run_id         TEXT,
    task_key                TEXT,
    universe_version        TEXT,
    constituent_as_of_date  DATE,
    entity_kind             TEXT,          -- 예: 'ticker'
    expected_entity_count   INTEGER NOT NULL DEFAULT 0,
    -- 작은 집합: 통째 저장. 큰 집합: NULL 로 두고 storage_uri 사용.
    entity_ids              JSONB,
    storage_uri             TEXT,
    content_hash            TEXT,          -- 재현 검증(내용 결정적 해시)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (expectation_snapshot_id),
    CONSTRAINT ck_ops_snapshot_body CHECK (entity_ids IS NOT NULL OR storage_uri IS NOT NULL)
);

COMMENT ON TABLE ops_expectation_snapshot IS
'실행 시점에 고정한 기대 universe(재현용). 작은 집합은 entity_ids(JSONB), 큰 집합은 storage_uri+content_hash. 과거 재실행은 현재가 아니라 이 스냅샷과 대조한다.';

-- ── expected_task — 원래 실행돼야 했던 논리 작업 ─────────────────────────
CREATE TABLE ops_expected_task (
    expected_task_id        TEXT NOT NULL,
    pipeline_run_id         TEXT NOT NULL,
    task_key                TEXT NOT NULL,   -- 안정적 카탈로그 ID(CLI/state name 아님)
    stage                   TEXT NOT NULL,
    dataset                 TEXT,
    plan_status             TEXT NOT NULL DEFAULT 'DUE',   -- DUE·SKIPPED
    task_outcome            TEXT,            -- PENDING·FULFILLED·FAILED·BLOCKED·MISSED (SKIPPED 이면 NULL)
    data_status             TEXT,            -- UNKNOWN·VALID·VALID_EMPTY·INCOMPLETE·INVALID (SKIPPED 이면 NULL)
    required                BOOLEAN NOT NULL DEFAULT TRUE,
    expected_at             TIMESTAMPTZ,
    deadline_at             TIMESTAMPTZ,
    eligible_at             TIMESTAMPTZ,     -- 실제 실행 가능해진 시각(upstream 충족)
    missed_at               TIMESTAMPTZ,     -- MISSED 판정 시각(비래치 — FULFILLED 로 가도 보존)
    fulfilled_at            TIMESTAMPTZ,
    blocked_at              TIMESTAMPTZ,
    expected_as_of_date     DATE,
    expectation_snapshot_id TEXT,
    skip_reason             TEXT,            -- 예: NON_TRADING_DAY
    outcome_reason          TEXT,            -- 예: FAILED_TO_START
    -- 현재 결과와 연결된 attempt 또는 증거 식별자(SFN state·ECS ARN 등).
    current_attempt_id      TEXT,
    -- 완전성 요약(스펙 §6): {expected_count, received_count, missing_count, missing_uri}.
    -- 큰 목록(missing_entity_ids)은 여기 넣지 않고 missing_uri(S3)로 가리킨다.
    completeness            JSONB,
    idempotency_key         TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (expected_task_id),
    CONSTRAINT uq_ops_expected_task_run_key UNIQUE (pipeline_run_id, task_key),
    CONSTRAINT fk_ops_expected_task_run FOREIGN KEY (pipeline_run_id)
        REFERENCES ops_pipeline_run (pipeline_run_id) ON DELETE CASCADE,
    CONSTRAINT fk_ops_expected_task_snapshot FOREIGN KEY (expectation_snapshot_id)
        REFERENCES ops_expectation_snapshot (expectation_snapshot_id) ON DELETE SET NULL,
    CONSTRAINT ck_ops_expected_task_plan CHECK (plan_status IN ('DUE','SKIPPED')),
    CONSTRAINT ck_ops_expected_task_outcome CHECK (
        task_outcome IS NULL OR task_outcome IN
        ('PENDING','FULFILLED','FAILED','BLOCKED','MISSED')
    ),
    CONSTRAINT ck_ops_expected_task_data CHECK (
        data_status IS NULL OR data_status IN
        ('UNKNOWN','VALID','VALID_EMPTY','INCOMPLETE','INVALID')
    )
);

COMMENT ON TABLE ops_expected_task IS
'실행 전 Planner 가 남기는 논리 작업. plan_status(DUE/SKIPPED)·task_outcome·data_status 는 별개 축이다. 종목 누락은 이 행이 아니라 completeness(요약)+S3 로그(상세)로 관리한다. 재시도는 새 행이 아니라 새 task_attempt.';

CREATE INDEX ix_ops_expected_task_run ON ops_expected_task (pipeline_run_id);
CREATE INDEX ix_ops_expected_task_outcome ON ops_expected_task (task_outcome)
    WHERE task_outcome IN ('PENDING','MISSED','BLOCKED');

-- ── task_attempt — 물리적 실행 시도 하나 ─────────────────────────
-- ECS Task ARN 이 생성되지 않은 RunTask submit/start 실패는 여기 가짜 행으로 남기지 않는다
-- (스펙 §6). 그런 실패는 expected_task.task_outcome=FAILED + outcome_reason=FAILED_TO_START 로만
-- 드러낸다. 멱등키는 (expected_task_id, ecs_task_arn) — MAX(attempt_number)+1 을 멱등 수단으로
-- 쓰지 않는다(경쟁 시 중복 발번). attempt_number 는 표시용으로만 안전 생성한다.
CREATE TABLE ops_task_attempt (
    attempt_id          TEXT NOT NULL,
    expected_task_id    TEXT NOT NULL,
    attempt_number      INTEGER,           -- 표시용(멱등 수단 아님)
    ecs_task_arn        TEXT NOT NULL,     -- 실제 ARN(가짜 행 금지)
    execution_status    TEXT NOT NULL,     -- RUNNING·SUCCEEDED·FAILED·TIMED_OUT
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    exit_code           INTEGER,
    failure_reason      TEXT,
    sfn_execution_arn   TEXT,
    sfn_state_name      TEXT,
    -- 이 시도에서 관측한 데이터 상태(있으면). expected_task.data_status 는 최신 결과 반영.
    data_status         TEXT,
    -- 원장 기록/복구 출처: WRAPPER(정상 계측) · RECONCILER_BACKFILL(누락 사후 복구).
    record_source       TEXT NOT NULL DEFAULT 'WRAPPER',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (attempt_id),
    CONSTRAINT uq_ops_task_attempt_ecs UNIQUE (expected_task_id, ecs_task_arn),
    CONSTRAINT fk_ops_task_attempt_expected FOREIGN KEY (expected_task_id)
        REFERENCES ops_expected_task (expected_task_id) ON DELETE CASCADE,
    CONSTRAINT ck_ops_task_attempt_exec CHECK (
        execution_status IN ('RUNNING','SUCCEEDED','FAILED','TIMED_OUT')
    ),
    CONSTRAINT ck_ops_task_attempt_data CHECK (
        data_status IS NULL OR data_status IN
        ('UNKNOWN','VALID','VALID_EMPTY','INCOMPLETE','INVALID')
    )
);

COMMENT ON TABLE ops_task_attempt IS
'논리 작업 1건에 대한 물리 실행 시도. (expected_task_id, ecs_task_arn) 멱등. ECS ARN 없는 submit 실패는 여기 안 남긴다. record_source 로 정상 계측(WRAPPER)과 사후 복구(RECONCILER_BACKFILL)를 구분한다.';

CREATE INDEX ix_ops_task_attempt_expected ON ops_task_attempt (expected_task_id);

-- ── reconciliation_issue — Reconciler 가 여는 불일치 ─────────────────────────
-- 열린 이슈만 유니크(같은 문제 해결 후 재발 시 새 이슈 허용). occurrence_count 로 반복 억제
-- (같은 상태 반복 탐지가 무한 중복 레코드·알림을 만들지 않게).
CREATE TABLE ops_reconciliation_issue (
    issue_id            TEXT NOT NULL,
    -- MISSED·STALLED·INCOMPLETE·LEDGER_GAP·EVIDENCE_LOST·PLANNER_MISSING·LAUNCH_CONFLICT 등
    issue_type          TEXT NOT NULL,
    scope               TEXT,              -- run·task·slot
    scope_key           TEXT,
    dedupe_key          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'OPEN',   -- OPEN·RESOLVED
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    resolution_reason   TEXT,
    resolution_source   TEXT,
    evidence            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    PRIMARY KEY (issue_id),
    CONSTRAINT ck_ops_issue_status CHECK (status IN ('OPEN','RESOLVED'))
);

COMMENT ON TABLE ops_reconciliation_issue IS
'Reconciler 가 여는 예정↔실제 불일치. 열린 이슈만 dedupe(부분 유니크), occurrence_count 로 반복 억제. 해결 후 재발은 새 이슈.';

-- 열린 이슈에만 유니크 — 해결(RESOLVED) 뒤 같은 dedupe_key 재발이 새 OPEN 을 만들 수 있게.
CREATE UNIQUE INDEX uq_ops_issue_open_dedupe
    ON ops_reconciliation_issue (dedupe_key) WHERE status = 'OPEN';
CREATE INDEX ix_ops_issue_status_type ON ops_reconciliation_issue (status, issue_type);
