"""Hypothesis model-tool boundary regression tests.

The legacy ``sql_tool`` parameter remains until its removal PR, but model-produced SQL
must fail before that callable can execute. ObjectSet calls remain structured and bounded.
"""
from __future__ import annotations

import duckdb
import pytest

from edge_analysis.observability import collect_trace
from edge_analysis.statics import sqltool
from edge_analysis.statics.hypothesize import MAX_ASKS, propose
from edge_analysis.statics.model_contract import ModelSchemaError


class _Ask:
    """스크립트대로 답하고 system·user 를 전부 남기는 ask 대역."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)
        self.systems: list[str] = []
        self.users: list[str] = []

    def __call__(self, system: str, user: str) -> dict:
        self.systems.append(system)
        self.users.append(user)
        return self.replies.pop(0) if self.replies else {"hypotheses": []}


def _tool(call) -> dict:
    return {"name": "sql", "description": "읽기 전용 DuckDB SQL.", "call": call}


def _propose(ask, tool):
    return propose(ask, facts="[셀] 사실", event_types=["CONTRACT.SIGNING"],
                   measurable=[("수급", "누적")], sql_tool=tool)


def test_sql_result_lands_in_the_next_prompt():
    """Even an offered legacy SQL tool cannot bypass the model-output contract."""
    calls: list[str] = []

    def call(sql: str) -> dict:
        calls.append(sql)
        return {"ok": True, "columns": ["c"], "rows": [[42]]}

    ask = _Ask([{"sql": "SELECT count(*) AS c FROM v_price_daily"},
                {"hypotheses": []}])
    with pytest.raises(ModelSchemaError):
        _propose(ask, _tool(call))
    assert calls == []


def test_round_cap_is_an_honest_termination():
    """상한을 다 쓰면 소진을 **알리고** 마지막 제출을 받는다 — 실행은 정확히 상한만큼."""
    calls: list[str] = []

    def call(sql: str) -> dict:
        calls.append(sql)
        return {"ok": True, "rows": []}

    ask = _Ask([{"sql": "SELECT 1"}] * 20)   # 모델이 끝없이 조회만 하려 든다
    with pytest.raises(ModelSchemaError):
        _propose(ask, _tool(call))
    assert calls == []


def test_rejection_reason_is_fed_back_verbatim():
    """allowlist 거부의 reason 이 다음 프롬프트에 그대로 실린다 — 모델이 고칠 재료다."""
    reason = "allowlist 밖 참조: secret · 허용 관계: v_price_daily"

    def call(sql: str) -> dict:
        return {"ok": False, "reason": reason}

    ask = _Ask([{"sql": "SELECT * FROM secret"}, {"hypotheses": []}])
    with pytest.raises(ModelSchemaError):
        _propose(ask, _tool(call))


def test_without_tool_the_flow_is_the_current_single_shot():
    """sql_tool 미주입 = 현행 주입식 그대로 — 프롬프트에 툴 언급도 없다(하위호환)."""
    ask = _Ask([{"hypotheses": []}] * MAX_ASKS)
    propose(ask, facts="f", event_types=["CONTRACT.SIGNING"],
            measurable=[("수급", "누적")])

    assert len(ask.users) == MAX_ASKS
    assert all("[탐색 도구" not in s for s in ask.systems)
    assert all("sql" not in u for u in ask.users)


def test_tool_offered_but_unused_costs_nothing():
    """툴을 열어도 모델이 즉시 tuples 로 답하면 왕복 0 — 성능 저하 없는 개방."""
    ask = _Ask([{"hypotheses": []}] * MAX_ASKS)
    with collect_trace() as trace:
        _propose(ask, _tool(lambda sql: {"ok": True, "rows": []}))

    assert len(ask.users) == MAX_ASKS
    [obs] = [e for e in trace if e.get("event") == "hypothesize.sql_rounds"]
    assert obs["rounds"] == 0


def test_real_sqltool_is_not_called_by_model_output():
    """The concrete legacy adapter is also blocked, not only a test double."""
    class _Lake:
        def __init__(self) -> None:
            self.con = duckdb.connect()
            self.con.execute("CREATE VIEW v_price_daily AS SELECT 7 AS n")
            self.bound = {"price_daily": "available_at"}

    tool = sqltool.tool_spec(_Lake())
    ask = _Ask([{"sql": "SELECT n FROM v_price_daily"}, {"hypotheses": []}])
    with collect_trace() as trace, pytest.raises(ModelSchemaError):
        _propose(ask, tool)
    assert not any(e.get("event") == "sqltool.query" for e in trace)


def _object_tools(call) -> dict:
    return {
        "specs": [{"name": "objectset.create", "description": "create a set",
                   "input_schema": {"type": "object"}}],
        "call": call,
    }


def test_objectset_result_lands_in_next_prompt_without_a_query_text_contract():
    calls: list[tuple[str, dict]] = []

    def call(name: str, arguments: dict) -> dict:
        calls.append((name, arguments))
        return {"ok": True, "handle": "os_123", "lineage_id": "lin_123"}

    ask = _Ask([
        {"tool": "objectset.create", "arguments": {"kind": "price_daily"}},
        {"hypotheses": []},
    ])
    with collect_trace() as trace:
        propose(ask, facts="f", event_types=["CONTRACT.SIGNING"],
                measurable=[("수급", "누적")], object_tools=_object_tools(call))

    assert calls == [("objectset.create", {"kind": "price_daily"})]
    assert "[ObjectSet 결과 1/" in ask.users[1]
    assert '"lineage_id": "lin_123"' in ask.users[1]
    assert all('{"sql"' not in system.lower() for system in ask.systems)
    [obs] = [e for e in trace if e.get("event") == "hypothesize.objectset_rounds"]
    assert obs["rounds"] == 1 and obs["rejected"] == 0


def test_objectset_mode_rejects_and_audits_a_model_sql_field():
    calls: list[tuple[str, dict]] = []
    ask = _Ask([{"sql": "SELECT * FROM secret"}, {"hypotheses": []}])

    with collect_trace() as trace, pytest.raises(ModelSchemaError):
        propose(ask, facts="f", event_types=["CONTRACT.SIGNING"],
                measurable=[("수급", "누적")],
                object_tools=_object_tools(lambda name, args: calls.append((name, args))))

    assert calls == []
    [obs] = [e for e in trace if e.get("event") == "llm.model_schema_rejected"]
    assert obs["code"] == "MODEL_SCHEMA_REJECTED" and obs["keys"] == ["sql"]
