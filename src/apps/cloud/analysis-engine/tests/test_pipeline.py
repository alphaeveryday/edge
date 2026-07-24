"""run 오케스트레이션 테스트.

의존성을 fake 로 주입해 I/O 가 아니라 제어 흐름을 고정한다: 트리거 없는 날은 분석
없이 잔잔히 종료하고, 트리거 있는 날은 설명을 영속하며, FK 전제가 없는 날은 고아
행을 넣는 대신 S3 로 폴백한다.
"""

import json
from datetime import date
from types import SimpleNamespace

from edge_analysis.domain.models import Holding, PriceTrigger
from edge_analysis.pipeline import run

_SETTINGS = SimpleNamespace(
    trade_date=date(2026, 7, 16),
    request_id="req-1",
    etf_ticker="091160",
    lake_bucket="test-lake",
    result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
    release_bundle_version="b1",
)


class _FakeLake:
    def load_holdings(self, etf_id, market, trade_date):
        return [Holding("005930", "삼성전자", 1.0)], "2026-07-15"

    def load_returns(self, market, trade_date):
        return {"005930": 0.05}


class _FakeStore:
    def __init__(self, trigger, prereqs):
        self._trigger = trigger
        self._prereqs = prereqs
        self.calls: list[str] = []

    def load_entity_index(self):
        return {"005930": "ent_1"}

    def resolve_etf_instrument(self, ticker):
        return ("inst_ETF", "테스트 ETF")

    def fetch_price_trigger(self, etf_instrument_id, trade_date):
        return self._trigger

    def persist_observation_route(self, trigger_id, decomp, route_code, event_search, entity_index):
        self.calls.append("obs_route")
        return {"trigger_id": trigger_id, "obs_id": "cob_1", "route_id": "rte_1"}

    def fetch_event_contexts(self, trade_date, tickers):
        return []

    def explanation_prerequisites(self, settings, etf_instrument_id):
        return self._prereqs

    def persist_explanation(self, settings, etf_instrument_id, explanation, **kwargs):
        self.calls.append("persist_explanation")
        return {"persisted": "rds", "explanation_result_id": "res_1", "run_id": "run_1"}


class _FakeClient:
    def complete_json(self, system, user):
        return {"verdict": "시장·섹터 주도", "explain": "…"}


class _FakeS3:
    def __init__(self):
        self.puts = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


_TRIGGER = PriceTrigger("pmt_1", 0.05, "abs", abs_gate=True, rel_gate=False)
_PREREQS_OK = {"profile": True, "route": "rte_1", "bundle": "b1"}


def _run(store, s3):
    return run(_SETTINGS, lake=_FakeLake(), store=store, client=_FakeClient(), s3=s3)


def _outcomes(s3):
    return [json.loads(p["Body"].decode("utf-8")).get("outcome") for p in s3.puts]


def test_no_trigger_exits_without_analysis():
    store = _FakeStore(trigger=None, prereqs=_PREREQS_OK)
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert store.calls == []  # observation/route·설명 없음
    assert _outcomes(s3) == ["normal_variation"]


def test_triggered_day_persists_the_explanation():
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert store.calls == ["obs_route", "persist_explanation"]
    assert "explained" in _outcomes(s3)
    bodies = [json.loads(p["Body"].decode("utf-8")) for p in s3.puts]
    archive = next(b for b in bodies if b.get("outcome") == "explained")
    assert "events" in archive  # 런 아카이브 이벤트 키 — 구 "kodex_events" 는 소비자 계약이 아니다


def test_missing_prerequisites_fall_back_to_s3():
    store = _FakeStore(trigger=_TRIGGER, prereqs={"profile": False, "route": None, "bundle": None})
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert "persist_explanation" not in store.calls  # 고아 RDS 행 없음
    keys = [p["Key"] for p in s3.puts]
    assert any("/runs/" not in k for k in keys)  # 설명 S3 폴백
    assert any("/runs/" in k for k in keys)      # 런 아카이브
