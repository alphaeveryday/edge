"""도구 규칙. 에이전트가 우회하면 공개 사건이 망가지는 규칙들이라 각각 반례를 직접 넣어 본다."""
from __future__ import annotations

import pytest

from conftest import article, seed
from edge_truth_agent import corpus, tools
from edge_truth_agent.tools import ToolError
from edge_truth_agent.workspace import Workspace

TODAY, NOW = "2026-10-20", "2026-10-20T10:00:00+09:00"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    news = [article("방시혁 검찰 기소", "서울남부지검이 방시혁 의장을 기소했다.", aid="1" * 64)]
    path = corpus.build_index({a.id: a for a in news}, tmp_path / "corpus.sqlite")
    monkeypatch.setenv("TRUTH_CORPUS", str(path))
    return Workspace.prepare(tmp_path / "s", mode="update", case=seed("hybe-ipo-2019"), inbox=[a.to_dict() for a in news],
                             index=[], today=TODAY, now=NOW)


def test_edit_requires_a_fresh_view(ws):
    # 보지 않은 사건·본 뒤 바뀐 사건을 고치면 엉뚱한 내용을 덮어쓴다.
    with pytest.raises(ToolError, match="view_case"):
        tools.set_value(ws, {"path": "legalStatus", "value": "x"})
    tools.view_case(ws, {})
    case = ws.case()
    case["legalStatus"] = "누군가 바꿈"
    ws.save_case(case)
    with pytest.raises(ToolError, match="다시 보세요"):
        tools.set_value(ws, {"path": "legalStatus", "value": "x"})


def test_str_replace_needs_exactly_one_match(ws):
    tools.view_case(ws, {})
    with pytest.raises(ToolError, match="없습니다"):
        tools.str_replace(ws, {"path": "headline[1]", "old": "존재하지 않는 문장", "new": "x"})
    with pytest.raises(ToolError, match="번 있습니다"):
        tools.str_replace(ws, {"path": "body[1].p", "old": "주관사", "new": "x"})
    tools.str_replace(ws, {"path": "legalStatus", "old": "미확인", "new": "확인 중"})
    assert ws.case()["legalStatus"].endswith("확인 중")


@pytest.mark.parametrize("path", ["status", "sources[0].url", "related[+]", "flaggedAt", "impact"])
def test_read_only_fields_cannot_be_edited(ws, path):
    # 판정·출처·날짜를 직접 고칠 수 있으면 공식 출처 규칙과 적중률 규칙이 무력해진다.
    tools.view_case(ws, {})
    with pytest.raises(ToolError, match="고칠 수 없습니다"):
        tools.set_value(ws, {"path": path, "value": "x"})


def test_source_must_be_read_before_it_is_registered(ws):
    # 읽지 않은 기사를 출처로 달 수 있으면 모델이 지어낸 근거가 공개된다.
    sid = "news-" + "1" * 16
    with pytest.raises(ToolError, match="읽지 않았습니다"):
        tools.add_source(ws, {"ref": sid})
    assert "서울남부지검" in tools.view_news(ws, {"id": sid})
    tools.add_source(ws, {"ref": sid})
    added = ws.case()["sources"][-1]
    assert added["id"] == sid and added["kind"] == "news" and added["publishedAt"] == "2026-10-20"


def test_news_report_cannot_change_the_verdict(ws):
    # 보도만으로 '밝혀짐'을 붙이면 수사기관 주장을 확정 사실로 바꾸게 된다(방법론 §8).
    sid = "news-" + "1" * 16
    tools.view_news(ws, {"id": sid})
    tools.add_source(ws, {"ref": sid})
    tools.view_case(ws, {})
    with pytest.raises(ToolError, match="판정을 바꿀 수 없습니다"):
        tools.propose_verdict(ws, {"status": "confirmed", "source_id": sid, "note": "검찰 기소 보도"})
    assert ws.case()["status"] == "pending"


