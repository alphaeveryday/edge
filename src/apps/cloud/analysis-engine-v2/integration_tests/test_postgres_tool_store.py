"""Explicit local PostgreSQL checks against the Flyway-created schema."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edge_analysis_v2.tool_store import ToolStore
from edge_analysis_v2.audited_execution import AuditedExecution, ToolExecutionError


@pytest.fixture
def audit():
    """Create isolated records in the local development database only."""
    dsn = os.environ["V2_TEST_DSN"]
    params = conninfo_to_dict(dsn)
    if (params.get("host") not in ("localhost", "127.0.0.1")
            or params.get("dbname") != "analysis_v2"
            or params.get("port") != "55439"
            or params.get("hostaddr") not in (None, "127.0.0.1")):
        raise ValueError("Integration tests require the local analysis_v2 database on port 55439")
    key = "audit-test-" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("INSERT INTO movement_analyses (analysis_id, etf_code, analysis_at, trading_date) VALUES (%s, 'TEST', now(), current_date)", (key,))
        conn.execute("INSERT INTO outlook_analyses (analysis_id, etf_code, analysis_at) VALUES (%s, 'TEST', now())", (key,))
        store = ToolStore(conn)
        definition = dict(tool_id=key, function_name=key, version="v1",
                          description="선택한 기간의 순매수 금액 합계", source_names=["일별 투자자 수급"],
                          formula_latex=r"\sum_{d=1}^{N} x_d")
        store.register_definition(**definition)
        now = datetime.now(timezone.utc)
        args = dict(tool_run_id=key, tool_id=key, analysis_kind="movement", analysis_id=key,
                    arguments={"days": 5}, context={"flow_as_of_date": "2026-09-18"},
                    output={"tool_run_id": key, "result": {"amount_krw": 9007199254740993, "label": "순매수"}},
                    started_at=now, finished_at=now)
        try:
            yield store, args, definition, dsn
        finally:
            conn.execute("DELETE FROM tool_runs WHERE tool_id = %s", (key,))
            conn.execute("DELETE FROM tool_definitions WHERE tool_id = %s", (key,))
            conn.execute("DELETE FROM movement_analyses WHERE analysis_id = %s", (key,))
            conn.execute("DELETE FROM outlook_analyses WHERE analysis_id = %s", (key,))


@pytest.mark.parametrize("kind", ["movement", "outlook"])
def test_response_is_committed_unchanged_and_survives_analysis_rollback(audit, kind):
    store, args, definition, dsn = audit
    args["analysis_kind"] = kind
    assert store.save_run(**args) == args["output"]
    with psycopg.connect(dsn) as analysis:
        analysis.execute("UPDATE movement_analyses SET summary = 'unpublished' WHERE analysis_id = %s", (args["analysis_id"],))
        analysis.rollback()
    with psycopg.connect(dsn, autocommit=True) as reader:
        record = ToolStore(reader).get_run(args["tool_run_id"])
    assert record["output"] == args["output"]
    assert record["arguments"] == args["arguments"]
    assert record["context"] == args["context"]
    assert record["formula_latex"] == definition["formula_latex"]
    assert record["source_names"] == definition["source_names"]
    assert record[f"{kind}_analysis_id"] == args["analysis_id"]
    assert record["status"] == "completed"


def test_definition_cannot_change_under_existing_evidence(audit):
    store, args, definition, _ = audit
    store.register_definition(**definition)
    with pytest.raises(ValueError, match="immutable"):
        store.register_definition(**(definition | {"formula_latex": "different"}))
    store.save_run(**args)
    assert store.get_run(args["tool_run_id"])["formula_latex"] == definition["formula_latex"]


def test_failure_is_visible_without_a_fabricated_result(audit):
    store, args, _, _ = audit
    assert store.save_run(**(args | {"output": None, "error_message": "Missing finalized data"})) is None
    record = store.get_run(args["tool_run_id"])
    assert record["status"] == "failed"
    assert record["output"] is None
    assert record["error_message"] == "Missing finalized data"


def test_duplicate_execution_cannot_overwrite_evidence(audit):
    store, args, _, _ = audit
    store.save_run(**args)
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.save_run(**(args | {"arguments": {"days": 30}}))
    assert store.get_run(args["tool_run_id"])["arguments"] == {"days": 5}


def test_missing_analysis_is_rejected_without_orphan_evidence(audit):
    store, args, _, _ = audit
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.save_run(**(args | {"analysis_id": "does-not-exist"}))
    assert store.get_run(args["tool_run_id"]) is None


def test_execution_boundary_returns_committed_response_and_records_failure(audit):
    store, args, definition, dsn = audit
    def calculate(name, arguments):
        if arguments["days"] != 5:
            raise ValueError("private diagnostic")
        return args["output"]
    executor = AuditedExecution(calculate, store, definitions=[definition],
                                analysis_kind="movement", analysis_id=args["analysis_id"],
                                context=args["context"])
    response = executor.call(definition["function_name"], {"days": 5})
    with psycopg.connect(dsn, autocommit=True) as reader:
        assert ToolStore(reader).get_run(response["tool_run_id"])["output"] == response
    with pytest.raises(ToolExecutionError) as raised:
        executor.call(definition["function_name"], {"days": 999})
    failure = store.get_run(raised.value.tool_run_id)
    assert failure["status"] == "failed"
    assert failure["arguments"] == {"days": 999}
    assert "private" not in failure["error_message"]
