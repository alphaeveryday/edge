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


def test_kr_first_message_is_unchanged():
    # 국내 판정 품질은 이 첫 메시지 위에서 맞춰 왔다 — 미국을 넣으며 국내 글이 한 글자라도 바뀌면 국내 판정이 흔들린다
    m = Mover(code="900010", name="가나다전자", market="KOSDAQ", price=13000.0, prev_close=10000.0, change_pct=30.0, trade_value=130.0)
    got = research._prompt(m, [], [], [["09:05", 10000.0]])
    assert got.startswith("오늘 급등한 종목: 가나다전자(900010, KOSDAQ) 현재가 13,000원, 전일 대비 +30.00% (전일 종가 10,000원, 거래대금 130억원).\n"
                          "장중 흐름(30분 간격): 09:05 10,000\n\n최근 14일 공시·시장조치(한국거래소 KIND, 접수번호 | 시각 | 제출 | 제목 — 목록 자체는 kind-list):\n")


def test_routine_us_filings_do_not_reopen_a_settled_verdict(cfg):
    # 급등주에는 임원 매매(Form 4)·5% 미만 보고(13G)가 수시로 붙는다. 그때마다 판정을 다시 열면 세션을 다 쓰고 판정이 매시 흔들린다.
    # 새 주식 발행(424B)은 급등과 직접 얽힌 사건이라 다시 연다.
    src = FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]})
    _run(cfg, src, _judging({"TANH": "false"}))

    class More(FakeSec):
        def __init__(self, form):
            super().__init__()
            self.form = form

        def disclosures(self, code, since, until):
            return [FILING, FILING | {"acptno": "0001588084-26-000100", "title": f"{self.form} · X"}]
    again = _judging({"TANH": "pending"})
    _run(cfg, src, again, kind=More("4"), now=NOW + timedelta(hours=1))
    _run(cfg, src, again, kind=More("SCHEDULE 13G"), now=NOW + timedelta(hours=1, minutes=1))  # 2024년 말 이후 실제 서식명
    assert again.calls == []
    _run(cfg, src, again, kind=More("424B5"), now=NOW + timedelta(hours=2))
    assert [c[0] for c in again.calls] == ["TANH"]


def _web(url, published="2026-10-22T13:00:00.000Z"):
    from edge_truth_agent.tools import _web_id
    return {"id": _web_id(url), "url": url, "title": "Tantech soars on deal", "publishedDate": published, "kind": "web",
            "text": "Tantech Holdings Ltd announced a supply agreement", "full": True}


def _seed_korean_news(cfg):
    a = article("[美특징주] 탄테크 홀딩스 급등", "탄테크 홀딩스가 올랐다", aid="e" * 64, published="2026-10-22 22:10:00")
    corpus.save(LocalStore(cfg.store), {a.id: a})
    return a


def _reading(tag="pending"):
    """조사 세션 대신: 첫 메시지와 작업 폴더(받은 원문·국내 기사)를 기록하고 판정을 남긴다."""
    seen = []

    def run(cfg, ws, *, code, prompt, corpus_path):
        ids = [line[2:].split(" | ")[0] for line in prompt.splitlines() if line.startswith("- web-")]
        seen.append({"prompt": prompt, "inbox": ws.inbox(), "snap": {w: ws.snapshot(w) for w in ids}})
        return _judging({code: tag})(cfg, ws, code=code, prompt=prompt, corpus_path=corpus_path)
    run.seen = seen
    return run


def test_us_news_comes_from_exa_first_and_is_readable_without_web_budget(cfg):
    # 미국 종목은 국내 수집 기사(빅카인즈)에 거의 없다 — 영어 보도를 코드가 먼저 모아 첫 메시지에 싣는다.
    # 원문을 작업 폴더에 넣어 두어야 모델이 세션 웹 예산(10회)을 목록 찾는 데 쓰지 않고 바로 읽는다.
    _seed_korean_news(cfg)
    asked, runner = [], _reading()
    web = [_web("https://www.globenewswire.com/a"), _web("https://www.reuters.com/b", "2026-10-22T12:00:00.000Z")]
    _run(cfg, FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]}), runner,
         web_news=lambda m, since: asked.append((m.code, since)) or web)
    got = runner.seen[0]
    assert asked == [("TANH", "2026-10-08")]
    assert "영어 웹 보도" in got["prompt"] and web[0]["id"] in got["prompt"] and "news-" not in got["prompt"]
    assert got["inbox"] == [] and got["snap"][web[0]["id"]]["full"]  # 웹 보도가 있으면 국내 기사는 싣지 않는다