def test_official_source_changes_verdict_only_when_read_and_dated_correctly(ws):
    case = ws.case()
    case["sources"].append({"id": "web-official1", "kind": "official", "publisher": "서울남부지검", "title": "기소 보도자료",
                            "url": "https://www.spo.go.kr/x", "publishedAt": "2026-10-19", "accessedAt": TODAY,
                            "snapshot": "snapshots/web-official1.json"})
    ws.save_case(case)
    _official_snapshot(ws, "web-official1", "2026-10-19T01:00:00Z")  # 원문 메타데이터의 발행일(한국 날짜 10-19)
    tools.view_case(ws, {})
    args = {"status": "confirmed", "source_id": "web-official1", "note": "검찰 기소. 판결과는 별개"}
    with pytest.raises(ToolError, match="읽지 않았습니다"):
        tools.propose_verdict(ws, args)
    state = ws.state
    state["viewed"].append("web-official1")
    ws.save_state(state)
    # 확인일은 모델이 고르지 않는다 — 근거 발행일이 그대로 들어가야 적중률 날짜를 옮길 수 없다.
    tools.propose_verdict(ws, {**args, "at": "2020-01-01"})
    assert ws.case()["status"] == "confirmed" and ws.case()["resolvedAt"] == "2026-10-19"


def test_session_rules_catch_status_changed_without_verdict_tool(ws):
    case = ws.case()
    case["status"] = "refuted"
    ws.save_case(case)
    assert any("propose_verdict" in line for line in tools.session_problems(ws))


def test_finish_without_changes_is_a_valid_outcome(ws):
    # 바꿀 근거가 없으면 고치지 않고 끝내는 것이 정상이다(사람이 검토한 문장을 흔들지 않는다).
    assert tools.finish(ws, {"summary": "새 사실 없음"}).startswith("완료: 변경 없음")
    assert ws.state["done"] is True


def test_search_news_finds_korean_words_with_particles(ws):
    # 한국어 조사가 붙어도('방시혁은') 찾아야 한다 — 낱말 경계 검색이면 놓친다.
    assert "news-" + "1" * 16 in tools.search_news(ws, {"query": "방시혁 기소"})


def test_tool_specs_and_implementations_match():
    from edge_truth_agent.toolspec import SPECS
    assert {name for name, *_ in SPECS} == set(tools.TOOLS)


def test_firm_behavior_verdict_needs_the_verdict_tool(ws):
    # 판정(status)은 막아도 유형별 판정을 '확인'으로 적으면 화면에는 확인된 거짓처럼 보인다.
    tools.view_case(ws, {})
    tools.set_value(ws, {"path": "behaviors[0].verdict", "value": "confirmed"})
    tools.view_case(ws, {})
    tools.set_value(ws, {"path": "levels.behavior", "value": "confirmed"})
    found = tools.session_problems(ws)
    assert any(line.startswith("behaviors[0].verdict") for line in found)
    assert any(line.startswith("levels.behavior") for line in found)


def test_extra_behavior_of_an_already_confirmed_type_is_still_caught(tmp_path):
    # 이미 '확인'인 유형이 있어도, 같은 유형을 하나 더 붙여 '확인'을 늘릴 수 없어야 한다.
    case = seed("geumyang-monlaa")
    ws = Workspace.prepare(tmp_path / "s", mode="update", case=case, inbox=[], index=[], today=TODAY, now=NOW)
    tools.view_case(ws, {})
    tools.set_value(ws, {"path": "behaviors[+]", "value": dict(case["behaviors"][0])})
    assert any(line.startswith(f"behaviors[{len(case['behaviors'])}].verdict") for line in tools.session_problems(ws))


def _official_snapshot(ws, sid: str, published):
    ws.save_snapshot(sid, {"id": sid, "url": "https://www.spo.go.kr/x", "title": "기소 보도자료", "publishedDate": published,
                           "kind": "official", "text": "서울남부지검은 방시혁 의장을 기소했다.", "full": True})


