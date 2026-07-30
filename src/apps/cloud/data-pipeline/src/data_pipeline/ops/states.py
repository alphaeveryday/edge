"""운영 원장 상태 4축 어휘 + 이슈/사유 코드 (ALPHA-530, 스펙 §3.2).

네 축을 **섞지 않는다** — 각 축은 서로 다른 질문에 답한다:

- plan_status        : 원래 실행 대상이었나            (DUE / SKIPPED)
- task_outcome       : 논리 작업의 최종 귀결            (PENDING / FULFILLED / FAILED / BLOCKED / MISSED)
- execution_status   : 물리 실행 시도 하나의 결과       (RUNNING / SUCCEEDED / FAILED / TIMED_OUT)
- data_status        : 산출 데이터 상태(exit code 독립) (UNKNOWN / VALID / VALID_EMPTY / INCOMPLETE / INVALID)

STALLED 는 저장 상태가 아니라 RUNNING+시간초과로 **파생**하는 health 값이다(컬럼 없음).

의미 구분(스펙 §3.2):
  attempt.FAILED  = 특정 ECS 시도 하나가 실패
  outcome.FAILED  = 재시도 포함 논리 작업이 최종 실패
  BLOCKED         = upstream/gate 때문에 실행 대상이 되지 못함
  MISSED          = 실제로 실행 가능해진 뒤에도 deadline 까지 그 state 가 실행되지 않음

이 상수들은 마이그레이션의 CHECK 어휘와 **정확히 일치**해야 한다(V202607240001).
"""

from __future__ import annotations

# ── plan_status ──
PLAN_DUE = "DUE"
PLAN_SKIPPED = "SKIPPED"
PLAN_STATUSES = frozenset({PLAN_DUE, PLAN_SKIPPED})

# ── task_outcome ──
OUTCOME_PENDING = "PENDING"
OUTCOME_FULFILLED = "FULFILLED"
OUTCOME_FAILED = "FAILED"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_MISSED = "MISSED"
TASK_OUTCOMES = frozenset(
    {OUTCOME_PENDING, OUTCOME_FULFILLED, OUTCOME_FAILED, OUTCOME_BLOCKED, OUTCOME_MISSED}
)

# ── attempt.execution_status ──
EXEC_RUNNING = "RUNNING"
EXEC_SUCCEEDED = "SUCCEEDED"
EXEC_FAILED = "FAILED"
EXEC_TIMED_OUT = "TIMED_OUT"
EXECUTION_STATUSES = frozenset({EXEC_RUNNING, EXEC_SUCCEEDED, EXEC_FAILED, EXEC_TIMED_OUT})

# ── data_status ──
DATA_UNKNOWN = "UNKNOWN"
DATA_VALID = "VALID"
DATA_VALID_EMPTY = "VALID_EMPTY"
DATA_INCOMPLETE = "INCOMPLETE"
DATA_INVALID = "INVALID"
DATA_STATUSES = frozenset(
    {DATA_UNKNOWN, DATA_VALID, DATA_VALID_EMPTY, DATA_INCOMPLETE, DATA_INVALID}
)

# ── pipeline_run.launch_status ──
LAUNCH_PLANNING = "PLANNING"
LAUNCH_LAUNCHED = "LAUNCHED"
LAUNCH_FAILED = "LAUNCH_FAILED"
LAUNCH_CONFLICT = "LAUNCH_CONFLICT"
LAUNCH_UNKNOWN = "LAUNCH_UNKNOWN"
LAUNCH_STATUSES = frozenset(
    {LAUNCH_PLANNING, LAUNCH_LAUNCHED, LAUNCH_FAILED, LAUNCH_CONFLICT, LAUNCH_UNKNOWN}
)

# ── pipeline_run.orchestration_status (SFN describe 동기화) ──
ORCH_RUNNING = "RUNNING"
ORCH_SUCCEEDED = "SUCCEEDED"
ORCH_FAILED = "FAILED"
ORCH_TIMED_OUT = "TIMED_OUT"
ORCH_ABORTED = "ABORTED"
ORCH_UNKNOWN = "UNKNOWN"

# ── reconciliation_issue.issue_type ──
ISSUE_MISSED = "MISSED"
ISSUE_STALLED = "STALLED"
ISSUE_INCOMPLETE = "INCOMPLETE"
ISSUE_LEDGER_GAP = "LEDGER_GAP"
ISSUE_EVIDENCE_LOST = "EVIDENCE_LOST"
ISSUE_PLANNER_MISSING = "PLANNER_MISSING"
ISSUE_LAUNCH_CONFLICT = "LAUNCH_CONFLICT"
# Planner 는 pipeline_run 을 남겼는데 SFN 실행이 확인되지 않는다(StartExecution 전/중 크래시).
# 스케줄러 RunTask 성공이 Planner 프로세스 성공을 보장하지 않아 생기는 fail-loud 공백을 메운다.
ISSUE_LAUNCH_UNCONFIRMED = "LAUNCH_UNCONFIRMED"

# ── 사유 코드(outcome_reason / skip_reason / record_source) ──
SKIP_NON_TRADING_DAY = "NON_TRADING_DAY"
REASON_FAILED_TO_START = "FAILED_TO_START"          # RunTask submit/start 실패(ECS ARN 없음)
REASON_DEADLINE_UNMET = "DEADLINE_UNMET_UPSTREAM"   # hard deadline 뒤에도 upstream 미완 → BLOCKED
SOURCE_WRAPPER = "WRAPPER"
SOURCE_RECONCILER_BACKFILL = "RECONCILER_BACKFILL"
