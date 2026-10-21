"""ACH 판정 규칙과 조사 도구: 태그는 모델이 아니라 표(가장 덜 어긋나는 가설)로 정해지고, 근거는 이 세션에서 읽은 것만 쓴다."""
from __future__ import annotations

import pytest

from conftest import article
from edge_truth_agent import corpus
from edge_truth_agent.tools import ToolError
from edge_truth_agent.workspace import Workspace
from edge_truth_research import ach, tools
from edge_truth_research.kind import KindError, parse_list

H = [{"id": "H1", "text": "공급계약이 실제로 맺어졌다", "kind": "event"},
     {"id": "H2", "text": "계약 규모가 부풀려졌다", "kind": "overstated"},
     {"id": "H3", "text": "데이터센터 테마로 올랐다", "kind": "theme"},
     {"id": "H4", "text": "업종 전체가 함께 올랐다", "kind": "sector"}]


def _ev(source, weight, **marks):
    return {"source": source, "note": "증거", "weight": weight, "marks": marks}


def _decide(evidence, primary=(), publishers=None):
    return ach.decide(H, evidence, primary=set(primary), publishers=publishers or {})


def test_event_needs_a_read_filing_or_two_outlets_to_be_promising():
    # 보도 한 곳만 보고 '확인'을 붙이면 회사 보도자료를 그대로 옮기는 셈이다 — 공시 본문이나 서로 다른 매체 둘이 있어야 유망주다.
    ev = [_ev("news-a", "mid", H1="C", H2="I", H3="I", H4="I"), _ev("kind-list", "high", H1="N", H2="N", H3="I", H4="N"),
          _ev("news-b", "low", H1="C", H2="N", H3="N", H4="I")]
    one_outlet = _decide(ev, publishers={"news-a": "a.com", "news-b": "a.com"})
    assert one_outlet["winner"] == "H1" and one_outlet["tag"] == "pending"
    assert _decide(ev, publishers={"news-a": "a.com", "news-b": "b.com"})["tag"] == "promising"
    filing = ev + [_ev("kind-20261022000001", "high", H1="C", H2="I", H3="I", H4="N")]
    assert _decide(filing, primary={"kind-20261022000001"})["tag"] == "promising"


def test_overstated_claim_is_false_and_ties_stay_pending():
    # 공시보다 부풀린 주장이 남으면 허위매물. 가장 덜 어긋나는 가설이 둘이면 어느 쪽도 정하지 않는다.
    over = [_ev("kind-1", "high", H1="I", H2="C", H3="I", H4="N"), _ev("news-a", "mid", H1="C", H2="C", H3="I", H4="I"),
            _ev("kind-list", "high", H1="N", H2="N", H3="I", H4="I")]
    got = _decide(over, primary={"kind-1"})
    assert got["winner"] == "H2" and got["tag"] == "false" and got["confidence"] >= 0.6
    tie = [_ev("news-a", "mid", H1="I", H2="I", H3="C", H4="C"), _ev("news-b", "mid", H1="I", H2="I", H3="C", H4="C"),
           _ev("kind-list", "high", H1="I", H2="I", H3="N", H4="N")]
    assert _decide(tie)["tag"] == "pending"


def test_theme_is_false_only_when_clearly_separated_and_sector_never_is():
    # 회사 근거 없는 테마 상승이 다른 설명과 뚜렷이 갈릴 때만 허위매물이다. 업종 동반 상승은 주장의 참·거짓이 아니라 보류다.
    clear = [_ev("kind-list", "high", H1="I", H2="I", H3="C", H4="N"), _ev("news-a", "mid", H1="I", H2="N", H3="C", H4="I"),
             _ev("news-b", "low", H1="N", H2="N", H3="C", H4="N")]
    assert _decide(clear)["tag"] == "false"
    close = [_ev("kind-list", "high", H1="I", H2="I", H3="C", H4="C"), _ev("news-a", "low", H1="N", H2="N", H3="C", H4="I"),
             _ev("news-b", "low", H1="N", H2="N", H3="N", H4="N")]
    assert _decide(close)["winner"] == "H3" and _decide(close)["tag"] == "pending"
    sector = [_ev("kind-list", "high", H1="I", H2="I", H3="N", H4="C"), _ev("news-a", "mid", H1="I", H2="N", H3="I", H4="C"),
              _ev("news-b", "mid", H1="N", H2="N", H3="I", H4="C")]
    assert _decide(sector)["winner"] == "H4" and _decide(sector)["tag"] == "pending"


def test_table_must_have_competing_hypotheses_and_enough_evidence():
    with pytest.raises(ach.AchError, match="3개"):
        ach.decide(H[:2], [], primary=set(), publishers={})
    with pytest.raises(ach.AchError, match="event"):
        ach.decide([h | {"kind": "theme"} for h in H], [_ev("x", "low")] * 3, primary=set(), publishers={})
    with pytest.raises(ach.AchError, match="증거"):
        ach.decide(H, [_ev("x", "low")], primary=set(), publishers={})


