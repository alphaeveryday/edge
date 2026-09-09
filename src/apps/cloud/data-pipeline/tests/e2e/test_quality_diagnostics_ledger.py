"""ALPHA-1067: 실제 PostgreSQL rollback 뒤 변경된 재시도 진단이 attempt별로 복구된다."""

import json
import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")


def _diagnostics(resolved: int) -> dict:
    unresolved = 1 - resolved
    return {
        "schema": "news_resolution_v1",
        "scope": "assertion_arguments",
        "metrics": {"total": 1, "resolved": resolved, "unresolved": unresolved},
        "issues": [] if resolved else [{
            "reason": "instrument_not_found", "role": "ISSUER",
            "expression": "미등록회사", "count": 1,
            "sample": {"articleId": "a1", "title": "미등록회사 수주"},
        }],
    }


def test_changed_retry_diagnostics_recover_after_real_postgres_rollback():
    """WHY: 첫 응답을 쓰다 SQL abort가 나도 그 JSONB가 남지 않고, 같은 논리 작업의
    다음 ECS attempt가 낸 변경 응답만 새 행에 저장돼야 복구 후 화면이 옛 원인을 안 보인다."""
    from data_pipeline.config import DbConfig
    from data_pipeline.db import connect
    from data_pipeline.ops import states
    from data_pipeline.ops.ledger import Ledger

    suffix = uuid4().hex
    run_id, task_id = f"run_quality_{suffix}", f"task_quality_{suffix}"
    db = DbConfig(
        host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
        name="edge", user="edge", password="edge", sslmode="disable",
    )
    ledger = Ledger(db=db)
    try:
        with connect(db) as conn:
            conn.execute(
                "INSERT INTO ops_pipeline_run"
                " (pipeline_run_id,run_key,pipeline_type,execution_name) VALUES (%s,%s,%s,%s)",
                (run_id, f"quality:{suffix}", "news", f"quality-{suffix}"),
            )
            conn.execute(
                "INSERT INTO ops_expected_task"
                " (expected_task_id,pipeline_run_id,task_key,stage,task_outcome,data_status,"
                " idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (task_id, run_id, "LOAD_ASSERTIONS", "load", "PENDING", "UNKNOWN", suffix),
            )

        first_id = ledger.record_attempt_start(
            expected_task_id=task_id, ecs_task_arn=f"arn:task:first:{suffix}",
        )
        with pytest.raises(Exception):
            with connect(db) as conn:
                conn.execute(
                    "UPDATE ops_task_attempt SET quality_diagnostics=%s::jsonb"
                    " WHERE attempt_id=%s",
                    (json.dumps(_diagnostics(0), ensure_ascii=False), first_id),
                )
                conn.execute("SELECT 1/0")

        with connect(db) as conn:
            assert conn.execute(
                "SELECT quality_diagnostics FROM ops_task_attempt WHERE attempt_id=%s",
                (first_id,),
            ).fetchone() == (None,)
        assert ledger.record_attempt_end(
            first_id, execution_status=states.EXEC_FAILED, exit_code=1,
            failure_reason="step_nonzero_exit", data_status=states.DATA_UNKNOWN,
        )

        retry_id = ledger.record_attempt_start(
            expected_task_id=task_id, ecs_task_arn=f"arn:task:retry:{suffix}",
        )
        recovered = _diagnostics(1)
        assert ledger.record_attempt_end(
            retry_id, execution_status=states.EXEC_SUCCEEDED, exit_code=0,
            data_status=states.DATA_VALID, quality_diagnostics=recovered,
        )

        with connect(db) as conn:
            rows = conn.execute(
                "SELECT attempt_id,quality_diagnostics FROM ops_task_attempt"
                " WHERE expected_task_id=%s ORDER BY attempt_number",
                (task_id,),
            ).fetchall()
        assert rows == [(first_id, None), (retry_id, recovered)]
    finally:
        with connect(db) as conn:
            conn.execute("DELETE FROM ops_pipeline_run WHERE pipeline_run_id=%s", (run_id,))


def test_postgres_attempt_survives_unsafe_log_then_stores_recovered_retry():
    """WHY: 로그의 NUL 하나 때문에 종료 UPDATE가 rollback되면 성공 컨테이너가 RUNNING으로 남는다;
    첫 시도는 NULL로 끝나고 변경된 정상 응답은 다음 시도에 저장돼야 한다."""
    from data_pipeline.config import DbConfig
    from data_pipeline.db import connect
    from data_pipeline.ops import states, wrapper
    from data_pipeline.ops.ledger import Ledger

    suffix = uuid4().hex
    run_id, task_id = f"run_unsafe_{suffix}", f"task_unsafe_{suffix}"
    db = DbConfig(
        host=os.environ["E2E_PGHOST"], port=int(os.environ["E2E_PGPORT"]),
        name="edge", user="edge", password="edge", sslmode="disable",
    )
    ledger = Ledger(db=db)

    def current_attempt_id() -> str:
        with connect(db) as conn:
            return str(conn.execute(
                "SELECT attempt_id FROM ops_task_attempt WHERE expected_task_id=%s"
                " ORDER BY attempt_number DESC LIMIT 1",
                (task_id,),
            ).fetchone()[0])

    try:
        with connect(db) as conn:
            conn.execute(
                "INSERT INTO ops_pipeline_run"
                " (pipeline_run_id,run_key,pipeline_type,execution_name) VALUES (%s,%s,%s,%s)",
                (run_id, f"unsafe:{suffix}", "news", f"unsafe-{suffix}"),
            )
            conn.execute(
                "INSERT INTO ops_expected_task"
                " (expected_task_id,pipeline_run_id,task_key,stage,task_outcome,data_status,"
                " idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (task_id, run_id, "LOAD_ASSERTIONS", "load", "PENDING", "UNKNOWN", suffix),
            )

        unsafe = _diagnostics(0)
        unsafe["issues"][0]["expression"] = "회사\x00"
        assert wrapper.instrument(
            lambda: 0, task_key="LOAD_ASSERTIONS", run_id=run_id, ledger=ledger,
            ecs_task_arn=f"arn:task:unsafe:{suffix}",
            observe_data_fn=lambda ec: {
                "ops_attempt_id": current_attempt_id(), "quality_diagnostics": unsafe,
            },
        ) == 0
        recovered = _diagnostics(1)
        assert wrapper.instrument(
            lambda: 0, task_key="LOAD_ASSERTIONS", run_id=run_id, ledger=ledger,
            ecs_task_arn=f"arn:task:recovered:{suffix}",
            observe_data_fn=lambda ec: {
                "ops_attempt_id": current_attempt_id(), "quality_diagnostics": recovered,
            },
        ) == 0

        with connect(db) as conn:
            rows = conn.execute(
                "SELECT execution_status,quality_diagnostics FROM ops_task_attempt"
                " WHERE expected_task_id=%s ORDER BY attempt_number",
                (task_id,),
            ).fetchall()
        assert rows == [
            (states.EXEC_SUCCEEDED, None),
            (states.EXEC_SUCCEEDED, recovered),
        ]
    finally:
        with connect(db) as conn:
            conn.execute("DELETE FROM ops_pipeline_run WHERE pipeline_run_id=%s", (run_id,))
