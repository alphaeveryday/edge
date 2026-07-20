"""Tests for the run orchestration.

Dependencies are injected as fakes, so these pin control flow rather than I/O:
a trigger-less day exits calmly without analysis, a triggered day persists the
explanation, and a day missing FK prerequisites falls back to S3 instead of
inserting an orphan row.
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
        return "inst_ETF"

    def fetch_price_trigger(self, etf_instrument_id, trade_date):
        return self._trigger

    def persist_observation_route(self, trigger_id, decomp, route_code, event_search, entity_index):
        self.calls.append("obs_route")
        return {"trigger_id": trigger_id, "obs_id": "cob_1", "route_id": "rte_1"}

    def fetch_kodex_events(self, trade_date, tickers):
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
    assert store.calls == []  # no observation/route, no explanation
    assert _outcomes(s3) == ["normal_variation"]


def test_triggered_day_persists_the_explanation():
    store = _FakeStore(trigger=_TRIGGER, prereqs=_PREREQS_OK)
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert store.calls == ["obs_route", "persist_explanation"]
    assert "explained" in _outcomes(s3)


def test_missing_prerequisites_fall_back_to_s3():
    store = _FakeStore(trigger=_TRIGGER, prereqs={"profile": False, "route": None, "bundle": None})
    s3 = _FakeS3()

    assert _run(store, s3) == 0
    assert "persist_explanation" not in store.calls  # no orphan RDS row
    keys = [p["Key"] for p in s3.puts]
    assert any("/runs/" not in k for k in keys)  # explanation S3 fallback
    assert any("/runs/" in k for k in keys)      # run archive