def test_exa_news_drops_pages_outside_the_window(monkeypatch):
    # 기간 조건을 걸어도 Exa 가 몇 년 전 시세 페이지를 섞어 준다(실측) — 그대로 실으면 모델이 옛 글을 오늘 급등 이유로 읽는다
    from edge_truth_agent import web as web_mod
    sent = {}

    def post(self, path, body):
        sent.update(body)
        about = "Tantech (NASDAQ:TANH) shares jumped"
        return {"results": [{"url": "https://a.com/mid", "title": "m", "publishedDate": "2026-10-20T10:00:00.000Z", "text": about},
                            {"url": "https://a.com/new", "title": "n", "publishedDate": "2026-10-22T10:00:00.000Z", "text": about},
                            {"url": "https://a.com/old", "title": "o", "publishedDate": "2017-05-23T00:00:00.000Z", "text": about},
                            {"url": "https://a.com/nodate", "title": "Tantech Holdings wins order", "text": "x"},
                            {"url": "https://a.com/other", "title": "Other Corp", "publishedDate": "2026-10-22T11:00:00.000Z",
                             "text": "Other Corp (NYSE:OTH) rose"},
                            {"url": "https://a.com/empty", "title": "e", "publishedDate": "2026-10-21T00:00:00.000Z", "text": ""}]}
    monkeypatch.setattr(web_mod.Exa, "_post", post)
    mover = Mover(code="TANH", name="탄테크 홀딩스", market="NASDAQ", price=24.83, prev_close=8.81, change_pct=181.8, trade_value=50.9,
                  name_eng="Tantech Holdings Ltd")
    got = research.exa_news("k", mover, "2026-10-08")
    # 최신순(입력 순서와 다르게), 날짜 모름은 뒤. 다른 회사 글은 버린다 — 하나라도 남으면 빅카인즈 대신을 막아 엉뚱한 보도만 싣게 된다
    assert [w["url"] for w in got] == ["https://a.com/new", "https://a.com/mid", "https://a.com/nodate"] and all(w["full"] for w in got)
    assert sent["startPublishedDate"] == "2026-10-08T00:00:00.000Z" and sent["category"] == "news"


def _fail(m, since):
    raise RuntimeError("Exa 429")


@pytest.mark.parametrize("search", [lambda m, s: [], _fail, None])
def test_us_falls_back_to_korean_news_only_without_exa_results(cfg, search):
    # Exa 가 비거나 실패하거나 키가 없으면(cfg.exa_key 없음 → None) 그 종목은 빅카인즈 기사로 대신한다 — 보도 없이 판정하지 않게.
    # 대신한 사실은 실행 보고에 남긴다.
    a = _seed_korean_news(cfg)
    runner = _reading()
    report = _run(cfg, FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]}), runner, web_news=search)
    got = runner.seen[0]
    assert a.source_id in got["prompt"] and "국내 수집 기사로 대신" in got["prompt"] and report["newsFallback"] == ["TANH"]
    assert [x["id"] for x in got["inbox"]] == [a.id]


def test_us_pending_reopens_only_for_web_news_published_after_the_verdict(cfg):
    # 웹 검색은 같은 질의에도 매번 결과가 조금씩 바뀐다 — 그것만으로 보류 판정을 매시 다시 열면 세션을 다 쓴다.
    # 판정 뒤에 새로 발행된 보도가 나왔을 때만 다시 본다.
    src = FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]})
    first = [_web("https://www.globenewswire.com/a")]
    _run(cfg, src, _reading("pending"), web_news=lambda m, s: first)
    again = _reading("pending")
    older = first + [_web("https://www.reuters.com/older", "2026-10-22T14:00:00.000Z")]  # 판정(11:35 EDT = 15:35Z) 전 발행
    _run(cfg, src, again, web_news=lambda m, s: older, now=NOW + timedelta(hours=1))
    assert again.seen == []
    newer = first + [_web("https://www.reuters.com/newer", "2026-10-22T16:10:00.000Z")]  # 판정 뒤 발행
    _run(cfg, src, again, web_news=lambda m, s: newer, now=NOW + timedelta(hours=2))
    assert len(again.seen) == 1


def test_us_pending_does_not_reopen_when_the_news_source_switches(cfg):
    # Exa 가 한 번 막혀 빅카인즈로 대신하면 기사 id 가 통째로 달라진다 — 그것을 '새 보도'로 세면 Exa 가 흔들릴 때마다 보류가 매시 다시 열린다
    _seed_korean_news(cfg)  # 22:10 KST = 09:10 EDT 발행 — 판정(11:35 EDT) 전
    src = FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]})
    _run(cfg, src, _reading("pending"), web_news=lambda m, s: [_web("https://www.globenewswire.com/a")])
    again = _reading("pending")
    report = _run(cfg, src, again, web_news=_fail, now=NOW + timedelta(hours=1))
    assert again.seen == [] and report["newsFallback"] == ["TANH"] and "web-news" in report["failed"]  # 전면 장애는 실패로 드러낸다


