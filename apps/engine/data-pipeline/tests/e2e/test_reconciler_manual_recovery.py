"""ALPHA-1063: 실제 PostgreSQL에서 수동 재시도 성공을 Reconciler가 보존한다."""

import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")


class _Sfn:
    def __init__(self, execution_arn: str, *, history: list[dict]):
        self.execution_arn = execution_arn
        self.history = history

    def describe_execution(self, *, executionArn):
        assert executionArn == self.execution_arn
        return {"executionArn": executionArn, "status": "FAILED"}

    def get_execution_history(self, *, executionArn, maxResults=1000, nextToken=None):
        assert executionArn == self.execution_arn
        assert nextToken is None
        return {"events": self.history}


class _Ecs:
    def describe_tasks(self, **_kwargs):
        raise AssertionError("SFN/ledger에 exit code가 있으므로 ECS 조회가 발생하면 안 된다")


def test_reconciler_preserves_later_manual_success_in_real_postgres():
    """WHY: 과거 SFN 실패를 늦게 backfill한 started_at을 믿으면 주기 대조가 이후 수동 성공을
    다시 FAILED로 덮는다. 실제 DB에서도 SFN 진입 시각으로 보정하고 downstream을 열어야 한다."""
    from data_pipeline.config import DbConfig
    from data_pipeline.db import connect
    from data_pipeline.ops import states
    from data_pipeline.ops.ledger import Ledger
    from data_pipeline.ops.reconciler import reconcile_run

    suffix = uuid4().hex
    run_id = f"run_reconcile_{suffix}"
    normalize_id = f"task_normalize_{suffix}"
    load_id = f"task_load_{suffix}"
    legacy_id = f"attempt_legacy_{suffix}"
    manual_id = f"attempt_manual_{suffix}"
    run_key = f"investor-intraday:{suffix}"
    execution_arn = f"arn:aws:states:ap-northeast-2:000000000000:execution:test:{suffix}"
    legacy_arn = f"arn:aws:ecs:ap-northeast-2:000000000000:task/legacy-{suffix}"
    manual_arn = f"arn:aws:ecs:ap-northeast-2:000000000000:task/manual-{suffix}"
    now = datetime.now(timezone.utc)
    sfn_started = now - timedelta(hours=3)
    manual_started = now - timedelta(hours=2)
    late_backfill_started = now - timedelta(hours=1)
    db = DbConfig(
        host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
        name="edge", user="edge", password="edge", sslmode="disable",
    )
    history = [
        {"id": 1, "previousEventId": 0, "timestamp": sfn_started,
         "type": "TaskStateEntered",
         "stateEnteredEventDetails": {"name": "NormalizeInvestorEstimate"}},
        {"id": 2, "previousEventId": 1, "timestamp": sfn_started,
         "type": "TaskSubmitted", "taskSubmittedEventDetails": {
             "output": json.dumps({"TaskArn": legacy_arn})}},
        {"id": 3, "previousEventId": 2, "timestamp": sfn_started,
         "type": "TaskSucceeded", "taskSucceededEventDetails": {
             "output": json.dumps({"Containers": [{"ExitCode": 1}]})}},
    ]

    try:
        with connect(db) as conn:
            conn.execute(
                "INSERT INTO ops_pipeline_run"
                " (pipeline_run_id,run_key,pipeline_type,execution_name,expected_execution_arn,"
                " sfn_execution_arn,launch_status,orchestration_status)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, run_key, "investor-intraday", f"reconcile-{suffix}", execution_arn,
                 execution_arn, states.LAUNCH_LAUNCHED, states.ORCH_FAILED),
            )
            conn.execute(
                "INSERT INTO ops_expected_task"
                " (expected_task_id,pipeline_run_id,task_key,stage,task_outcome,data_status,"
                " eligible_at,current_attempt_id,idempotency_key)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s),(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (normalize_id, run_id, "NORMALIZE_INVESTOR_INTRADAY", "normalize",
                 states.OUTCOME_FULFILLED, states.DATA_VALID, sfn_started, manual_id,
                 f"normalize:{suffix}", load_id, run_id, "LOAD_INVESTOR_INTRADAY", "load",
                 states.OUTCOME_PENDING, states.DATA_UNKNOWN, None, None, f"load:{suffix}"),
            )
            conn.execute(
                "INSERT INTO ops_task_attempt"
                " (attempt_id,expected_task_id,attempt_number,ecs_task_arn,execution_status,"
                " started_at,finished_at,exit_code,record_source)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s),(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (legacy_id, normalize_id, 1, legacy_arn, states.EXEC_FAILED,
                 late_backfill_started, late_backfill_started, 1,
                 states.SOURCE_RECONCILER_BACKFILL,
                 manual_id, normalize_id, 2, manual_arn, states.EXEC_SUCCEEDED,
                 manual_started, manual_started, 0, states.SOURCE_WRAPPER),
            )

        for _ in range(2):
            reconcile_run(
                Ledger(db=db),
                run_key=run_key, now=now, sfn_client=_Sfn(execution_arn, history=history),
                ecs_client=_Ecs(),
            )

        with connect(db) as conn:
            task_rows = conn.execute(
                "SELECT task_key,task_outcome,outcome_reason,eligible_at"
                " FROM ops_expected_task WHERE pipeline_run_id=%s ORDER BY task_key",
                (run_id,),
            ).fetchall()
            corrected_started_at = conn.execute(
                "SELECT started_at FROM ops_task_attempt WHERE attempt_id=%s", (legacy_id,),
            ).fetchone()[0]

        assert task_rows[0][0:3] == ("LOAD_INVESTOR_INTRADAY", states.OUTCOME_PENDING, None)
        assert task_rows[0][3] is not None
        assert task_rows[1][0:3] == (
            "NORMALIZE_INVESTOR_INTRADAY", states.OUTCOME_FULFILLED, None,
        )
        assert corrected_started_at == sfn_started
    finally:
        with connect(db) as conn:
            conn.execute("DELETE FROM ops_pipeline_run WHERE pipeline_run_id=%s", (run_id,))
