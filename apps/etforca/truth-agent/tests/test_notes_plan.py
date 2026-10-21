"""조사 노트와 계획 세션. 모델은 가짜로 바꾸고, 코드가 맡은 순서·노트 보존·후보 선정을 본다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import SEED, seed
from edge_truth_agent import news, notes, runner, tools
from edge_truth_agent.session import SessionResult
from edge_truth_agent.store import LocalStore
from edge_truth_agent.workspace import Workspace

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 21, 10, 5, tzinfo=KST)


# ── 노트 형식 (코드가 맞춘다) ──
def test_update_replaces_todo_keeps_older_records_and_trims():
    # 모델은 할 일과 이번 기록만 넘긴다. 지난 기록을 모델이 다시 옮겨 쓰다 잃지 않게 코드가 보존한다.
    text = notes.template(seed("hybe-ipo-2019"))
    for h in range(notes.KEEP_RECORDS + 2):
        text = notes.update(text, f"2026-10-21 {h:02d}:05", [f"할 일 {h}"], f"기록 {h}")
    assert notes.open_items(text) == 1 and "할 일 0" not in text
    assert text.count("\n### ") == notes.KEEP_RECORDS
    assert text.index(f"기록 {notes.KEEP_RECORDS + 1}") < text.index(f"기록 {notes.KEEP_RECORDS}")  # 최신이 위


def test_same_time_record_is_replaced_and_headings_in_record_do_not_split():
    # 한 세션이 write_notes 를 두 번 불러도 기록은 하나. 기록 안의 '###' 줄이 항목을 쪼개면 보존 개수가 틀어진다.
    text = notes.template(seed("hybe-ipo-2019"))
    text = notes.update(text, "2026-10-21 10:05", [], "첫 기록")
    text = notes.update(text, "2026-10-21 10:05", [], "### 고친 기록\n둘째 줄")
    assert "첫 기록" not in text and text.count("\n### ") == 1 and "고친 기록" in text


def test_write_notes_validates_and_set_plan_accepts_only_candidates(tmp_path):
    ws = Workspace.prepare(tmp_path / "u", mode="update", case=seed("hybe-ipo-2019"), inbox=[], index=[],
                           today="2026-10-21", now=NOW.isoformat(), notes=notes.template(seed("hybe-ipo-2019")))
    with pytest.raises(tools.ToolError, match="next"):
        tools.write_notes(ws, {"next": ["줄\n바꿈"], "record": "기록"})
    with pytest.raises(tools.ToolError, match="record"):
        tools.write_notes(ws, {"next": [], "record": " "})
    plan = Workspace.prepare(tmp_path / "p", mode="plan", case=None, inbox=[], index=[{"id": "hybe-ipo-2019"}],
                             today="2026-10-21", now=NOW.isoformat())
    with pytest.raises(tools.ToolError, match="후보"):
        tools.set_plan(plan, {"order": [{"case": "made-up", "reason": "x"}]})
    with pytest.raises(tools.ToolError, match="skip"):
        tools.set_plan(plan, {"order": [{"case": "hybe-ipo-2019", "reason": "x"}], "skip": ["hybe-ipo-2019"]})


# ── 실행 흐름 ──
def _lake():
    from test_news_render_runner import FakeLake, bk
    key = news.minute_prefix("2026-10-21") + "window=0930/attempt=1/page=1.ndjson"
    return FakeLake({key: [bk("방시혁 하이브 의장 검찰 기소", "서울남부지검이 방시혁 의장을 기소했다.",
                              "https://www.yna.co.kr/view/AKR20261021000100001")]})


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SEED_DIR", SEED)
    monkeypatch.setattr(runner, "render", lambda cases, web, focus: {k: {"errors": [], "warnings": []} for k in [*focus, "index"]})
    from test_news_render_runner import _cfg
    return _cfg(tmp_path), LocalStore(tmp_path / "store")


def _sessions(order: list[str], log: list, *, fail: bool = False):
    """계획은 order 대로, 사건 세션은 받은 노트를 기록하고 write_notes 로 마무리(또는 실패)."""
    def run(cfg, ws, *, case_id, mode, prompt, **kw):
        state = ws.state
        if mode == "plan":
            log.append(("plan", sorted(r["id"] for r in ws.index())))
            tools.set_plan(ws, {"order": [{"case": c, "reason": "시험"} for c in order if c in {r["id"] for r in ws.index()}]})
        else:
            log.append((case_id, ws.notes()))
            if fail:
                raise RuntimeError("모델 API 장애")
            tools.write_notes(ws, {"next": [f"{case_id} 다음 확인"], "record": f"{case_id} 조사함"})
        state = ws.state
        state["done"] = True
        ws.save_state(state)
        return SessionResult(case_id=case_id, done=True)
    return run


def test_plan_order_is_followed_and_each_session_gets_only_its_own_notes(flow):
    cfg, store = flow
    log: list = []
    report = runner.run(cfg, "hourly", lake=_lake(), session_runner=_sessions(["geumyang-monlaa", "hybe-ipo-2019"], log), now=NOW)
    # 한 번도 조사하지 않은 사건은 새 기사가 없어도 첫 계획 후보다(노트를 처음 만든다).
    assert log[0] == ("plan", ["geumyang-monlaa", "homeplus-short-term-bonds", "hybe-ipo-2019", "koreazinc-offering-2024"])
    assert [entry[0] for entry in log[1:]] == ["geumyang-monlaa", "hybe-ipo-2019"]  # 계획 순서대로, 하나씩
    assert "금양" in log[1][1] and "하이브" not in log[1][1]  # 그 사건 노트 하나만
    saved = store.get(notes.key("hybe-ipo-2019"))[0].decode("utf-8")
    assert "hybe-ipo-2019 조사함" in saved and notes.open_items(saved) == 1
    plan_md = store.get(f"plans/2026-10-21/{report['runId']}.md")[0].decode("utf-8")
    assert plan_md.index("geumyang-monlaa") < plan_md.index("hybe-ipo-2019") and "homeplus-short-term-bonds" in plan_md

    # 한 시간 뒤: 방금 조사한 사건은 새 기사도 없고 다시 볼 때가 안 됐다. 고르지 않은 두 사건만 후보로 남는다.
    log.clear()
    runner.run(cfg, "hourly", lake=_lake(), session_runner=_sessions([], log), now=NOW + timedelta(hours=1))
    assert log == [("plan", ["homeplus-short-term-bonds", "koreazinc-offering-2024"])]

    # 하루가 지나면 '다음에 찾아볼 것'이 남은 사건이 다시 후보가 된다.
    log.clear()
    runner.run(cfg, "hourly", lake=_lake(), session_runner=_sessions([], log), now=NOW + runner.FOLLOW_UP + timedelta(hours=1))
    assert {"geumyang-monlaa", "hybe-ipo-2019"} <= set(log[0][1])


def test_failed_session_leaves_a_code_record_and_keeps_the_articles(flow):
    # 세션이 마무리하지 못하면 노트가 갱신되지 않아 다음 세션이 같은 일을 모른 채 되풀이한다 — 코드가 실패를 적는다.
    cfg, store = flow
    report = runner.run(cfg, "hourly", lake=_lake(), session_runner=_sessions(["hybe-ipo-2019"], [], fail=True), now=NOW)
    assert report["failed"] == ["hybe-ipo-2019"]
    saved = store.get(notes.key("hybe-ipo-2019"))[0].decode("utf-8")
    assert "(코드 기록)" in saved and "모델 API 장애" in saved
    state = runner.json.loads(store.get(runner.STATE_KEY)[0])
    assert state["pending"]["hybe-ipo-2019"]["tries"] == 1


def test_skipped_case_drops_its_articles(flow):
    # 계획이 무관하다고 본 기사를 계속 쌓아 두면 매시 같은 후보로 계획 세션이 다시 돈다.
    cfg, store = flow

    def skipping(cfg, ws, *, case_id, mode, **kw):
        tools.set_plan(ws, {"order": [], "skip": [r["id"] for r in ws.index()]})
        state = ws.state
        state["done"] = True
        ws.save_state(state)
        return SessionResult(case_id=case_id, done=True)

    runner.run(cfg, "hourly", lake=_lake(), session_runner=skipping, now=NOW)
    state = runner.json.loads(store.get(runner.STATE_KEY)[0])
    assert state["pending"] == {} and set(state["seen"]) >= {"hybe-ipo-2019"}


def test_tool_specs_cli_serves_every_session_mode(capsys):
    # pi 확장은 세션마다 이 CLI 로 도구 목록을 받는다 — 세션 종류를 늘리고 여기를 빼먹으면 그 세션은 시작조차 못 한다.
    import json
    from edge_truth_agent.__main__ import main
    from edge_truth_agent.session import SKILLS
    for mode in SKILLS:
        assert main(["tool-specs", "--mode", mode]) == 0
        assert {s["name"] for s in json.loads(capsys.readouterr().out)} >= {"finish"}


def test_session_can_finish_after_adding_a_source_dated_today(tmp_path):
    # updatedAt 은 모델이 못 고친다. 저장본의 옛 updatedAt 으로 검사하면 오늘 날짜 출처를 등록한 세션은 끝날 수 없다
    # (실제 실행에서 세션 둘이 예산을 다 쓰고 실패했다).
    from conftest import seed as load
    case = load("hybe-ipo-2019")
    case["updatedAt"] = "2026-10-20T00:00:00+09:00"
    ws = Workspace.prepare(tmp_path / "u", mode="update", case=case, inbox=[], index=[], today="2026-10-21",
                           now=NOW.isoformat(), notes=notes.template(case))
    edited = ws.case()
    edited["sources"].append({**edited["sources"][0], "id": "news-today-0001", "publishedAt": "2026-10-21", "accessedAt": "2026-10-21"})
    ws.save_case(edited)
    assert not [p for p in tools.session_problems(ws) if p.startswith("updatedAt")]


def test_registered_source_can_be_looked_up_by_id(tmp_path):
    # 노트의 '다음에 찾아볼 것'은 기존 출처를 가리키는 일이 많다. 그 id 로 못 열면 세션이 원문 주소를 찾느라 예산을 쓴다.
    case = seed("geumyang-monlaa")
    ws = Workspace.prepare(tmp_path / "u", mode="update", case=case, inbox=[], index=[], today="2026-10-21",
                           now=NOW.isoformat(), notes=notes.template(case))
    shown = tools.view_news(ws, {"id": "official-fsc-87894"})
    assert "https://www.fsc.go.kr/no010101/87894" in shown and "web_fetch" in shown