def test_us_web_news_times_are_shown_in_new_york_time(cfg):
    # 첫 메시지는 '시각은 뉴욕 현지'라고 한다 — UTC 를 그대로 자르면 13:00Z(=09:00 EDT) 기사가 13:00 으로 보여 급등 전후가 뒤집힌다
    runner = _reading()
    _run(cfg, FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8)]}), runner,
         web_news=lambda m, s: [_web("https://www.reuters.com/b", "2026-10-22T13:00:00.000Z") | {"title": "Tantech\nsoars"}])
    line = next(x for x in runner.seen[0]["prompt"].splitlines() if x.startswith("- web-"))
    assert "| 2026-10-22 09:00 |" in line and line.endswith("Tantech soars")


def test_us_searches_news_only_for_movers_that_can_get_a_session(cfg):
    # 확정 판정은 보도로 다시 열지 않으니 검색해도 쓰이지 않는다 — 매 실행 15종목 검색은 비용만 든다(매분 감시는 6분마다 실행을 띄운다)
    src = FakeQuotes({"NASDAQ": [_m("TANH", "탄테크 홀딩스", 181.8), _m("XLAB", "엑사스케일", 73.2)]})
    _run(cfg, src, _judging({"TANH": "false", "XLAB": "pending"}), web_news=lambda m, s: [])
    asked = []
    _run(cfg, src, _judging({"XLAB": "pending"}), web_news=lambda m, s: asked.append(m.code) or [], now=NOW + timedelta(hours=1))
    assert asked == ["XLAB"]  # 허위로 확정된 TANH 는 찾지 않는다(새 공시가 나오면 그때 다시 찾는다)