def test_model_supplied_date_cannot_set_the_resolution_date(ws, tmp_path):
    # 원문에 날짜가 없어 모델이 적은 날짜는 확인일이 되면 안 된다 — 적중률이 그 날짜로 정해진다.
    # 출처를 등록한 세션이 끝난 뒤 다음 세션에서 판정해도 막혀야 한다.
    _official_snapshot(ws, "web-official2", None)
    tools.view_news(ws, {"id": "web-official2"})
    tools.add_source(ws, {"ref": "web-official2", "published_at": "2026-10-19"})
    later = Workspace.prepare(tmp_path / "next", mode="update", case=ws.case(), inbox=[], index=[], today=TODAY, now=NOW)
    _official_snapshot(later, "web-official2", None)
    tools.view_news(later, {"id": "web-official2"})
    tools.view_case(later, {})
    with pytest.raises(ToolError, match="직접 넣은 날짜"):
        tools.propose_verdict(later, {"status": "confirmed", "source_id": "web-official2", "note": "검찰 기소. 판결과는 별개"})


def test_metadata_date_wins_over_model_and_is_korean_date(ws):
    # 원문 메타데이터가 있으면 모델 값은 무시한다. 메타데이터는 UTC 라 한국 날짜로 바꾼다.
    _official_snapshot(ws, "web-official3", "2026-10-18T16:30:00Z")
    tools.view_news(ws, {"id": "web-official3"})
    tools.add_source(ws, {"ref": "web-official3", "published_at": "2026-10-20"})
    assert ws.case()["sources"][-1]["publishedAt"] == "2026-10-19"


def test_removed_rebuttal_source_still_cannot_resolve(tmp_path):
    # 반론을 지운 뒤 그 출처로 판정하면 당사자 해명이 판정 근거가 된다.
    case = seed("hybe-ipo-2019")
    case["sources"].append({"id": "web-official4", "kind": "official", "publisher": "금융위원회", "title": "해명 원문",
                            "url": "https://www.fsc.go.kr/x", "publishedAt": "2026-10-19", "accessedAt": TODAY})
    case["rebuttal"].append({"label": "당사자 해명", "text": "회사는 위법이 없다고 밝혔습니다.", "sourceId": "web-official4"})
    ws = Workspace.prepare(tmp_path / "s", mode="update", case=case, inbox=[], index=[], today=TODAY, now=NOW)
    _official_snapshot(ws, "web-official4", "2026-10-19T01:00:00Z")
    tools.view_case(ws, {})
    tools.remove(ws, {"path": f"rebuttal[{len(case['rebuttal']) - 1}]"})
    state = ws.state
    state["viewed"].append("web-official4")
    ws.save_state(state)
    tools.view_case(ws, {})
    with pytest.raises(ToolError, match="반론"):
        tools.propose_verdict(ws, {"status": "refuted", "source_id": "web-official4", "note": "회사 해명으로 해소"})


def test_new_case_needs_two_outlets_by_address_not_by_name(tmp_path):
    # 매체 이름은 모델이 적는다 — 같은 언론사 기사 두 건을 다른 이름으로 달아 '2곳'을 채울 수 없어야 한다.
    ws = Workspace.prepare(tmp_path / "s", mode="discover", case=None, inbox=[], index=[], today=TODAY, now=NOW)
    case = seed("hybe-ipo-2019") | {"origin": "agent", "flaggedAt": TODAY}
    case["sources"] = [
        {"id": "news-a", "kind": "news", "publisher": "동아일보", "title": "a", "url": "https://www.donga.com/a", "publishedAt": TODAY, "accessedAt": TODAY},
        {"id": "news-b", "kind": "news", "publisher": "동아닷컴", "title": "b", "url": "https://www.donga.com/b", "publishedAt": TODAY, "accessedAt": TODAY},
    ]
    ws.save_case(case)
    assert any("2곳" in line for line in tools.session_problems(ws))


def test_new_case_needs_a_short_title(tmp_path):
    # 목록·홈은 짧은 제목으로 훑어 읽는다 — 새 사건이 제목 없이 등록되면 긴 문장이 잘려 보인다.
    ws = Workspace.prepare(tmp_path / "s", mode="discover", case=None, inbox=[], index=[], today=TODAY, now=NOW)
    case = seed("hybe-ipo-2019") | {"origin": "agent", "flaggedAt": TODAY}
    case.pop("title", None)
    ws.save_case(case)
    assert any(line.startswith("title") for line in tools.session_problems(ws))
