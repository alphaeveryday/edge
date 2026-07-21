"""설명 대상 ETF 정합 테스트 (ALPHA-467 — KODEX 반도체 하드코딩 제거).

검사하는 WHY: 엔진은 ALPHAMALE_ETF_TICKER env 로 아무 ETF 나 받아 돌 수 있는데, 표시명·
구성종목명·instrument 폴백이 KODEX 반도체(091160)에 박혀 있으면 다른 ETF 를 돌려도
그 ETF 와 무관한 설명이 나오고(프롬프트가 "KODEX 반도체"), 마스터 조회 실패가 091160
instrument_id 로 조용히 폴백돼 계보가 오염된다. 대상 ETF 것만 쓰이는지 값으로 고정한다.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from edge_analysis import daily_pipeline as dp
from edge_analysis.daily_pipeline import _primary_thread_id, analyze, resolve_etf_instrument


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _FakeCursor(self._row)


def test_resolve_returns_none_when_master_missing():
    """마스터에 대상 ETF 가 없으면 None — 구현은 091160 instrument_id 로 폴백했다. 폴백은
    holdings(env 티커)와 트리거·설명(폴백 id)을 다른 축에 붙여 계보를 조용히 오염시킨다.
    호출부가 fail-loud 하도록 None 을 돌린다(Rule 12)."""
    assert resolve_etf_instrument(_FakeConn(None), "999999") is None


def test_resolve_returns_instrument_id_and_display_name():
    """표시명은 entity.display_name 에서 온다 — instrument 자체엔 이름 컬럼이 없다."""
    assert resolve_etf_instrument(_FakeConn(("inst_XYZ", "TIGER 2차전지")), "305720") == (
        "inst_XYZ", "TIGER 2차전지")


class _FakeClient:
    def __init__(self):
        self.system = None
        self.packet = None

    def complete_json(self, system, packet):
        self.system, self.packet = system, packet
        return {"verdict": "원인 미확인", "explain": "확인이 필요합니다.",
                "confidence": "보류", "key_evidence": [], "unexplained": ""}


_SETTINGS = SimpleNamespace(etf_ticker="305720", trade_date=date(2026, 7, 20))
_DECOMP = {
    "proxy_ret": 0.05, "coverage": 0.9, "top3": 0.8, "advancing": 1, "total_priced": 1,
    "n_constituents": 2,
    "members": [{"ticker": "247540", "name": "에코프로비엠", "weight": 0.25,
                 "ret": 0.08, "contribution": 0.02}],
}
_GATE = {"abs_gate": True}


def test_analyze_uses_target_etf_name_and_holdings_names_not_kodex():
    """다른 ETF 로 돌리면 프롬프트·이벤트 종목명이 그 ETF 것이어야 한다 — 구 "KODEX 반도체"
    리터럴·KODEX_CONSTITUENTS 9종목 dict 가 아니라(ALPHA-467). dict 는 다른 ETF 에도
    KODEX 종목명을 붙였고, 시스템 프롬프트는 항상 "KODEX 반도체 ETF" 로 지시했다."""
    client = _FakeClient()
    events = [{"ticker": "247540", "event_type_code": "NEWS", "novelty_status": "NEW",
               "title": "에코프로비엠 증설 발표", "available_at": "2026-07-20T09:00:00+09:00"}]
    name_by_ticker = {"247540": "에코프로비엠"}

    analyze(client, _SETTINGS, "TIGER 2차전지", name_by_ticker,
            _DECOMP, _GATE, "CONCENTRATED", events)

    assert "TIGER 2차전지 (305720)" in client.packet   # 표시명은 대상 ETF 것
    assert "TIGER 2차전지 ETF" in client.system         # 시스템 프롬프트도
    assert "에코프로비엠(247540)" in client.packet       # 이벤트 종목명은 holdings 파생
    assert "KODEX 반도체" not in client.packet
    assert "KODEX 반도체" not in client.system


def test_analyze_event_name_falls_back_to_ticker_when_absent_from_holdings():
    """이벤트 티커가 holdings 에 없으면 티커 그대로 — 옛 KODEX 종목명을 억지로 붙이지 않는다."""
    client = _FakeClient()
    events = [{"ticker": "000660", "event_type_code": "NEWS", "novelty_status": "NEW",
               "title": "무관 종목", "available_at": "2026-07-20T09:00:00+09:00"}]

    analyze(client, _SETTINGS, "TIGER 2차전지", {}, _DECOMP, _GATE, "CONCENTRATED", events)

    assert "000660(000660)" in client.packet  # 이름 없으면 티커로만
    assert "SK하이닉스" not in client.packet     # 구 KODEX_CONSTITUENTS 매핑 소멸


def test_run_fails_loud_when_holdings_empty(monkeypatch):
    """트리거가 있어도 holdings 가 비면(파티션 결손) 근거 없는 설명을 만들지 않고 fail-loud
    (Rule 12, edge-review F2). load_etf_holdings=[] 이면 proxy None·구성종목 0·뉴스 0 packet
    이 LLM 까지 가 입력 결손을 정상 분석으로 위장한다 — 그 전에 비0 종료해야 한다."""
    monkeypatch.setattr(dp, "connect", lambda s: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(dp, "load_entity_index", lambda conn: {})
    monkeypatch.setattr(dp, "resolve_etf_instrument", lambda conn, t: ("inst_A", "TIGER 2차전지"))
    monkeypatch.setattr(dp, "_boto3_client", lambda svc, s: object())
    monkeypatch.setattr(dp, "load_etf_holdings", lambda *a, **k: ([], None))
    settings = SimpleNamespace(
        trade_date=date(2026, 7, 20), request_id="r", region="ap-northeast-2",
        lake_bucket="b", etf_ticker="305720", pg={}, deepseek_api_key="k",
        deepseek_model="deepseek-chat", release_bundle_version=None,
        result_s3_prefix=None, aws_profile=None)

    with pytest.raises(dp.PipelineError):
        dp.run(settings)


def test_primary_thread_id_picks_first_threaded_not_first_row():
    """뉴스 대상을 전체 holdings 로 넓히면 upstream 이 아직 안 스레드한(thread_id NULL)
    이벤트가 목록 맨 앞에 올 수 있다 — primary_thread_id 는 events[0] 이 아니라 **스레드된
    첫 이벤트**를 골라야 계보가 안 끊긴다(edge-review, 기본 091160 런에도 회귀였던 지점)."""
    events = [
        {"ticker": "247540", "thread_id": None},        # unthreaded 가 먼저 정렬
        {"ticker": "005930", "thread_id": "thr_CORE"},  # 스레드된 core 이벤트
    ]
    assert _primary_thread_id(events) == "thr_CORE"


def test_primary_thread_id_is_none_only_when_nothing_threaded():
    """하나도 안 스레드됐을 때만 None — 그때는 계보가 실제로 없다."""
    assert _primary_thread_id([{"ticker": "x", "thread_id": None}]) is None
    assert _primary_thread_id([]) is None
