"""국내 종목 사건 그래프 도구와 사건 그래프 스킬 (ALPHA-1453, ALPHA-1455).

WHY: 그래프는 기사마다 사건을 따로 뽑아 같은 일이 여러 줄이고, 수치를 문자열로 돌려준다. 모델이 '보도 9건 = 일 9건'으로
읽거나, 단위·기준이 다른 값을 같은 줄에 세우면 선례 판단이 틀린다. 그래프가 꺼져 있으면(평일 05:30~16:00 밖) 도구는
오류를 알려야 하고 세션은 계속돼야 한다. 미국 세션에는 그래프 도구가 열리지 않는다(사건은 국내 뉴스·공시로 만든다).
스킬이 저장값과 다른 이름(단위 계열 PERCENT 등)이나 온톨로지에 없는 코드를 알려 주면 모델이 그대로 따라 해 0건을 받는다.
"""
import re
from datetime import date

import pytest

from edge_truth_research import graph, session, tools
from edge_truth_research.toolspec import GRAPH_TOOLS, specs

TODAY = date(2026, 10, 26)


def _title(kind, body, day="2026-10-12"):
    return f"{kind} / {body} / {day}"


def test_skill_lists_every_ontology_type_and_stays_small():
    text = graph.skill()
    types, labels = graph.registry()
    for code in types:
        head, _, rest = code.partition(".")
        assert re.search(rf"^- {re.escape(head)}: .*{re.escape(rest)} {re.escape(labels.get(code, ''))}", text, re.M), code
    assert len(text) < 12000  # 세션마다 시스템 지시에 붙는다 — 약 4천 토큰 이하


def test_skill_metric_table_names_only_real_type_metric_pairs_in_stored_units():
    # WHY: 표의 (타입·수치·단위)를 그대로 find_events 에 넣는다 — 온톨로지가 거부하거나 저장 단위가 아니면 도구가 오류·0건이다
    types, _ = graph.registry()
    rows = re.findall(r"^\| ([A-Z_.]+) \| ([A-Z_]+) \| ([A-Z]+) \|", graph.skill(), re.M)
    assert len(rows) >= 20
    family = {"KRW": "CURRENCY", "USD": "CURRENCY", "PCT": "PERCENT", "COUNT": "COUNT",
              "DAY": "DURATION_DAYS", "YEARS": "DURATION_DAYS", "MONTHS": "DURATION_DAYS"}
    assert set(family) == set(graph.UNITS)
    for event_type, metric, unit in rows:
        assert event_type in types and metric in types[event_type].quantities, (event_type, metric)
        assert family[unit] == types[event_type].quantities[metric].unit_family, (event_type, metric, unit)  # 금액에 PCT 같은 줄 방지
    assert "PERCENT" not in graph.skill().split("## 4.")[1]  # 표·탐색법에는 저장 단위만


def test_company_events_folds_differently_worded_reports_of_the_same_happening_and_keeps_article_ids():
    rows = [{"type": "COMPANY.CONTRACT.SIGNING", "pred": "SIGN", "d": "2026-10-12", "role": "SUPPLIER", "stage": None,
             "title": _title("COMPANY.CONTRACT.SIGNING", "앤로보틱스 [SUPPLIER]; 비블리오테카(Bibliotheca) [CUSTOMER]; 로미 공급 [CONTRACT_OBJECT]"),
             "article": "a" * 64},
            {"type": "COMPANY.CONTRACT.SIGNING", "pred": "SIGN", "d": "2026-10-12", "role": "SUPPLIER", "stage": None,
             "title": _title("COMPANY.CONTRACT.SIGNING", "앤로보틱스 [SUPPLIER]; 로미의 글로벌 독점 공급 [CONTRACT_OBJECT]; 비블리오테카 [CUSTOMER]"),
             "article": "b" * 64},
            {"type": "COMPANY.ALLIANCE.PARTNERSHIP", "pred": "FORM", "d": "2026-10-05", "role": "PARTNER", "stage": "SIGNED",
             "title": _title("COMPANY.ALLIANCE.PARTNERSHIP", "앤로보틱스 [PARTNER]; 비블리오테카 [PARTNER_2]", "2026-10-05"), "article": None}]
    seen = []
    result = graph.company_events("138360", TODAY, 90, run=lambda q, p: seen.append(p) or rows)
    events = result["events"]
    assert seen[0] == {"code": "138360", "since": "2026-07-28", "limit": graph.COMPANY_LIMIT}
    assert not result["truncated"] and result["oldest"] == "2026-10-05"
    assert [(e["type"], e["reports"]) for e in events] == [("COMPANY.CONTRACT.SIGNING", 2), ("COMPANY.ALLIANCE.PARTNERSHIP", 1)]
    assert events[0]["articles"] == ["a" * 64, "b" * 64]
    assert events[0]["what"] == max((r["title"].split(" / ")[1] for r in rows[:2]), key=len)  # 가장 긴(자세한) 서술을 남긴다
    assert events[1]["stage"] == "SIGNED" and events[1]["articles"] == []


