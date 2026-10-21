"""오늘 오른 종목 조사: 코드가 맡은 목록·시세·공시·게시·재조사 규칙. 시세·KIND·모델 세션은 가짜로 바꾼다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from conftest import article
from edge_truth_agent import corpus
from edge_truth_agent.session import SessionResult
from edge_truth_agent.store import LocalStore
from edge_truth_research import logos, quotes as quotes_mod, research
from edge_truth_research.quotes import Mover, is_common_stock

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 22, 11, 35, tzinfo=KST)


class FakeQuotes:
    def __init__(self, movers_by_market, *, traded=True):
        self.by_market, self.traded = movers_by_market, traded

    def session_day(self, market="KOSPI"):
        return "20261022" if self.traded else "20261021"

    def risers(self, market, min_change=0.0):
        return self.by_market.get(market, [])

    def series(self, code, market, day):
        return [["09:05", 1000.0], ["09:10", 1100.0], ["11:30", 1300.0]] if self.traded else []


def _filing(no):
    return {"acptno": no, "at": "2026-10-22 09:02", "title": "단일판매ㆍ공급계약체결", "submitter": "가나다전자",
            "url": f"https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={no}"}


class FakeKind:
    def __init__(self, extra=(), fail=()):
        self.extra, self.fail = list(extra), set(fail)

    def disclosures(self, code, since, until):
        if code in self.fail:
            raise RuntimeError("KIND 응답 오류")
        return [_filing("20261022000001"), *map(_filing, self.extra)] if code == "900010" else []

    def homepage(self, code):
        return None


def _m(code, name, pct, market="KOSDAQ", trade_value=130.0):
    return Mover(code=code, name=name, market=market, price=1300.0, prev_close=1300 / (1 + pct / 100), change_pct=pct, trade_value=trade_value)


def _verdict(tag):
    return {"tag": tag, "confidence": 0.8 if tag != "pending" else 0.2,
            "event": {"title": "공급계약 공시", "kind": "contract", "at": None}, "claim": "대형 계약을 맺었다고 알렸다",
            "finding": "계약 상대가 공시와 다르다", "reasons": ["공시와 보도의 계약 상대가 다르다"],
            "sources": [{"title": "공급계약", "publisher": "한국거래소 KIND", "url": "https://kind.krx.co.kr/x", "kind": "disclosure",
                         "publishedAt": "2026-10-22"}]}


def _judging(tags):
    """조사 세션 대신: 정해진 판정을 작업 폴더 상태에 남긴다(None = 판정 없이 끝난 세션)."""
    calls = []

    def run(cfg, ws, *, code, prompt, corpus_path):
        calls.append(code)
        if tags[code] is None:
            return SessionResult(case_id=code, done=False, error="timeout")
        state = ws.state
        state["moverVerdict"], state["done"] = _verdict(tags[code]), True
        state["ach"] = {"hypotheses": [], "evidence": []}
        ws.save_state(state)
        return SessionResult(case_id=code, done=True)
    run.calls = calls
    return run


def _seed_news(cfg):
    a = article("가나다전자 대형 공급계약 체결", "가나다전자가 공급계약을 맺었다", aid="b" * 64, published="2026-10-22 09:10:00")
    corpus.save(LocalStore(cfg.store), {a.id: a})


def _run(cfg, src, runner, *, kind=None, now=NOW):
    return research.run_research(cfg, quotes=src, kind=kind or FakeKind(), session_runner=runner, now=now)


def _latest(cfg):
    return json.loads(LocalStore(cfg.publish).get("movers/latest.json")[0])


def test_publishes_contract_valid_ranking_with_false_first(cfg):
    # 화면 계약(스키마)을 지키고, '우리 판단이 맞을 때 되돌려질 폭'(상승률 × 확신도)이 큰 허위매물이 위로 와야 한다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9), _m("900020", "라마바바이오", 15.0), _m("900030", "소폭상승", 4.0),
                                 _m("900040", "몇주체결", 29.0, trade_value=0.1)]})
    runner = _judging({"900010": "false", "900020": "pending"})
    report = _run(cfg, src, runner)
    doc = _latest(cfg)
    assert [i["code"] for i in doc["items"]] == ["900010", "900020"]  # 상승률 10%·거래대금 30억 미달은 뺀다
    top = doc["items"][0]
    assert top["tag"] == "false" and top["impact"] == round(29.9 * 0.8, 2) and doc["items"][1]["impact"] == 0
    assert top["series"][-1] == ["11:30", 1300.0] and report["published"] and sorted(runner.calls) == ["900010", "900020"]


def test_ach_table_stays_private(cfg):
    # 가설·증거 표는 감사용이다 — 공개 데이터에 새면 화면 계약(스키마)이 깨지고 내부 메모가 그대로 나간다.
    _seed_news(cfg)
    _run(cfg, FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]}), _judging({"900010": "false"}))
    assert "ach" not in _latest(cfg)["items"][0]
    seen = json.loads(LocalStore(cfg.store).get("state/movers-seen/2026-10-22.json")[0])
    assert "ach" in seen["900010"]


def test_same_day_rerun_keeps_verdicts_and_skips_judged(cfg):
    # 매시 같은 종목을 다시 판정하면 비용만 든다. 이미 판정한 종목은 시세만 새로 싣는다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    _run(cfg, src, _judging({"900010": "false"}))
    again = _judging({"900010": "promising"})
    _run(cfg, src, again, now=NOW + timedelta(hours=1))
    assert again.calls == [] and _latest(cfg)["items"][0]["tag"] == "false"


def test_published_dates_accumulate_newest_first(cfg):
    # 화면은 이 목록으로 지난 날짜를 고른다 — 다음 날 게시가 앞선 날을 지우면 그날 판정을 다시 볼 길이 없다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    _run(cfg, src, _judging({"900010": "false"}))
    _run(cfg, src, _judging({"900010": "false"}), now=NOW + timedelta(hours=1))
    # 운영 태스크는 공개 버킷을 읽을 권한이 없다 — 목록은 비공개 원본에서 이어 써야 한다(공개 사본이 사라져도)
    (Path(str(cfg.publish)) / "movers" / "dates.json").unlink()
    nxt = type("Next", (FakeQuotes,), {"session_day": lambda self, market="KOSPI": "20261023"})(src.by_market)
    _run(cfg, nxt, _judging({"900010": "false"}), now=NOW + timedelta(days=1))
    assert json.loads(LocalStore(cfg.publish).get("movers/dates.json")[0]) == ["2026-10-23", "2026-10-22"]


def test_market_closed_day_is_not_published(cfg):
    # 휴장일에 순위는 전 거래일 값을 준다 — 그날 체결이 없으면 오늘 결과로 게시하지 않는다(빈 순위도 같다).
    for src in (FakeQuotes({"KOSPI": [_m("900010", "가나다전자", 29.9, "KOSPI")]}, traded=False), FakeQuotes({}, traded=False)):
        report = _run(cfg, src, _judging({}))
        assert not report["published"] and LocalStore(cfg.publish).get("movers/latest.json")[0] is None
    assert _run(cfg, FakeQuotes({}), _judging({}))["published"]  # 장이 열린 날의 빈 목록은 게시한다


def test_unjudged_movers_are_retried_next_hour(cfg):
    # 세션이 실패했거나 한 실행의 세션 수를 넘친 종목은 보류로 싣고, 다음 실행에 다시 조사해야 한다(영영 보류로 남지 않게).
    _seed_news(cfg)
    many = [_m(f"9001{i:02d}", f"종목{i}", 20.0 + i) for i in range(research.NEW_SESSIONS)]
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9), *many]})
    first = _judging({"900010": None} | {m.code: "pending" for m in many})
    _run(cfg, src, first)
    assert len(first.calls) == research.NEW_SESSIONS
    skipped = {i["code"] for i in _latest(cfg)["items"] if i["judgedAt"] is None}
    assert "900010" in skipped and len(skipped) == 2  # 실패 1 + 넘침 1
    again = _judging({"900010": "false"} | {m.code: "pending" for m in many})
    _run(cfg, src, again, now=NOW + timedelta(hours=1))
    assert set(again.calls) == skipped


def test_disclosure_failure_defers_instead_of_judging_without_filings(cfg):
    # 공시를 못 봤는데 '최근 공시 없음'으로 판정하면 실제 공시가 있는 급등을 테마로 몰 수 있다 — 조사하지 않고 보류로 싣는다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    runner = _judging({"900010": "false"})
    report = _run(cfg, src, runner, kind=FakeKind(fail={"900010"}))
    item = _latest(cfg)["items"][0]
    assert runner.calls == [] and item["tag"] == "pending" and "공시를 조회하지 못해" in item["finding"]
    # 전 종목이 공시를 못 보면 보류만 게시하고 성공으로 끝내지 않는다(태스크 상태에 남긴다)
    assert report["deferred"] == ["900010"] and "kind" in report["failed"] and report["published"]


def test_verdict_evidence_is_recorded_only_after_publishing(cfg, monkeypatch):
    # 게시 전에 죽었는데 근거를 '본 것'으로 남기면 새 판정은 사라지고 다음 실행에도 다시 보지 않는다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    real = LocalStore.put

    def broken(self, key, *a, **kw):
        if key == "movers/latest.json":
            raise OSError("publish failed")
        return real(self, key, *a, **kw)
    monkeypatch.setattr(LocalStore, "put", broken)
    with pytest.raises(OSError):
        _run(cfg, src, _judging({"900010": "false"}))
    monkeypatch.setattr(LocalStore, "put", real)
    again = _judging({"900010": "false"})
    _run(cfg, src, again, now=NOW + timedelta(hours=1))
    assert again.calls == ["900010"]


def test_new_filing_reopens_a_settled_verdict(cfg):
    # 허위매물로 본 뒤 회사가 정정·확인 공시를 내면 판정이 뒤집힐 수 있다 — 태그와 무관하게 새 공시가 나오면 다시 연다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    _run(cfg, src, _judging({"900010": "false"}))
    again = _judging({"900010": "false"})
    _run(cfg, src, again, kind=FakeKind(extra=["20261022000099"]), now=NOW + timedelta(hours=1))
    assert again.calls == ["900010"]


def test_mover_that_drops_out_and_returns_keeps_its_verdict(cfg):
    # 15위·거래대금 경계를 오가는 종목을 돌아올 때마다 새로 조사하면 세션을 낭비하고 판정이 시간마다 뒤집힌다.
    _seed_news(cfg)
    hot = _m("900010", "가나다전자", 29.9)
    _run(cfg, FakeQuotes({"KOSDAQ": [hot]}), _judging({"900010": "false"}))
    _run(cfg, FakeQuotes({"KOSDAQ": [_m("900020", "라마바바이오", 15.0)]}), _judging({"900020": "pending"}), now=NOW + timedelta(hours=1))
    back = _judging({"900010": "promising"})
    _run(cfg, FakeQuotes({"KOSDAQ": [hot]}), back, now=NOW + timedelta(hours=2))
    assert back.calls == [] and _latest(cfg)["items"][0]["tag"] == "false"


def test_new_news_reopens_only_pending(cfg):
    # 급등주마다 특징주 기사가 매시 붙는다 — 새 보도만으로 확정 판정을 다시 열면 매 실행 세션 예산을 다 쓴다. 보류는 새 보도로 다시 본다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9), _m("900020", "가나다전자우리", 15.0)]})
    _run(cfg, src, _judging({"900010": "false", "900020": "pending"}))
    more = article("가나다전자우리·가나다전자 동반 상한가", "두 종목이 함께 올랐다", aid="d" * 64, published="2026-10-22 11:30:00")
    texts = corpus.load(LocalStore(cfg.store))
    corpus.save(LocalStore(cfg.store), texts | {more.id: more})
    again = _judging({"900010": "false", "900020": "pending"})
    _run(cfg, src, again, now=NOW + timedelta(hours=1))
    assert again.calls == ["900020"]


def test_quote_outage_is_a_failure_not_a_holiday(cfg):
    # 시세가 전부 실패한 것을 휴장으로 보고하면 화면이 조용히 한 시간 묵고 원인도 틀리게 남는다.
    class Down(FakeQuotes):
        def series(self, code, market, day):
            raise RuntimeError("Yahoo 503")
    report = _run(cfg, Down({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]}), _judging({}))
    assert not report["published"] and report["failed"] == ["series"] and "휴장" not in report["skipped"]


def test_series_keeps_only_that_day_labelled_by_interval_end(monkeypatch):
    # 휴장일·장 전에는 Yahoo 가 전 거래일 봉을 준다 — 섞이면 어제 그래프가 오늘 것처럼 보인다.
    # 봉 시각은 구간 시작이라 끝 시각으로 옮긴다(시작 시각을 달면 그래프가 5분 앞서 보인다). 빈 봉(null)은 버린다.
    day = int(datetime(2026, 10, 22, 9, 0, tzinfo=KST).timestamp())
    ts = [day - 86400 + 6 * 3600, day, day + 300, day + 600]
    chart = {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [{"close": [1.0, 1000.0, None, 1100.0]}]}}]}}
    client = quotes_mod.Quotes()
    urls = []
    monkeypatch.setattr(client, "_get", lambda url: urls.append(url) or chart)
    assert client.series("900010", "KOSDAQ", "20261022") == [["09:05", 1000.0], ["09:15", 1100.0]]
    assert urls[0].endswith("/900010.KQ?interval=5m&range=1d")


def test_risers_keep_common_stocks_and_stop_below_threshold(monkeypatch):
    # 순위의 절반쯤은 ETF·ETN 이다. 보통주만 남기고, 기준 상승률 아래로 내려가면 다음 쪽을 받지 않는다(호출 낭비).
    def row(code, name, pct, end="stock"):
        return {"itemCode": code, "stockName": name, "closePriceRaw": "11000", "compareToPreviousClosePriceRaw": "1000",
                "fluctuationsRatio": str(pct), "accumulatedTradingValueRaw": "5500000000", "stockEndType": end}
    pages = {1: [row("005930", "삼성전자", 12.0), row("069500", "KODEX 200", 11.5, "etf"), row("005935", "삼성전자우", 11.0),
                 row("900120", "가나다스팩1호", 10.5), row("0158S0", "새상장", 10.2)],
             2: [row("000660", "SK하이닉스", 9.0)]}
    client = quotes_mod.Quotes()
    seen = []

    def fake_get(url):
        page = int(url.split("page=")[1].split("&")[0])
        seen.append(page)
        return {"stocks": pages.get(page, [])}
    monkeypatch.setattr(client, "_get", fake_get)
    got = client.risers("KOSPI", 10.0)
    assert [(m.code, m.prev_close, m.trade_value) for m in got] == [("005930", 10000.0, 55.0), ("0158S0", 10000.0, 55.0)]
    assert seen == [1, 2]
    assert is_common_stock("005930", "삼성전자") and not is_common_stock("005935", "삼성전자우")


def test_changed_ranking_format_fails_instead_of_publishing_nothing(monkeypatch):
    # 응답 형식이 바뀌어 행이 다 걸러지면 장중에 '오늘 급등 없음'이 게시된다 — 형식이 다르면 실패로 올린다.
    client = quotes_mod.Quotes()
    monkeypatch.setattr(client, "_get", lambda url: {"items": [{"code": "005930"}]})
    with pytest.raises(quotes_mod.QuoteError):
        client.risers("KOSPI", 10.0)
    with pytest.raises(quotes_mod.QuoteError):
        client.session_day()


def test_trading_day_comes_from_ranking_trades_not_chart(monkeypatch):
    # 거래일은 순위의 체결 시각으로 본다(5분 시세는 지연이 있으면 첫 실행을 휴장으로 빠뜨린다). 거래정지 종목의 옛 체결일에 끌리지 않게 가장 최근 날짜.
    rows = [{"itemCode": "005930", "fluctuationsRatio": "12.0", "localTradedAt": "2026-10-20T15:30:00+09:00"},
            {"itemCode": "000660", "fluctuationsRatio": "11.0", "localTradedAt": "2026-10-22T08:55:01+09:00"}]
    client = quotes_mod.Quotes()
    monkeypatch.setattr(client, "_get", lambda url: {"stocks": rows})
    assert client.session_day() == "20261022"


def test_logo_accepts_only_safe_image_formats():
    # 로고는 우리 도메인에서 서빙된다 — SVG(스크립트 가능)·HTML 은 받지 않는다.
    assert logos.image_kind(b"\x89PNG\r\n\x1a\n....") == "png" and logos.image_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert logos.image_kind(b"<svg onload=alert(1)>") is None and logos.image_kind(b"<!doctype html>") is None
    page = '<link rel="icon" href="/a.svg"><link rel="icon" href="/fav.png"><link rel="apple-touch-icon" href="/touch.png">'
    assert logos.icon_candidates(page, "https://x.co.kr/") == ["https://x.co.kr/touch.png", "https://x.co.kr/fav.png", "https://x.co.kr/favicon.ico"]