# ── 도구 ──
FILING = {"acptno": "20261022000001", "at": "2026-10-22 09:02", "title": "단일판매ㆍ공급계약체결", "submitter": "가나다전자",
          "url": "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20261022000001"}


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUTH_CORPUS", str(corpus.build_index({}, tmp_path / "c.sqlite")))
    a = article("가나다전자 대형 공급계약", "가나다전자가 계약을 맺었다", aid="c" * 64).to_dict()
    return Workspace.prepare(tmp_path / "s", mode="research", case=None, inbox=[a], index=[], today="2026-10-22",
                             now="2026-10-22T11:35:00+09:00", mover={"code": "900010", "name": "가나다전자", "filings": [FILING]})


def _table(ws):
    tools.set_hypotheses(ws, {"hypotheses": [{"text": h["text"], "kind": h["kind"]} for h in H]})


TEXT = {"event_title": "공급계약 공시", "event_kind": "contract", "claim": "대형 계약을 맺었다", "finding": "공시 금액과 보도가 다르다",
        "reasons": ["공시 금액은 매출의 3%다"]}


def test_evidence_only_from_what_this_session_read(ws, monkeypatch):
    # 읽지 않은 기사를 근거로 쓰면 지어낸 근거와 같다. 보도는 high(원문급) 무게를 받지 못한다.
    _table(ws)
    with pytest.raises(ToolError, match="읽지 않은"):
        tools.add_evidence(ws, {"source_id": "news-" + "c" * 16, "note": "계약 보도", "weight": "mid", "marks": {"H1": "C"}})
    tools.view_news(ws, {"id": "c" * 64})
    with pytest.raises(ToolError, match="high"):
        tools.add_evidence(ws, {"source_id": "news-" + "c" * 16, "note": "계약 보도", "weight": "high", "marks": {"H1": "C"}})
    assert "1건째" in tools.add_evidence(ws, {"source_id": "news-" + "c" * 16, "note": "계약 보도", "weight": "mid", "marks": {"H1": "C"}})
    # 공시 목록 자체는 읽기 없이 쓸 수 있다('없음'의 근거)
    assert "2건째" in tools.add_evidence(ws, {"source_id": "kind-list", "note": "다른 공시 없음", "weight": "high", "marks": {"H3": "I"}})


def test_tag_comes_from_the_table_not_the_model(ws, monkeypatch):
    # 모델은 태그를 고르지 못한다 — 표가 가리키는 대로 정해지고, 읽은 공시 본문이 과장을 가리키면 허위매물이 된다.
    monkeypatch.setattr(tools.Kind, "document", lambda self, acpt: "계약금액 1,000,000,000원 최근매출액 대비 3%")
    _table(ws)
    tools.read_disclosure(ws, {"acptno": "20261022000001"})
    tools.view_news(ws, {"id": "c" * 64})
    tools.add_evidence(ws, {"source_id": "kind-20261022000001", "note": "공시 금액은 매출의 3%", "weight": "high",
                            "marks": {"H1": "I", "H2": "C", "H3": "I", "H4": "N"}})
    tools.add_evidence(ws, {"source_id": "news-" + "c" * 16, "note": "보도는 '초대형 계약'", "weight": "mid",
                            "marks": {"H1": "C", "H2": "C", "H3": "I", "H4": "I"}})
    tools.add_evidence(ws, {"source_id": "kind-list", "note": "다른 공시 없음", "weight": "high", "marks": {"H3": "I"}})
    with pytest.raises(ToolError, match="가설 번호"):
        tools.set_verdict(ws, TEXT | {"finding": "H2 가 남았다"})
    out = tools.set_verdict(ws, TEXT | {"tag": "promising"})  # 모델이 태그를 넣어도 무시된다
    verdict = ws.state["moverVerdict"]
    assert "false" in out and verdict["tag"] == "false" and verdict["sources"][0]["kind"] == "disclosure"
    assert "finish" in out and tools.finish(ws, {"summary": "끝"}) == "완료"


def test_finish_requires_a_verdict(ws):
    with pytest.raises(ToolError, match="set_verdict"):
        tools.finish(ws, {"summary": "끝"})


def test_disclosure_must_be_in_the_listed_filings(ws):
    # 목록 밖 접수번호를 열게 두면 다른 회사 공시를 이 종목 근거로 쓸 수 있다.
    with pytest.raises(ToolError, match="목록"):
        tools.read_disclosure(ws, {"acptno": "20261022999999"})


ROW = """<tr class="first"><td class="first txc">2</td><td class="txc">2026-10-21 12:52</td>
<td><a href="#companysum" title='혜인'> 혜인</a></td>
<td><a href="#viewer" onclick="openDisclsViewer('20261021000197','')" title='단일판매ㆍ공급계약체결'>단일판매ㆍ공급계약체결</a></td>
<td>혜인</td><td class="txc"></td></tr>"""


def test_kind_list_parses_rows_and_fails_loudly_on_format_change():
    # 형식이 바뀌어 행을 못 읽는데 '공시 없음'으로 넘기면 실제 공시가 있는 급등을 테마로 몰 수 있다.
    got = parse_list(f"<table>{ROW}</table>")
    assert got == [{"acptno": "20261021000197", "at": "2026-10-21 12:52", "title": "단일판매ㆍ공급계약체결", "submitter": "혜인",
                    "url": "https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=20261021000197"}]
    assert parse_list("<table><tr><td>조회된 결과값이 없습니다.</td></tr></table>") == []
    with pytest.raises(KindError):
        parse_list("<div onclick=\"openDisclsViewer('20261021000197','')\">바뀐 형식</div>")
