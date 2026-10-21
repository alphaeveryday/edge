"""오늘의 급등주: 코드가 맡은 목록·시세·게시·판정 규칙. KIS·DART·모델은 가짜로 바꾼다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from conftest import article
from edge_truth_agent import corpus, kis as kis_mod, logos, movers, tools
from edge_truth_agent.kis import Mover, five_minute, is_common_stock
from edge_truth_agent.session import SessionResult
from edge_truth_agent.store import LocalStore
from edge_truth_agent.workspace import Workspace

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 22, 11, 0, tzinfo=KST)


class FakeKis:
    def __init__(self, movers_by_market, *, traded=True):
        self.by_market, self.traded = movers_by_market, traded

    def risers(self, market, limit=30):
        return self.by_market.get(market, [])

    def series(self, code, until, day):
        return [["09:00", 1000.0], ["09:05", 1100.0], ["10:55", 1300.0]] if self.traded else []


class FakeDart:
    def corp_codes(self):
        return {"900010": "00000001", "900020": "00000002"}

    def filings(self, corp, since, until):
        return [{"rcept_no": "20261022000001", "report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "2026-10-22",
                 "flr_nm": "가나다전자", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261022000001"}] if corp == "00000001" else []

    def homepage(self, corp):
        return None


def _m(code, name, pct, market="KOSDAQ"):
    return Mover(code=code, name=name, market=market, price=1300.0, prev_close=1300 / (1 + pct / 100), change_pct=pct, volume=1_000_000)


@pytest.fixture
def cfg(tmp_path):
    from test_news_render_runner import _cfg
    return _cfg(tmp_path)


def _judging(tags):
    """판정 세션 대신: 받은 보도 하나를 읽고 정해진 태그로 판정한다."""
    calls = []

    def run(cfg, ws, *, case_id, mode, prompt, **kw):
        calls.append(case_id)
        tag = tags[case_id]
        ids = []
        if tag != "pending" and ws.inbox():
            ids = [tools.view_news(ws, {"id": ws.inbox()[0]["id"]}).split("\n", 1)[0]]
        tools.set_mover_verdict(ws, {"tag": tag, "confidence": 0.8, "event_title": "공급계약 공시", "event_kind": "contract",
                                     "claim": "회사는 대형 계약을 맺었다고 밝혔다", "finding": "계약 상대가 공시와 다르다",
                                     "reasons": ["공시와 보도자료의 계약 상대가 다르다"], "source_ids": ids})
        state = ws.state
        state["done"] = True
        ws.save_state(state)
        return SessionResult(case_id=case_id, done=True)
    run.calls = calls
    return run


def _seed_news(cfg):
    a = article("가나다전자 대형 공급계약 체결", "가나다전자가 공급계약을 맺었다", aid="b" * 64, published="2026-10-22 09:10:00")
    corpus.save(LocalStore(cfg.store), {a.id: a})


def test_publishes_contract_valid_ranking_with_false_first(cfg):
    # 화면 계약(스키마)을 지키고, '우리 판단이 맞을 때 되돌려질 폭'(상승률 × 확신도)이 큰 허위매물이 위로 와야 한다.
    _seed_news(cfg)
    kis = FakeKis({"KOSDAQ": [_m("900010", "가나다전자", 29.9), _m("900020", "라마바바이오", 15.0), _m("900030", "소폭상승", 4.0)]})
    runner = _judging({"900010": "false", "900020": "pending"})
    report = movers.run_movers(cfg, kis=kis, dart=FakeDart(), session_runner=runner, now=NOW)
    doc = json.loads((LocalStore(cfg.publish).get("movers/latest.json")[0]))
    assert [i["code"] for i in doc["items"]] == ["900010", "900020"]  # 급등 기준(10%) 미달은 뺀다
    top = doc["items"][0]
    assert top["tag"] == "false" and top["impact"] == round(29.9 * 0.8, 2) and doc["items"][1]["impact"] == 0
    assert top["series"][-1] == ["10:55", 1300.0] and report["published"] and sorted(runner.calls) == ["900010", "900020"]


def test_same_day_rerun_keeps_verdicts_and_skips_judged(cfg):
    # 매시 같은 종목을 다시 판정하면 비용만 든다. 이미 판정한 종목은 시세만 새로 싣는다.
    _seed_news(cfg)
    kis = FakeKis({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    movers.run_movers(cfg, kis=kis, dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW)
    again = _judging({"900010": "promising"})
    movers.run_movers(cfg, kis=kis, dart=FakeDart(), session_runner=again, now=NOW + timedelta(hours=1))
    doc = json.loads(LocalStore(cfg.publish).get("movers/latest.json")[0])
    assert again.calls == [] and doc["items"][0]["tag"] == "false"


def test_market_closed_day_is_not_published(cfg):
    # 휴장일에 KIS 순위는 전 거래일 값을 준다 — 그날 분봉이 없으면 오늘 결과로 게시하지 않는다.
    kis = FakeKis({"KOSPI": [_m("900010", "가나다전자", 29.9, "KOSPI")]}, traded=False)
    report = movers.run_movers(cfg, kis=kis, dart=FakeDart(), session_runner=_judging({}), now=NOW)
    assert not report["published"] and LocalStore(cfg.publish).get("movers/latest.json")[0] is None


def test_common_stock_filter_and_five_minute_buckets():
    assert is_common_stock("005930", "삼성전자") and not is_common_stock("005935", "삼성전자우")
    assert not is_common_stock("069500", "KODEX 200") and not is_common_stock("900120", "가나다스팩1호")
    assert five_minute({"0900": 10, "0904": 11, "0905": 12, "0909": 13}) == [["09:00", 11], ["09:05", 13]]


def test_risers_parse_vendor_rows(monkeypatch):
    rows = [{"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자", "stck_prpr": "70,000", "prdy_vrss": "7000", "prdy_ctrt": "11.11", "acml_vol": "100"},
            {"stck_shrn_iscd": "005935", "hts_kor_isnm": "삼성전자우", "stck_prpr": "60000", "prdy_vrss": "6000", "prdy_ctrt": "11.1", "acml_vol": "5"}]
    client = kis_mod.Kis("k", "s")
    monkeypatch.setattr(client, "_get", lambda path, tr, params: {"output": rows})
    got = client.risers("KOSPI")
    assert [(m.code, m.prev_close, m.change_pct) for m in got] == [("005930", 63000.0, 11.11)]


def _ws(tmp_path, inbox=()):
    return Workspace.prepare(tmp_path / "s", mode="mover", case=None, inbox=list(inbox), index=[], today="2026-10-22",
                             now=NOW.isoformat(), mover={"code": "900010", "name": "가나다전자", "filings": []})


def test_mover_verdict_rules(tmp_path, monkeypatch):
    # 유망주는 원문(공시·공식 원문)으로만, 출처는 이 세션에서 읽은 것만 — 보도만 보고 '확인'을 붙이면 기관 발표를 그대로 옮기는 셈이다.
    monkeypatch.setenv("TRUTH_CORPUS", str(corpus.build_index({}, tmp_path / "c.sqlite")))
    a = article("가나다전자 계약", "요약", aid="c" * 64).to_dict()
    ws = _ws(tmp_path, [a])
    base = {"confidence": 0.7, "event_title": "공급계약 공시", "event_kind": "contract", "claim": "주장", "finding": "확인한 것",
            "reasons": ["이유"]}
    with pytest.raises(tools.ToolError, match="읽지 않은"):
        tools.set_mover_verdict(ws, base | {"tag": "false", "source_ids": ["news-" + "c" * 16]})
    tools.view_news(ws, {"id": a["id"]})
    with pytest.raises(tools.ToolError, match="원문"):
        tools.set_mover_verdict(ws, base | {"tag": "promising", "source_ids": ["news-" + "c" * 16]})
    with pytest.raises(tools.ToolError, match="set_mover_verdict"):
        tools.finish(ws, {"summary": "끝"})
    assert "저장" in tools.set_mover_verdict(ws, base | {"tag": "false", "source_ids": ["news-" + "c" * 16]})
    assert ws.state["moverVerdict"]["sources"][0]["kind"] == "news"


def test_logo_accepts_only_safe_image_formats():
    # 로고는 우리 도메인에서 서빙된다 — SVG(스크립트 가능)·HTML 은 받지 않는다.
    assert logos.image_kind(b"\x89PNG\r\n\x1a\n....") == "png" and logos.image_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert logos.image_kind(b"<svg onload=alert(1)>") is None and logos.image_kind(b"<!doctype html>") is None
    page = '<link rel="icon" href="/a.svg"><link rel="icon" href="/fav.png"><link rel="apple-touch-icon" href="/touch.png">'
    assert logos.icon_candidates(page, "https://x.co.kr/") == ["https://x.co.kr/touch.png", "https://x.co.kr/fav.png", "https://x.co.kr/favicon.ico"]
