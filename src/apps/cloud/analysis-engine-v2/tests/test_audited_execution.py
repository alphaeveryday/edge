"""Agent-visible tool results must already have committed evidence."""
from unittest.mock import Mock

import pytest

from edge_analysis_v2.tools.execution import AuditedExecution, ToolExecutionError


def executor(call, store):
    return AuditedExecution(call, store, definitions=[dict(
        tool_id="sum:v1", function_name="sum", version="v1", description="Sum",
        source_names=["daily flow"], formula_latex="S=\\sum x")],
        analysis_kind="movement", analysis_id="a1", context={"analysis_at": "2026-09-21T10:00:00+09:00"})


def test_returns_only_committed_response_and_preserves_original_arguments():
    output = {"tool_run_id": "r1", "result": {"amount_krw": 18}}
    def call(name, arguments):
        arguments["days"] = 99
        return output
    store = Mock()
    store.save_run.return_value = output
    runtime = executor(call, store)
    arguments = {"days": 5}
    assert runtime.call("sum", arguments) is output
    assert arguments == {"days": 5}
    saved = store.save_run.call_args.kwargs
    assert saved["arguments"] == arguments
    assert saved["output"] is output
    assert saved["tool_id"] == "sum:v1"
    assert saved["finished_at"] >= saved["started_at"]


def test_calculation_error_is_recorded_without_leaking_exception_text(caplog):
    store = Mock()
    def call(name, arguments):
        raise ValueError("password=secret")
    runtime = executor(call, store)
    with pytest.raises(ToolExecutionError) as raised:
        runtime.call("sum", {"days": 5})
    saved = store.save_run.call_args.kwargs
    assert saved["output"] is None
    assert saved["tool_run_id"] == raised.value.tool_run_id
    assert "secret" not in saved["error_message"]
    assert "secret" not in str(raised.value)
    # WHY(ALPHA-1162): the agent and the audit row see only a generic failure, so without this line the cause was
    # nowhere. Operators get the type and the raising line, still never the text.
    assert f"tool_run_id={raised.value.tool_run_id} type=ValueError at=test_audited_execution.py:" in caplog.text
    assert "in call" in caplog.text and "secret" not in caplog.text


def test_storage_failure_never_becomes_success_or_triggers_retry():
    call = Mock(return_value={"tool_run_id": "r1", "result": 18})
    store = Mock()
    store.save_run.side_effect = ConnectionError("database unavailable")
    with pytest.raises(ConnectionError):
        executor(call, store).call("sum", {})
    call.assert_called_once()
    store.save_run.assert_called_once()


def test_unknown_tool_is_not_executed():
    call, store = Mock(), Mock()
    with pytest.raises(ValueError, match="Unknown"):
        executor(call, store).call("other", {})
    call.assert_not_called()
    store.save_run.assert_not_called()


def test_definition_registration_failure_prevents_execution():
    call, store = Mock(), Mock()
    store.register_definition.side_effect = ValueError("definition conflict")
    with pytest.raises(ValueError, match="conflict"):
        executor(call, store)
    call.assert_not_called()
