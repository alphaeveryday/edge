"""미국 급등주: 국내와 같은 흐름을 Naver 해외 순위·Yahoo·SEC 로, 뉴욕 시각으로 돈다. 네트워크는 쓰지 않는다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from conftest import article
from edge_truth_agent import corpus
from edge_truth_agent.session import SessionResult
from edge_truth_agent.store import LocalStore
from edge_truth_agent.tools import ToolError
from edge_truth_agent.workspace import Workspace
from edge_truth_research import quotes as quotes_mod, research, sec as sec_mod, tools
from edge_truth_research.markets import KR, US
from edge_truth_research.quotes import Chart, Mover

ET = ZoneInfo("America/New_York")
KST = timezone(timedelta(hours=9))
# 뉴욕 10월 22일 11:35 = 한국 10월 23일 00:35. 날짜는 뉴욕 기준이어야 한다(한국 날짜로 쓰면 하루 밀린다)
NOW = datetime(2026, 10, 22, 11, 35, tzinfo=ET)


def _m(code, name, pct, *, price=24.83, value=50.9, market="NASDAQ"):
    return Mover(code=code, name=name, market=market, price=price, prev_close=price / (1 + pct / 100), change_pct=pct, trade_value=value)


class FakeQuotes:
    def __init__(self, by_market, *, traded=True):
        self.by_market, self.traded, self.days = by_market, traded, []

    def session_day(self, market="KOSPI"):
        self.days.append(market)
        return "20261022" if self.traded else "20261021"

    def risers(self, market, min_change=0.0):
        return self.by_market.get(market, [])

    def chart(self, code, market, day):
        return Chart([["09:35", 9.0], ["11:30", 24.0]], 24.83, "2026-10-22T11:34:00-04:00")


FILING = {"acptno": "0001588084-26-000099", "at": "2026-10-22 08:01", "title": "6-K · REPORT OF FOREIGN PRIVATE ISSUER",
          "submitter": "TANTECH HOLDINGS LTD", "url": "https://www.sec.gov/Archives/edgar/data/1588084/000158808426000099/a6k.htm"}


class FakeSec:
    def __init__(self, fail=()):
        self.fail, self.calls, self.homepages = set(fail), [], []

    def disclosures(self, code, since, until):
        self.calls.append((code, since, until))
        if code in self.fail:
            raise sec_mod.SecError("SEC 티커 목록에 없다")
        return [FILING] if code == "TANH" else []

    def homepage(self, code):
        self.homepages.append(code)  # 미국은 찾지 않아야 한다(SEC 에 홈페이지가 없고, 로고는 순위가 준다)
        return None


def _judging(tags):
    calls = []

    def run(cfg, ws, *, code, prompt, corpus_path):
        calls.append((code, prompt, ws.mover()))
        state = ws.state
        state["moverVerdict"], state["done"] = {
            "tag": tags[code], "confidence": 0.7, "event": {"title": "중국 기업과 공급 계약 기대", "kind": "news", "at": None},
            "claim": "대형 계약을 맺었다", "finding": "공시에 계약이 없다", "reasons": ["6-K 에 계약 내용이 없다"],
            "sources": [{"title": "6-K", "publisher": "TANTECH HOLDINGS LTD", "url": FILING["url"], "kind": "disclosure",
                         "publishedAt": "2026-10-22"}]}, True
        state["ach"] = {"hypotheses": [], "evidence": []}
        ws.save_state(state)
        return SessionResult(case_id=code, done=True)
    run.calls = calls
    return run


def _run(cfg, src, runner, *, kind=None, now=NOW, **kw):
    return research.run_research(cfg, quotes=src, kind=kind or FakeSec(), session_runner=runner, now=now, market=US, **kw)


def test_us_run_publishes_under_its_own_path_on_new_york_date(cfg):
    # 미국 장은 한국 밤에 열린다. 한국 날짜로 쓰면 '오늘' 파일이 하루 밀리고, 국내 자리에 쓰면 국내 급등주를 덮는다.
    # 1달러 미만 동전주와 거래대금 200만 달러 미만은 화면 기준에서 뺀다(사용자 결정).
    src = FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8), _m("SHFS", "SHF 홀딩스", 122.4, price=0.34, value=306.6),
                                 _m("TINY", "작은회사", 40.0, value=1.5)],
                      "NYSE": [_m("BHVN", "바이오헤이븐", 25.0, price=14.69, value=477.0, market="NYSE")]})
    runner, sec = _judging({"TANH": "false", "BHVN": "promising"}), FakeSec()
    report = _run(cfg, src, runner, kind=sec, now=NOW.astimezone(KST))  # 스케줄이 한국 시각으로 넘겨도 뉴욕 날짜로 바꾼다
    assert report["published"] and report["mode"] == "research-us" and src.days == ["NASDAQ"]
    assert sec.calls[0] == ("TANH", "2026-10-08", "2026-10-22") and sec.homepages == []
    pub = LocalStore(cfg.publish)
    doc = json.loads(pub.get("movers/us/latest.json")[0])
    assert doc["date"] == "2026-10-22" and pub.get("movers/us/2026-10-22.json")[0]
    assert pub.get("movers/latest.json")[0] is None and pub.get("movers/2026-10-23.json")[0] is None  # 국내 자리는 그대로
    assert json.loads(pub.get("movers/us/dates.json")[0]) == ["2026-10-22"]
    assert [i["code"] for i in doc["items"]] == ["TANH", "BHVN"]  # 허위매물이 위, 동전주·소액 거래 종목은 없다
    assert {i["market"] for i in doc["items"]} == {"NASDAQ", "NYSE"}
    # 판정 기록·잠금은 미국 자리에 — 국내 정각 조사와 상태가 섞이지 않는다
    store = LocalStore(cfg.store)
    assert store.get("state/us/movers-seen/2026-10-22.json")[0] and store.get(KR.seen_key("2026-10-22"))[0] is None
    assert doc["items"][0]["marks"][0]["at"] == "11:35"  # 판정 점은 뉴욕 시각


def test_us_session_sees_sec_filings_and_dollar_prices(cfg):
    # 모델이 미국 종목인 줄 모르면 KIND·원 단위로 읽고 kind- id 를 지어낸다. 첫 메시지와 작업 폴더가 시장을 알려야 한다.
    runner = _judging({"TANH": "false"})
    _run(cfg, FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]}), runner)
    code, prompt, mover = runner.calls[0]
    assert "SEC EDGAR" in prompt and "sec-list" in prompt and "$24.83" in prompt and "백만 달러" in prompt and "KIND" not in prompt
    assert mover["market"] == "NASDAQ" and mover["filings"] == [FILING]


def test_us_filing_outage_defers_and_fails_loudly(cfg):
    # SEC 를 못 읽은 종목을 '최근 공시 없음'으로 판정하면 테마로 몬다 — 보류로 싣고, 전부 실패면 실행을 실패로 남긴다.
    runner = _judging({})
    report = _run(cfg, FakeQuotes({"NASDAQ": [_m("ZZZZ", "모르는회사", 30.0)]}), runner, kind=FakeSec(fail={"ZZZZ"}))
    assert runner.calls == [] and report["failed"] == ["sec"] and report["deferred"] == ["ZZZZ"]
    item = json.loads(LocalStore(cfg.publish).get("movers/us/latest.json")[0])["items"][0]
    assert item["tag"] == "pending" and "공시를 조회하지 못해" in item["finding"]


def test_us_watch_uses_new_york_clock_and_launches_a_us_run(cfg):
    # 감시가 한국 시각으로 장 마감·정각 조사를 보면 미국 장 내내 '정규장이 끝났습니다'로 아무것도 띄우지 못한다.
    from edge_truth_research.prices import watch_new
    src, launched = FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]}), []
    at = NOW.replace(hour=10, minute=5).astimezone(KST)  # 한국 23:05
    report = watch_new(cfg.store, quotes=src, now=at, launch=lambda: launched.append(1) or "task-1", market=US)
    assert report["launched"] == "task-1" and LocalStore(cfg.store).get(US.launch_key)[0]
    closed = watch_new(cfg.store, quotes=src, now=NOW.replace(hour=16, minute=1), launch=lambda: launched.append(1), market=US)
    assert closed["skipped"] == "정규장이 끝났습니다." and launched == [1]


def test_us_watch_launch_command_names_the_market(monkeypatch):
    # 감시가 띄운 태스크가 --market 없이 돌면 국내 조사가 미국 시간에 돌아 국내 화면을 '휴장'으로 건너뛴다.
    import sys
    import types
    from edge_truth_research import prices
    sent = {}

    class Ecs:
        def run_task(self, **kw):
            sent.update(kw)
            return {"tasks": [{"taskArn": "arn:task"}]}
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda name: Ecs()))
    for k in ("TRUTH_RUN_CLUSTER", "TRUTH_RUN_TASK", "TRUTH_RUN_SG", "TRUTH_RUN_CONTAINER"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("TRUTH_RUN_SUBNETS", "a,b")
    assert prices.launch_research(US) == "arn:task"
    assert sent["overrides"]["containerOverrides"][0]["command"] == ["run", "--new", "--market", "us"]
    prices.launch_research(KR)
    assert sent["overrides"]["containerOverrides"][0]["command"] == ["run", "--new"]


def test_us_prices_refresh_their_own_file(cfg):
    # 매분 가격 갱신이 국내 파일만 보면 미국 화면 가격이 정각 조사 때 값에 멈춘다.
    from edge_truth_research.prices import refresh_prices
    _run(cfg, FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]}), _judging({"TANH": "false"}))

    class Later(FakeQuotes):
        def chart(self, code, market, day):
            assert market == "NASDAQ" and day == "20261022"
            return Chart([["09:35", 9.0], ["11:50", 20.0]], 20.0, "2026-10-22T11:50:00-04:00")
    report = refresh_prices(cfg.store, cfg.publish, quotes=Later({}), now=NOW + timedelta(minutes=16), market=US)
    item = json.loads(LocalStore(cfg.publish).get("movers/us/latest.json")[0])["items"][0]
    assert report["published"] and item["price"] == 20.0 and item["series"][-1] == ["11:50", 20.0]


# ── 시세(Naver 해외 · Yahoo) ──
def test_us_risers_parse_world_ranking_and_drop_non_common(monkeypatch):
    # 해외 순위에는 신주인수권·워런트·스팩·ETF 가 보통주처럼 섞인다. 거래대금은 달러 원시값이라 백만 달러로 바꾼다.
    def row(sym, eng, pct, end="stock", logo="https://ssl.pstatic.net/imgstock/fn/real/logo/png/stock/StockX.O.png"):
        return {"symbolCode": sym, "stockName": f"{sym} 한글", "stockNameEng": eng, "closePriceRaw": "24.83",
                "compareToPreviousClosePriceRaw": "16.02", "fluctuationsRatioRaw": str(pct), "fluctuationsRatio": str(pct),
                "accumulatedTradingValueRaw": "50942000", "stockEndType": end, "itemLogoPngUrl": logo}
    rows = [row("TANH", "Tantech Holdings Ltd", 181.8), row("HAVAR", "Harvard Ave Acquisition Rights Expiry", 21.0),
            row("ABCDW", "Abcd Corp Warrant", 20.0), row("SOXL", "Direxion Semi Bull 3X", 15.0, "etf"),
            row("EVIL", "Evil Inc", 12.0, logo="http://evil.example/x.png"), row("LOW", "Low Inc", 9.0)]
    client = quotes_mod.Quotes()
    urls = []
    monkeypatch.setattr(client, "_get", lambda url: urls.append(url) or {"stocks": rows})
    got = client.risers("NASDAQ", 10.0)
    assert [m.code for m in got] == ["TANH", "EVIL"] and urls[0].startswith("https://api.stock.naver.com/stock/exchange/NASDAQ/up?")
    tanh = got[0]
    assert tanh.trade_value == pytest.approx(50.942) and tanh.prev_close == pytest.approx(8.81) and tanh.logo_url.startswith("https://ssl.pstatic.net/")
    assert got[1].logo_url is None  # 로고는 우리 도메인에서 서빙된다 — Naver 이미지 서버 주소만 받는다


def test_us_chart_uses_new_york_session_and_bare_ticker(monkeypatch):
    # 미국 정규장은 뉴욕 09:30~16:00 이다. 한국 장 시간(09:00~15:30)으로 거르면 그래프 앞뒤가 잘린다.
    open_ = int(datetime(2026, 10, 22, 9, 30, tzinfo=ET).timestamp())
    ts = [open_ - 600, open_, open_ + 6 * 3600 + 25 * 60, open_ + 6 * 3600 + 30 * 60]  # 장 전 · 09:30 · 15:55 · 16:00(시간외)
    chart = {"chart": {"result": [{"meta": {"regularMarketPrice": 25.0, "regularMarketTime": open_ + 6 * 3600 + 30 * 60},
                                   "timestamp": ts, "indicators": {"quote": [{"close": [8.0, 9.0, 24.0, 24.5]}]}}]}}
    client = quotes_mod.Quotes()
    urls = []
    monkeypatch.setattr(client, "_get", lambda url: urls.append(url) or chart)
    got = client.chart("BRK.B", "NYSE", "20261022")
    assert got.series == [["09:35", 9.0], ["16:00", 24.0]] and got.price == 25.0 and got.at == "2026-10-22T16:00:00-04:00"
    assert "/chart/BRK-B?" in urls[0]


# ── SEC 공시 ──
def _submissions(**over):
    recent = {"accessionNumber": ["0001-26-3", "0001-26-2", "0001-26-1"], "filingDate": ["2026-10-22", "2026-10-15", "2026-09-01"],
              "acceptanceDateTime": ["2026-10-22T12:01:00.000Z", "2026-10-16T01:18:55.000Z", "2026-09-01T20:00:00.000Z"],
              "form": ["8-K", "S-1", "8-K"], "primaryDocument": ["a8k.htm", "s1.htm", "old.htm"],
              "primaryDocDescription": ["8-K", "REGISTRATION STATEMENT", "8-K"], "items": ["1.01,9.01", "", "8.01"]} | over
    return {"name": "TANTECH HOLDINGS LTD", "filings": {"recent": recent}}


def _sec(monkeypatch, responses):
    client = sec_mod.Sec(contact="ops@example.com")
    urls = []

    def fake(url):
        urls.append(url)
        got = responses(url)
        return got if isinstance(got, bytes) else json.dumps(got).encode()
    monkeypatch.setattr(client, "_open", fake)
    return client, urls


def test_sec_list_keeps_window_in_new_york_time_with_item_meanings(monkeypatch):
    # 모델은 목록만 보고 읽을 공시를 고른다 — 8-K 항목 번호만 주면 무슨 일인지 모른다. 접수 시각은 화면·판정과 같은 뉴욕 시각.
    def respond(url):
        return {"0": {"ticker": "TANH", "cik_str": 1588084}} if url.endswith("company_tickers.json") else _submissions()
    client, urls = _sec(monkeypatch, respond)
    got = client.disclosures("TANH", "2026-10-08", "2026-10-22")
    assert [f["acptno"] for f in got] == ["0001-26-3", "0001-26-2"]  # 14일 밖(9/1)은 뺀다
    assert got[0]["at"] == "2026-10-22 08:01" and got[0]["title"] == "8-K · 1.01 중요 계약 체결 · 9.01"
    assert got[1]["title"] == "S-1 · REGISTRATION STATEMENT"
    assert got[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1588084/0001263/a8k.htm"
    assert urls[1] == "https://data.sec.gov/submissions/CIK0001588084.json"


def test_sec_unknown_ticker_or_changed_format_is_an_error_not_no_filings(monkeypatch):
    # '공시 없음'은 허위매물 판정의 근거(sec-list)가 된다. 못 읽은 것을 없다고 넘기면 안 된다.
    tickers = {"0": {"ticker": "TANH", "cik_str": 1588084}}
    client, _ = _sec(monkeypatch, lambda url: tickers if url.endswith("company_tickers.json") else {"filings": {}})
    with pytest.raises(sec_mod.SecError):
        client.disclosures("NOPE", "2026-10-08", "2026-10-22")
    with pytest.raises(sec_mod.SecError):
        client.disclosures("TANH", "2026-10-08", "2026-10-22")
    with pytest.raises(sec_mod.SecError, match="TRUTH_SEC_CONTACT"):
        sec_mod.Sec(contact="")._open("https://data.sec.gov/x")  # SEC 는 연락처 없는 요청을 막는다 — 조용히 403 대신 이유를 알린다


def test_sec_document_appends_the_press_release_exhibit(monkeypatch):
    # 8-K 본문은 '첨부 99.1 참조'뿐인 일이 많다 — 첨부 보도자료를 함께 읽어야 계약 상대·금액이 보인다.
    base = "https://www.sec.gov/Archives/edgar/data/1/0001263/"

    def respond(url):
        if url.endswith("index.json"):
            return {"directory": {"item": [{"name": "a8k.htm"}, {"name": "ex99-1.htm"}, {"name": "R1.htm"}]}}
        return b"<html><body>Item 8.01 See Exhibit 99.1</body></html>" if url.endswith("a8k.htm") else b"<p>$12 million supply deal</p>"
    client, urls = _sec(monkeypatch, respond)
    text = client.document(base + "a8k.htm")
    assert "See Exhibit 99.1" in text and "$12 million supply deal" in text and urls[-1] == base + "ex99-1.htm"
    with pytest.raises(sec_mod.SecError):
        client.document("https://evil.example/a.htm")  # 목록 밖 주소를 원문처럼 읽게 두지 않는다


# ── 도구 ──
@pytest.fixture
def us_ws(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUTH_CORPUS", str(corpus.build_index({}, tmp_path / "c.sqlite")))
    a = article("탄테크 홀딩스 급등", "탄테크가 계약을 맺었다", aid="d" * 64).to_dict()
    return Workspace.prepare(tmp_path / "s", mode="research", case=None, inbox=[a], index=[], today="2026-10-22",
                             now="2026-10-22T11:35:00-04:00", mover={"code": "TANH", "name": "탄테크 홀딩스", "market": "NASDAQ",
                                                                      "filings": [FILING]})


def test_us_tools_use_sec_ids_and_reject_kind_ids(us_ws, monkeypatch):
    # 미국 세션에서 kind- id 를 받아 주면 없는 공시가 근거로 실린다. 공시 목록 자체는 sec-list 로 '없음'의 근거가 된다.
    monkeypatch.setattr(tools.Sec, "document", lambda self, url: "We entered into a $12 million agreement")
    tools.set_hypotheses(us_ws, {"hypotheses": [{"text": "공급 계약이 실제로 맺어졌다", "kind": "event"},
                                                 {"text": "계약 규모가 부풀려졌다", "kind": "overstated"},
                                                 {"text": "중국 테마로 올랐다", "kind": "theme"}]})
    out = tools.read_disclosure(us_ws, {"acptno": "sec-0001588084-26-000099"})
    assert out.startswith("sec-0001588084-26-000099") and "TANTECH" in out
    with pytest.raises(ToolError, match="sec-list"):
        tools.add_evidence(us_ws, {"source_id": "kind-list", "note": "공시 없음", "weight": "high", "marks": {"H1": "I"}})
    tools.add_evidence(us_ws, {"source_id": "sec-0001588084-26-000099", "note": "6-K 에 1,200만 달러 계약", "weight": "high",
                               "marks": {"H1": "C", "H2": "I", "H3": "I"}})
    tools.add_evidence(us_ws, {"source_id": "sec-list", "note": "다른 공시 없음", "weight": "high", "marks": {"H3": "I"}})
    tools.view_news(us_ws, {"id": "d" * 64})
    tools.add_evidence(us_ws, {"source_id": "news-" + "d" * 16, "note": "보도도 같은 계약", "weight": "mid",
                               "marks": {"H1": "C", "H2": "N", "H3": "I"}})
    assert tools._primary(us_ws, "sec-0001588084-26-000099")
    tools.set_verdict(us_ws, {"event_title": "1,200만 달러 공급 계약", "event_kind": "contract", "claim": "계약", "finding": "공시로 확인",
                              "reasons": ["6-K 에 계약 금액이 있다"]})
    sources = us_ws.state["moverVerdict"]["sources"]
    assert sources[0]["publisher"] == "TANTECH HOLDINGS LTD" and sources[0]["kind"] == "disclosure"
    assert any(s["publisher"] == "미국 SEC EDGAR" and "browse-edgar" in s["url"] for s in sources)