def _measure(eid, value, unit="KRW", basis="TOTAL", vsrc="PARSED", parse="ok", d="2026-10-20", company=None):
    company = company or eid
    return {"id": eid, "d": d, "title": _title("COMPANY.CONTRACT.SIGNING", f"{company} [SUPPLIER]; 공사 [CONTRACT_OBJECT]", d),
            "value": value, "unit": unit, "basis": basis, "vsrc": vsrc, "parse": parse, "who": [f"{company}:SUPPLIER"],
            "ids": [f"actor_{company}"]}


def test_similar_events_compares_decimals_and_counts_other_units_as_unknown_not_as_misses():
    rows = [_measure("e1", "930900000000.00000000"),              # 맞음
            _measure("e2", "26800000000.00000000"),               # 기준 미달
            _measure("e3", "500000000000", basis="UNKNOWN"),      # 기준이 다름 → 모름
            _measure("e4", "82000000", unit="USD"),               # 단위가 다름 → 모름
            _measure("e5", None),                                 # 값 없음 → 모름
            _measure("e6", "200000000000", vsrc="UNRESOLVED"),    # 불확실 → 모름
            _measure("e1", "930900000000.0"),                     # 같은 사건의 다른 측정 → 한 건
            _measure("e7", "930900000000", company="e1"),          # 같은 계약을 다룬 다른 기사의 사건 → 한 건(보도 2)
            _measure("e8", "300000000000", company="mine")]        # 조사 중인 회사의 사건 → 선례에서 뺀다
    def run(q, p):
        return [{"issuer": "actor_mine"}] if q == graph.ISSUER else rows
    found = graph.similar_events("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "100000000000", "KRW", "TOTAL",
                                 TODAY, 180, exclude_code="138360", run=run)
    assert [(m["value"], m["reports"]) for m in found["matched"]] == [("930,900,000,000", 2)]  # 사건 e1·e7(e1 은 측정 2개)
    assert found["unknown"] == 4 and found["scanned"] == 9 and not found["truncated"]


def test_similar_events_without_basis_matches_unknown_basis_and_shows_it():
    # WHY: 저장 기준의 대부분이 UNKNOWN 이다 — 기준을 맞춰야만 세면 계약 선례의 절반이 '모름'으로 빠진다
    rows = [_measure("e1", "500000000000", basis="UNKNOWN"), _measure("e2", "400000000000", basis="TOTAL"),
            _measure("e3", "82000000", unit="USD")]
    found = graph.similar_events("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "100000000000", "KRW", "",
                                 TODAY, 180, run=lambda q, p: rows)
    assert [(m["value"], m["basis"]) for m in found["matched"]] == [("500,000,000,000", "UNKNOWN"), ("400,000,000,000", "TOTAL")]
    assert found["unknown"] == 1  # 단위가 다른 것만 모름


def test_similar_events_reads_news_days_and_disclosure_day_as_one_unit():
    # WHY: 계약 기간은 공시에서 DAY, 뉴스 파서에서 DAYS 로 저장된다 — 한쪽만 맞추면 다른 쪽 선례가 '모름'으로 빠진다
    rows = [_measure("e1", "900", unit="DAY"), _measure("e2", "1000", unit="DAYS")]
    found = graph.similar_events("COMPANY.CONTRACT.SIGNING", "CONTRACT_DURATION", "gte", "365", "DAY", "",
                                 TODAY, 180, run=lambda q, p: rows)
    assert sorted(m["value"] for m in found["matched"]) == ["1,000", "900"] and found["unknown"] == 0


@pytest.mark.parametrize("args, message", [
    (("COMPANY.NOPE", "CONTRACT_VALUE", "gte", "1"), "온톨로지에 없는"),
    (("COMPANY.CONTRACT.SIGNING", "DEAL_VALUE", "gte", "1"), "수치가 아닙니다"),
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gt", "1"), "gte·lte"),
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "nan"), "유한한"),
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "1천억"), "숫자가 아닙니다"),
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "1", "CURRENCY"), "저장 단위가 아닙니다"),  # 단위 계열 이름
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "1", "KRW", "YEARLY"), "기준이 아닙니다"),
])
def test_similar_events_rejects_codes_outside_the_ontology_before_querying(args, message):
    def never(q, p):
        raise AssertionError("잘못된 입력으로 그래프를 조회하지 않는다")
    args = (*args, "KRW", "TOTAL")[:6]
    with pytest.raises(ValueError, match=message):
        graph.similar_events(*args, TODAY, 180, run=never)


