"""propose 의 sql 탐색 왕복 계약 (ALPHA-886 2단계) — 각 반례가 지키는 규칙:

  · 모델이 sql 을 요청하면 실행 결과가 **다음 왕복 프롬프트**에 실린다 — 안 실리면
    조회가 제안에 닿지 못하고 툴은 장식이 된다
  · 왕복 상한 초과는 **정직 종료**다 — 조용히 끊으면 무응답과 못 가른다
  · 거부(allowlist 등)는 reason 그대로 되먹임된다 — 오류도 관측이다
  · 툴 미주입(구형 호출자)은 현행 주입식 단발과 동일하다 — 하위호환
  · 질의 감사 record(sqltool.query)와 왕복 관측(hypothesize.sql_rounds)이 남는다
"""
from __future__ import annotations

import duckdb

from edge_analysis.observability import collect_trace
from edge_analysis.statics import sqltool
from edge_analysis.statics.hypothesize import MAX_ASKS, MAX_SQL_ROUNDS, propose


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
    """조회 결과가 다음 왕복 프롬프트에 실려야 조회가 제안 재료가 된다."""
    calls: list[str] = []

    def call(sql: str) -> dict:
        calls.append(sql)
        return {"ok": True, "columns": ["c"], "rows": [[42]]}

    ask = _Ask([{"sql": "SELECT count(*) AS c FROM v_price_daily"},
                {"hypotheses": []}])
    _propose(ask, _tool(call))

    assert calls == ["SELECT count(*) AS c FROM v_price_daily"]
    assert "[탐색 도구" in ask.systems[0] and "읽기 전용 DuckDB SQL." in ask.systems[0]
    assert "[sql 결과 1/" in ask.users[1] and "42" in ask.users[1]
    # 첫 프롬프트에는 아직 결과가 없다 — 실린 위치가 '다음' 왕복이다.
    assert "[sql 결과" not in ask.users[0]


def test_round_cap_is_an_honest_termination():
    """상한을 다 쓰면 소진을 **알리고** 마지막 제출을 받는다 — 실행은 정확히 상한만큼."""
    calls: list[str] = []

    def call(sql: str) -> dict:
        calls.append(sql)
        return {"ok": True, "rows": []}

    ask = _Ask([{"sql": "SELECT 1"}] * 20)   # 모델이 끝없이 조회만 하려 든다
    with collect_trace() as trace:
        valid, _rej = _propose(ask, _tool(call))

    assert len(calls) == MAX_SQL_ROUNDS      # 상한 너머 실행은 없다
    assert valid == []
    assert any("왕복 상한 소진" in u for u in ask.users)
    [obs] = [e for e in trace if e.get("event") == "hypothesize.sql_rounds"]
    assert obs["rounds"] == MAX_SQL_ROUNDS and obs["rejected"] == 0


def test_rejection_reason_is_fed_back_verbatim():
    """allowlist 거부의 reason 이 다음 프롬프트에 그대로 실린다 — 모델이 고칠 재료다."""
    reason = "allowlist 밖 참조: secret · 허용 관계: v_price_daily"

    def call(sql: str) -> dict:
        return {"ok": False, "reason": reason}

    ask = _Ask([{"sql": "SELECT * FROM secret"}, {"hypotheses": []}])
    with collect_trace() as trace:
        _propose(ask, _tool(call))

    assert reason in ask.users[1]
    [obs] = [e for e in trace if e.get("event") == "hypothesize.sql_rounds"]
    assert obs["rounds"] == 1 and obs["rejected"] == 1


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


def test_real_sqltool_keeps_the_query_audit_record():
    """실제 sqltool 을 물렸을 때 질의 감사(sqltool.query)가 trace 에 남는다 —
    왕복 관측 한 줄이 질의 원문 감사를 대체하는 게 아니라 얹힌다."""
    class _Lake:
        def __init__(self) -> None:
            self.con = duckdb.connect()
            self.con.execute("CREATE VIEW v_price_daily AS SELECT 7 AS n")
            self.bound = {"price_daily": "available_at"}

    tool = sqltool.tool_spec(_Lake())
    ask = _Ask([{"sql": "SELECT n FROM v_price_daily"}, {"hypotheses": []}])
    with collect_trace() as trace:
        _propose(ask, tool)

    assert any(e.get("event") == "sqltool.query" for e in trace)
    assert "[sql 결과 1/" in ask.users[1] and '"rows": [[7]]' in ask.users[1]
