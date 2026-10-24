"""국내 종목 사건 그래프 도구와 사건 메뉴 (ALPHA-1453).

WHY: 그래프는 기사마다 사건을 따로 뽑아 같은 일이 여러 줄이고, 수치를 문자열로 돌려준다. 모델이 '보도 9건 = 일 9건'으로
읽거나, 단위·기준이 다른 값을 같은 줄에 세우면 선례 판단이 틀린다. 그래프가 꺼져 있으면(평일 05:30~16:00 밖) 도구는
오류를 알려야 하고 세션은 계속돼야 한다. 미국 세션에는 그래프 도구가 열리지 않는다(사건은 국내 뉴스·공시로 만든다).
"""
from datetime import date

import pytest

from edge_truth_research import graph, session, tools
from edge_truth_research.toolspec import GRAPH_TOOLS, specs

TODAY = date(2026, 10, 26)


def _title(kind, body, day="2026-10-12"):
    return f"{kind} / {body} / {day}"


def test_menu_lists_every_ontology_type_with_its_codes_and_stays_small():
    text = graph.menu()
    types, _ = graph.registry()
    assert len(text.splitlines()) == len(types)
    line = next(l for l in text.splitlines() if l.startswith("COMPANY.CONTRACT.SIGNING "))
    assert "SUPPLIER*" in line and "CONTRACT_VALUE(CURRENCY)" in line and "SIGN" in line
    assert len(text) < 12000  # 세션마다 시스템 지시에 붙는다 — 약 4천 토큰 이하


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
    events = graph.company_events("138360", TODAY, 90, run=lambda q, p: seen.append(p) or rows)
    assert seen[0] == {"code": "138360", "since": "2026-07-28"}
    assert [(e["type"], e["reports"]) for e in events] == [("COMPANY.CONTRACT.SIGNING", 2), ("COMPANY.ALLIANCE.PARTNERSHIP", 1)]
    assert events[0]["articles"] == ["a" * 64, "b" * 64]
    assert events[0]["what"] == max((r["title"].split(" / ")[1] for r in rows[:2]), key=len)  # 가장 긴(자세한) 서술을 남긴다
    assert events[1]["stage"] == "SIGNED" and events[1]["articles"] == []


def _measure(eid, value, unit="KRW", basis="TOTAL", vsrc="PARSED", parse="ok", d="2026-10-20"):
    return {"id": eid, "d": d, "title": _title("COMPANY.CONTRACT.SIGNING", f"{eid} [SUPPLIER]; 공사 [CONTRACT_OBJECT]", d),
            "value": value, "unit": unit, "basis": basis, "vsrc": vsrc, "parse": parse, "who": [f"{eid}:SUPPLIER"]}


def test_similar_events_compares_decimals_and_counts_other_units_as_unknown_not_as_misses():
    rows = [_measure("e1", "930900000000.00000000"),              # 맞음
            _measure("e2", "26800000000.00000000"),               # 기준 미달
            _measure("e3", "500000000000", basis="UNKNOWN"),      # 기준이 다름 → 모름
            _measure("e4", "82000000", unit="USD"),               # 단위가 다름 → 모름
            _measure("e5", None),                                 # 값 없음 → 모름
            _measure("e6", "200000000000", vsrc="UNRESOLVED"),    # 불확실 → 모름
            _measure("e1", "930900000000.0")]                     # 같은 사건의 다른 측정 → 한 건
    found = graph.similar_events("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "100000000000", "KRW", "TOTAL",
                                 TODAY, 180, run=lambda q, p: rows)
    assert [m["value"] for m in found["matched"]] == ["930,900,000,000"]
    assert found["unknown"] == 4 and found["scanned"] == 7 and not found["truncated"]


@pytest.mark.parametrize("args, message", [
    (("COMPANY.NOPE", "CONTRACT_VALUE", "gte", "1"), "메뉴에 없는"),
    (("COMPANY.CONTRACT.SIGNING", "DEAL_VALUE", "gte", "1"), "수치가 아닙니다"),
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gt", "1"), "gte·lte"),
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "nan"), "유한한"),
    (("COMPANY.CONTRACT.SIGNING", "CONTRACT_VALUE", "gte", "1천억"), "숫자가 아닙니다"),
])
def test_similar_events_rejects_codes_outside_the_menu_before_querying(args, message):
    def never(q, p):
        raise AssertionError("잘못된 입력으로 그래프를 조회하지 않는다")
    with pytest.raises(ValueError, match=message):
        graph.similar_events(*args, "KRW", "TOTAL", TODAY, 180, run=never)


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
    with pytest.raises(tools.ToolError, match="메뉴에 없는"):
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
    assert "## 사건 메뉴" in system and "COMPANY.CONTRACT.SIGNING" in system
    assert session._env(with_graph, us, tmp_path / "c.sqlite")["TRUTH_GRAPH"] == "0"
