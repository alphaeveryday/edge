"""Invalid evidence must fail before any database write."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from edge_analysis_v2.tool_store import ToolStore


def connection(*, autocommit=True, status=TransactionStatus.IDLE):
    return SimpleNamespace(autocommit=autocommit, info=SimpleNamespace(transaction_status=status))


def run_arguments():
    now = datetime.now(timezone.utc)
    return dict(tool_run_id="run-1", tool_id="sum:v1", analysis_kind="movement",
                analysis_id="analysis-1", arguments={"days": 5}, context={},
                output={"tool_run_id": "run-1", "result": {"amount_krw": 18}},
                started_at=now, finished_at=now)


@pytest.mark.parametrize("kwargs", [{"autocommit": False}, {"status": TransactionStatus.INTRANS}])
def test_audit_must_not_share_a_transaction_that_can_later_roll_back(kwargs):
    with pytest.raises(ValueError, match="dedicated"):
        ToolStore(connection(**kwargs))


@pytest.mark.parametrize("change", [
    {"output": {"tool_run_id": "another-run", "result": 18}},
    {"output": {"tool_run_id": "run-1", "result": float("nan")}},
    {"arguments": {"days": float("inf")}},
    {"arguments": {1: "would change JSON key type"}},
    {"analysis_kind": "arbitrary-table"},
    {"started_at": datetime(2026, 9, 21)},
    {"finished_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
    {"output": None},
    {"error_message": "cannot be both success and failure"},
])
def test_invalid_fact_or_identity_is_not_sent_to_database(change):
    with pytest.raises(ValueError):
        ToolStore(connection()).save_run(**(run_arguments() | change))


def test_entering_an_outer_transaction_after_construction_is_also_rejected():
    conn = connection()
    store = ToolStore(conn)
    conn.info.transaction_status = TransactionStatus.INTRANS
    with pytest.raises(ValueError, match="dedicated"):
        store.save_run(**run_arguments())