def test_kr_runs_never_call_exa(cfg):
    # 국내는 빅카인즈가 그대로 주 출처다 — 미국 웹 검색이 국내 실행에 끼면 비용만 들고 첫 메시지가 바뀐다.
    from test_research import FakeKind, FakeQuotes as KrQuotes, _m as kr_m

    def boom(m, since):
        raise AssertionError("국내 실행이 Exa 를 불렀다")
    report = research.run_research(cfg, quotes=KrQuotes({"KOSDAQ": [kr_m("900010", "가나다전자", 29.9)]}), kind=FakeKind(),
                                   session_runner=_judging({"900010": "false"}), now=datetime(2026, 10, 22, 11, 35, tzinfo=KST),
                                   web_news=boom)
    assert report["published"] and "newsFallback" not in report


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
    # 반 클래스 주식은 'BRK B' 처럼 띄어 온다(실측) — 보통주로 살리고 BRK.B 로 맞춘다. 우선주 'AHT PRD' 는 거른다
    rows = [row("TANH", "Tantech Holdings Ltd", 181.8), row("HAVAR", "Harvard Ave Acquisition Rights Expiry", 21.0),
            row("ABCDW", "Abcd Corp Warrant", 20.0), row("SOXL", "Direxion Semi Bull 3X", 15.0, "etf"),
            row("BRK B", "Berkshire Hathaway Inc", 14.0), row("AHT PRD", "Ashford Hospitality Trust Pref D", 13.0),
            row("EVIL", "Evil Inc", 12.0, logo="http://evil.example/x.png"), row("LOW", "Low Inc", 9.0)]
    client = quotes_mod.Quotes()
    urls = []
    monkeypatch.setattr(client, "_get", lambda url: urls.append(url) or {"stocks": rows})
    got = client.risers("NASDAQ", 10.0)
    assert [m.code for m in got] == ["TANH", "BRK.B", "EVIL"] and urls[0].startswith("https://api.stock.naver.com/stock/exchange/NASDAQ/up?")
    tanh = got[0]
    assert tanh.trade_value == pytest.approx(50.942) and tanh.prev_close == pytest.approx(8.81) and tanh.logo_url.startswith("https://ssl.pstatic.net/")
    assert tanh.name_eng == "Tantech Holdings Ltd"
    assert got[2].logo_url is None  # 로고는 우리 도메인에서 서빙된다 — Naver 이미지 서버 주소만 받는다
    # 실제로 읽는 원시값 칸이 사라지면 전부 0% 로 걸러져 '급등 없음'이 게시된다 — 형식 변경으로 실패시킨다
    for r in rows:
        r.pop("fluctuationsRatioRaw")
    with pytest.raises(quotes_mod.QuoteError):
        client.risers("NASDAQ", 10.0)


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
    # 접수 시각은 실측 쌍(BHVN 2026-10): JSON 15:00:07Z ↔ EDGAR 접수 페이지 07:00:07, JSON 10-16 01:18:55Z ↔ 10-15 17:18:55
    recent = {"accessionNumber": ["0001-26-3", "0001-26-2", "0001-26-1"], "filingDate": ["2026-10-22", "2026-10-15", "2026-09-01"],
              "acceptanceDateTime": ["2026-10-22T15:00:07.000Z", "2026-10-16T01:18:55.000Z", "2026-09-01T20:00:00.000Z"],
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
    # JSON 시각은 Z 가 붙어도 UTC 가 아니다 — UTC 로 읽으면 장 전 07:00 보도자료가 11:00(급등 뒤)으로 보여 판정이 뒤집힌다
    assert got[0]["at"] == "2026-10-22 07:00" and got[0]["title"] == "8-K · 1.01 중요 계약 체결 · 9.01"
    assert got[1]["at"] == "2026-10-15 17:18" and got[1]["title"] == "S-1 · REGISTRATION STATEMENT"  # 접수일(filingDate)과 날짜가 맞는다
    # 겨울(EST)은 차이가 한 시간 더 크다(실측 AAPL 2026-02-25T02:55:58Z ↔ 2026-02-24 16:55:58)
    assert sec_mod.accepted_at("2026-02-25T02:55:58.000Z").isoformat() == "2026-02-24T16:55:58-05:00"
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


def test_us_regulator_originals_count_and_english_name_identifies_the_company(us_ws, tmp_path):
    # 미국 급등의 확인 원문은 FDA·SEC 같은 미국 기관 영어 원문이다. 국내 기관 도메인만 인정하면 FDA 승인도 '확인 불가'로 남아
    # 미국 판정이 늘 보류로 쏠린다. 회사 이름은 영어 원문에 한글로 나오지 않으니 영문명(몸통)으로 이 회사 글인지 가린다.
    def web(sid, url, text):
        us_ws.save_snapshot(sid, {"id": sid, "url": url, "title": "t", "kind": "web", "text": text, "full": True})
    mover = us_ws.mover() | {"nameEng": "Tantech Holdings Ltd"}
    (us_ws.root / "mover.json").write_text(json.dumps(mover, ensure_ascii=False), encoding="utf-8")
    web("web-fda", "https://www.fda.gov/news-events/x", "FDA approves a drug")
    web("web-sec", "https://www.sec.gov/Archives/edgar/data/1/x.htm", "TANTECH HOLDINGS LTD entered into an agreement")
    web("web-other", "https://www.sec.gov/Archives/edgar/data/2/y.htm", "Another Company Inc agreement")
    web("web-fake", "https://fda.gov.evil.example/x", "Tantech approval")
    assert tools._official(us_ws, "web-fda") and not tools._official(us_ws, "web-fake")
    assert tools._primary(us_ws, "web-sec") and not tools._primary(us_ws, "web-other")
    # 이름을 짧게 잘라 낱말 일부로 맞추면 남의 기관 글이 '이 회사 원문'이 된다(근거 없는 유망주 판정)
    for eng, unrelated, own in (("Arm Holdings plc", "pharmaceutical farm alarm", "Arm Holdings announced"),
                                ("Click Holdings Limited", "Click here to read", "CLICK HOLDINGS filed"),
                                ("Target Corp", "the target date", "Target agreed")):
        (us_ws.root / "mover.json").write_text(json.dumps(mover | {"nameEng": eng}, ensure_ascii=False), encoding="utf-8")
        web("web-x", "https://www.sec.gov/Archives/edgar/data/9/z.htm", unrelated)
        assert not tools._primary(us_ws, "web-x"), eng
        web("web-x", "https://www.sec.gov/Archives/edgar/data/9/z.htm", own)
        assert tools._primary(us_ws, "web-x"), eng
    # 국내 세션은 공유 기준 그대로다(미국 기관 도메인을 공식으로 보지 않는다)
    kr = Workspace.prepare(tmp_path / "kr", mode="research", case=None, inbox=[], index=[], today="2026-10-22",
                           now="2026-10-22T11:35:00+09:00", mover={"code": "900010", "name": "가나다전자", "filings": []})
    kr.save_snapshot("web-fda", {"id": "web-fda", "url": "https://www.fda.gov/x", "title": "t", "kind": "web", "text": "x", "full": True})
    assert not tools._official(kr, "web-fda")