def _ws(tmp_path, market="KOSDAQ"):
    from edge_truth_agent.workspace import Workspace
    ws = Workspace.prepare(tmp_path / "ws", mode="research", case=None, inbox=[], index=[], today="2026-10-26",
                           now="2026-10-26T10:35:00+09:00", mover={"code": "138360", "name": "앤로보틱스", "market": market, "filings": []})
    (ws.root / ".pi").mkdir(exist_ok=True)
    return ws


def test_graph_down_is_a_tool_error_the_session_can_read(tmp_path, monkeypatch):
    monkeypatch.delenv("GRAPH_URI", raising=False)
    ws = _ws(tmp_path)
    with pytest.raises(tools.ToolError, match="GRAPH_URI"):
        tools.company_events(ws, {})
    with pytest.raises(tools.ToolError, match="온톨로지에 없는"):
        tools.find_events(ws, {"event_type": "X", "metric": "Y", "op": "gte", "value": "1", "unit": "KRW", "basis": "TOTAL"})


def test_us_sessions_get_no_graph_tools(tmp_path, cfg):
    from dataclasses import replace
    with_graph = replace(cfg, graph_uri="bolt://graph:7687")
    kr, us = _ws(tmp_path / "kr"), _ws(tmp_path / "us", market="NASDAQ")
    assert session._graph(with_graph, kr) and not session._graph(with_graph, us) and not session._graph(cfg, kr)
    with pytest.raises(tools.ToolError, match="국내 종목"):
        tools.company_events(us, {})
    names = [s["name"] for s in specs(web=True, graph=False)]
    assert not set(GRAPH_TOOLS) & set(names)
    assert set(GRAPH_TOOLS) <= {s["name"] for s in specs(web=True, graph=True)}
    cmd = session.command(with_graph, "p", graph=True, system=session._system(kr))
    assert "company_events" in cmd[cmd.index("--tools") + 1]
    system = (kr.root / ".pi" / "system.md").read_text(encoding="utf-8")
    assert "# 사건 그래프 스킬" in system and "COMPANY: " in system and "CONTRACT.SIGNING 계약 체결" in system
    assert session._env(with_graph, us, tmp_path / "c.sqlite")["TRUTH_GRAPH"] == "0"


def test_a_hung_graph_query_returns_as_a_tool_error_within_the_deadline(monkeypatch):
    # WHY: 서버가 시간 초과를 지키지 않으면 도구 프로세스가 세션 시간(420초)까지 묶여 판정을 통째로 잃는다
    import sys
    import threading
    import time
    import types
    release = threading.Event()

    class Driver:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def session(self, **kw):
            return self

        def run(self, *a):
            release.wait(10)
            return []

    fake = types.SimpleNamespace(GraphDatabase=types.SimpleNamespace(driver=lambda *a, **k: Driver()), Query=lambda s, timeout: s)
    monkeypatch.setitem(sys.modules, "neo4j", fake)
    monkeypatch.setenv("GRAPH_URI", "bolt://graph:7687")
    monkeypatch.setattr(graph, "DEADLINE", 0.2)
    started = time.monotonic()
    with pytest.raises(graph.GraphError, match="답이 없습니다"):
        graph._run("MATCH (n) RETURN n", {})
    assert time.monotonic() - started < 2
    release.set()


def test_skill_failure_runs_the_session_without_graph_tools(tmp_path, cfg, monkeypatch):
    from dataclasses import replace
    calls = {}

    class Proc:
        returncode, pid = 0, 1

        def communicate(self, timeout):
            return b"", b""

    def popen(cmd, cwd, env, **kw):
        calls.update(cmd=cmd, env=env)
        return Proc()
    monkeypatch.setattr(session.subprocess, "Popen", popen)
    monkeypatch.setattr(session, "_read_events", lambda *a, **k: None)
    monkeypatch.setattr(graph, "skill", lambda: (_ for _ in ()).throw(ImportError("edge_ontology")))
    session.run(replace(cfg, graph_uri="bolt://graph:7687"), _ws(tmp_path), code="138360", prompt="p", corpus_path=tmp_path / "c")
    assert "company_events" not in calls["cmd"][calls["cmd"].index("--tools") + 1]
    assert calls["env"]["TRUTH_GRAPH"] == "0"
