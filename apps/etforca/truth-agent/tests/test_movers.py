"""오늘의 급등주: 코드가 맡은 목록·시세·게시·판정 규칙. 시세·DART·모델은 가짜로 바꾼다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from conftest import article
from edge_truth_agent import corpus, logos, movers, quotes as quotes_mod, tools
from edge_truth_agent.quotes import Mover, is_common_stock
from edge_truth_agent.session import SessionResult
from edge_truth_agent.store import LocalStore
from edge_truth_agent.workspace import Workspace

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 22, 11, 0, tzinfo=KST)


class FakeQuotes:
    def __init__(self, movers_by_market, *, traded=True):
        self.by_market, self.traded = movers_by_market, traded

    def session_day(self, market="KOSPI"):
        return "20261022" if self.traded else "20261021"

    def risers(self, market, min_change=0.0):
        return self.by_market.get(market, [])

    def series(self, code, market, day):
        return [["09:00", 1000.0], ["09:05", 1100.0], ["10:55", 1300.0]] if self.traded else []


def _filing(no):
    return {"rcept_no": no, "report_nm": "단일판매ㆍ공급계약체결", "rcept_dt": "2026-10-22", "flr_nm": "가나다전자",
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={no}"}


class FakeDart:
    def __init__(self, extra=(), fail=()):
        self.extra, self.fail = list(extra), set(fail)

    def corp_codes(self):
        return {"900010": "00000001", "900020": "00000002"} | {f"9001{i:02d}": f"1{i:07d}" for i in range(10)}

    def filings(self, corp, since, until):
        if corp in self.fail:
            raise RuntimeError("DART 응답 오류")
        return [_filing("20261022000001"), *map(_filing, self.extra)] if corp == "00000001" else []

    def homepage(self, corp):
        return None


def _m(code, name, pct, market="KOSDAQ", trade_value=130.0):
    return Mover(code=code, name=name, market=market, price=1300.0, prev_close=1300 / (1 + pct / 100), change_pct=pct, trade_value=trade_value)


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
        if tag is None:  # 세션이 판정 없이 끝났다(시간 초과 등)
            return SessionResult(case_id=case_id, done=False, error="timeout")
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
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9), _m("900020", "라마바바이오", 15.0), _m("900030", "소폭상승", 4.0),
                              _m("900040", "몇주체결", 29.0, trade_value=0.1)]})
    runner = _judging({"900010": "false", "900020": "pending"})
    report = movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=runner, now=NOW)
    doc = json.loads((LocalStore(cfg.publish).get("movers/latest.json")[0]))
    assert [i["code"] for i in doc["items"]] == ["900010", "900020"]  # 상승률 10%·거래대금 30억 미달은 뺀다
    top = doc["items"][0]
    assert top["tag"] == "false" and top["impact"] == round(29.9 * 0.8, 2) and doc["items"][1]["impact"] == 0
    assert top["series"][-1] == ["10:55", 1300.0] and report["published"] and sorted(runner.calls) == ["900010", "900020"]


def test_same_day_rerun_keeps_verdicts_and_skips_judged(cfg):
    # 매시 같은 종목을 다시 판정하면 비용만 든다. 이미 판정한 종목은 시세만 새로 싣는다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW)
    again = _judging({"900010": "promising"})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=again, now=NOW + timedelta(hours=1))
    doc = json.loads(LocalStore(cfg.publish).get("movers/latest.json")[0])
    assert again.calls == [] and doc["items"][0]["tag"] == "false"


def test_published_dates_accumulate_newest_first(cfg):
    # 화면은 이 목록으로 지난 날짜를 고른다 — 다음 날 게시가 앞선 날을 지우면 그날 판정을 다시 볼 길이 없다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW)
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW + timedelta(hours=1))
    nxt = type("Next", (FakeQuotes,), {"session_day": lambda self, market="KOSPI": "20261023"})(src.by_market)
    movers.run_movers(cfg, quotes=nxt, dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW + timedelta(days=1))
    assert json.loads(LocalStore(cfg.publish).get("movers/dates.json")[0]) == ["2026-10-23", "2026-10-22"]


def test_market_closed_day_is_not_published(cfg):
    # 휴장일에 순위는 전 거래일 값을 준다 — 그날 체결이 없으면 오늘 결과로 게시하지 않는다.
    src = FakeQuotes({"KOSPI": [_m("900010", "가나다전자", 29.9, "KOSPI")]}, traded=False)
    report = movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=_judging({}), now=NOW)
    assert not report["published"] and LocalStore(cfg.publish).get("movers/latest.json")[0] is None


def test_empty_ranking_on_closed_day_is_not_published(cfg):
    # 휴장일에 순위가 비어 오면 '오늘 급등 없음'으로 덮어쓰지 않는다. 장이 열린 날의 빈 목록은 게시한다.
    report = movers.run_movers(cfg, quotes=FakeQuotes({}, traded=False), dart=FakeDart(), session_runner=_judging({}), now=NOW)
    assert not report["published"] and LocalStore(cfg.publish).get("movers/latest.json")[0] is None
    assert movers.run_movers(cfg, quotes=FakeQuotes({}), dart=FakeDart(), session_runner=_judging({}), now=NOW)["published"]


def test_unjudged_movers_are_retried_next_hour(cfg):
    # 세션이 실패했거나 한 실행의 세션 수를 넘친 종목은 보류로 싣고, 다음 정각에 다시 판정해야 한다(영영 보류로 남지 않게).
    _seed_news(cfg)
    many = [_m(f"9001{i:02d}", f"종목{i}", 20.0 + i) for i in range(movers.NEW_SESSIONS)]
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9), *many]})
    first = _judging({"900010": None} | {m.code: "pending" for m in many})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=first, now=NOW)
    assert len(first.calls) == movers.NEW_SESSIONS
    doc = json.loads(LocalStore(cfg.publish).get("movers/latest.json")[0])
    skipped = {i["code"] for i in doc["items"] if i["judgedAt"] is None}
    assert "900010" in skipped and len(skipped) == 2  # 실패 1 + 넘침 1
    again = _judging({"900010": "false"} | {m.code: "pending" for m in many})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=again, now=NOW + timedelta(hours=1))
    assert set(again.calls) == skipped


def test_dart_failure_defers_instead_of_judging_without_filings(cfg):
    # 공시를 못 봤는데 '최근 공시 없음'으로 판정하면 실제 공시가 있는 급등을 풍문으로 몰 수 있다 — 판정하지 않고 보류로 싣는다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    runner = _judging({"900010": "false"})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(fail={"00000001"}), session_runner=runner, now=NOW)
    item = json.loads(LocalStore(cfg.publish).get("movers/latest.json")[0])["items"][0]
    assert runner.calls == [] and item["tag"] == "pending" and "공시를 조회하지 못해" in item["finding"]
    # 전 종목이 공시를 못 보면 보류만 게시하고 성공으로 끝내지 않는다(태스크 상태에 남긴다)
    report = movers.run_movers(cfg, quotes=src, dart=None, session_runner=runner, now=NOW + timedelta(hours=1))
    assert report["deferred"] == ["900010"] and "dart" in report["failed"] and report["published"]


def test_verdict_evidence_is_recorded_only_after_publishing(cfg, monkeypatch):
    # 게시 전에 죽었는데 근거를 '본 것'으로 남기면 새 판정은 사라지고 다음 정각에도 다시 보지 않는다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    real = LocalStore.put

    def broken(self, key, *a, **kw):
        if key == "movers/latest.json":
            raise OSError("publish failed")
        return real(self, key, *a, **kw)
    monkeypatch.setattr(LocalStore, "put", broken)
    with pytest.raises(OSError):
        movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW)
    monkeypatch.setattr(LocalStore, "put", real)
    again = _judging({"900010": "false"})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=again, now=NOW + timedelta(hours=1))
    assert again.calls == ["900010"]


def test_new_filing_reopens_a_settled_verdict(cfg):
    # 허위매물로 본 뒤 회사가 정정·확인 공시를 내면 판정이 뒤집힐 수 있다 — 태그와 무관하게 새 근거가 나오면 다시 연다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW)
    again = _judging({"900010": "false"})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(extra=["20261022000099"]), session_runner=again, now=NOW + timedelta(hours=1))
    assert again.calls == ["900010"]


def test_mover_that_drops_out_and_returns_keeps_its_verdict(cfg):
    # 15위·거래대금 경계를 오가는 종목을 돌아올 때마다 새로 판정하면 세션을 낭비하고 판정이 시간마다 뒤집힌다.
    _seed_news(cfg)
    hot = _m("900010", "가나다전자", 29.9)
    movers.run_movers(cfg, quotes=FakeQuotes({"KOSDAQ": [hot]}), dart=FakeDart(), session_runner=_judging({"900010": "false"}), now=NOW)
    movers.run_movers(cfg, quotes=FakeQuotes({"KOSDAQ": [_m("900020", "라마바바이오", 15.0)]}), dart=FakeDart(),
                      session_runner=_judging({"900020": "pending"}), now=NOW + timedelta(hours=1))
    back = _judging({"900010": "promising"})
    movers.run_movers(cfg, quotes=FakeQuotes({"KOSDAQ": [hot]}), dart=FakeDart(), session_runner=back, now=NOW + timedelta(hours=2))
    item = json.loads(LocalStore(cfg.publish).get("movers/latest.json")[0])["items"][0]
    assert back.calls == [] and item["tag"] == "false"


def test_new_news_reopens_only_pending(cfg):
    # 급등주마다 특징주 기사가 매시 붙는다 — 새 보도만으로 확정 판정을 다시 열면 매 정각 세션 예산을 다 쓴다. 보류는 새 보도로 다시 본다.
    _seed_news(cfg)
    src = FakeQuotes({"KOSDAQ": [_m("900010", "가나다전자", 29.9), _m("900020", "가나다전자우리", 15.0)]})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=_judging({"900010": "false", "900020": "pending"}), now=NOW)
    more = article("가나다전자우리·가나다전자 동반 상한가", "두 종목이 함께 올랐다", aid="d" * 64, published="2026-10-22 11:30:00")
    texts = corpus.load(LocalStore(cfg.store))
    corpus.save(LocalStore(cfg.store), texts | {more.id: more})
    again = _judging({"900010": "false", "900020": "pending"})
    movers.run_movers(cfg, quotes=src, dart=FakeDart(), session_runner=again, now=NOW + timedelta(hours=1))
    assert again.calls == ["900020"]


def test_quote_outage_is_a_failure_not_a_holiday(cfg):
    # 시세가 전부 실패한 것을 휴장으로 보고하면 화면이 조용히 한 시간 묵고 원인도 틀리게 남는다.
    class Down(FakeQuotes):
        def series(self, code, market, day):
            raise RuntimeError("Yahoo 503")
    report = movers.run_movers(cfg, quotes=Down({"KOSDAQ": [_m("900010", "가나다전자", 29.9)]}), dart=FakeDart(),
                               session_runner=_judging({}), now=NOW)
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
    # 거래일은 순위의 체결 시각으로 본다(5분 시세는 지연이 있으면 09:00 실행을 휴장으로 빠뜨린다). 거래정지 종목의 옛 체결일에 끌리지 않게 가장 최근 날짜.
    rows = [{"itemCode": "005930", "fluctuationsRatio": "12.0", "localTradedAt": "2026-10-20T15:30:00+09:00"},
            {"itemCode": "000660", "fluctuationsRatio": "11.0", "localTradedAt": "2026-10-22T08:55:01+09:00"}]
    client = quotes_mod.Quotes()
    monkeypatch.setattr(client, "_get", lambda url: {"stocks": rows})
    assert client.session_day() == "20261022"


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


def test_news_page_opened_on_the_web_is_not_a_primary_source(tmp_path, monkeypatch):
    # 기사 페이지를 web_fetch 로 열었다고 원문이 되지 않는다 — 유망주는 공식·공시 원문만.
    monkeypatch.setenv("TRUTH_CORPUS", str(corpus.build_index({}, tmp_path / "c.sqlite")))
    ws = _ws(tmp_path)
    for sid, kind in (("web-news1", "news"), ("web-official1", "official")):
        ws.save_snapshot(sid, {"url": f"https://example.com/{sid}", "title": "원문", "kind": kind, "text": "본문"})
    state = ws.state
    state["viewed"] = ["web-news1", "web-official1"]
    ws.save_state(state)
    base = {"tag": "promising", "confidence": 0.7, "event_title": "공급계약 공시", "event_kind": "contract", "claim": "주장",
            "finding": "확인한 것", "reasons": ["공시와 일치"]}
    with pytest.raises(tools.ToolError, match="원문"):
        tools.set_mover_verdict(ws, base | {"source_ids": ["web-news1"]})
    assert "저장" in tools.set_mover_verdict(ws, base | {"source_ids": ["web-official1"]})
    assert ws.state["moverVerdict"]["sources"][0]["kind"] == "official"


def test_verdict_that_cannot_be_published_is_rejected_in_session(tmp_path, monkeypatch):
    # 게시 단계에서야 형식 오류가 나면 세션은 성공, 종목은 화면에서 빠진 채 매시 같은 판정을 되풀이한다 — 세션 안에서 고치게 한다.
    monkeypatch.setenv("TRUTH_CORPUS", str(corpus.build_index({}, tmp_path / "c.sqlite")))
    a = article("가나다전자 계약", "요약", aid="e" * 64).to_dict() | {"url": ""}
    ws = _ws(tmp_path, [a])
    tools.view_news(ws, {"id": a["id"]})
    with pytest.raises(tools.ToolError, match="게시 형식"):
        tools.set_mover_verdict(ws, {"tag": "false", "confidence": 0.7, "event_title": "공급계약 공시", "event_kind": "contract",
                                     "claim": "주장", "finding": "확인한 것", "reasons": ["이유"], "source_ids": ["news-" + "e" * 16]})


def test_logo_accepts_only_safe_image_formats():
    # 로고는 우리 도메인에서 서빙된다 — SVG(스크립트 가능)·HTML 은 받지 않는다.
    assert logos.image_kind(b"\x89PNG\r\n\x1a\n....") == "png" and logos.image_kind(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert logos.image_kind(b"<svg onload=alert(1)>") is None and logos.image_kind(b"<!doctype html>") is None
    page = '<link rel="icon" href="/a.svg"><link rel="icon" href="/fav.png"><link rel="apple-touch-icon" href="/touch.png">'
    assert logos.icon_candidates(page, "https://x.co.kr/") == ["https://x.co.kr/touch.png", "https://x.co.kr/fav.png", "https://x.co.kr/favicon.ico"]
