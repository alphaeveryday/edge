"""The runnable fixture supports every factor without source data connections."""
import json

import pytest

from edge_analysis_v2.factor_store import FACTOR_KEYS, project_factor_metrics
from edge_analysis_v2.fixture_tools import FixtureTools
from edge_analysis_v2.fixture_tools.demo import build_demo_fixture


@pytest.mark.parametrize("factor", ["차트", "매크로", "밸류", "수급"])
def test_all_detail_factors_have_real_calculations(factor):
    tools = FixtureTools(build_demo_fixture())
    result = tools.call("get_instrument_factors", {"instrument_id": tools.fixture['context']['etf_code'], "factors": [FACTOR_KEYS[factor]]})
    cards = project_factor_metrics(result, tools.fixture['context']['etf_code'])[factor]
    assert cards
    assert all(r["observed_at"] for r in cards)
    json.dumps(result, allow_nan=False)


def test_initial_input_is_raw_bounded_and_time_limited():
    fixture = build_demo_fixture()
    fixture["macro"].append({"series": "usd_krw", "value": 9999, "unit": "KRW_per_USD", "observed_at": "2099-01-01T00:00:00+09:00", "available_at": "2099-01-01T00:00:00+09:00"})
    payload = FixtureTools(fixture).initial_input()
    assert sum(len(body["rows"]) for body in payload["prices"].values()) == 120
    assert sum(len(body["rows"]) for body in payload["flow"].values()) == 60
    assert len(payload["financials"]["rows"]) == 8
    assert 9999 not in [row[1] for row in payload["macro"]["usd_krw"]["rows"]]


def test_preopen_never_uses_current_day_provisional_price():
    fixture = build_demo_fixture(analysis_at="2026-09-21T08:30:00+09:00")
    assert fixture["price_snapshots"] == []
    assert FixtureTools(fixture).call("calculate_chart_indicators", {})["result"]["momentum"] is not None


def test_quiet_scenario_has_no_new_news_or_indicator_extreme():
    fixture = build_demo_fixture("quiet")
    result = FixtureTools(fixture).call("calculate_chart_indicators", {})["result"]
    assert 20 < result["momentum"] < 80
    assert all(r["published_at"] < "2026-09-15" for r in fixture["news"])
    assert max(abs(r["net_amount_krw"]) for r in fixture["flow"]) <= 15000


def test_utc_cutoff_means_same_korean_trading_day():
    fixture = build_demo_fixture(analysis_at="2026-09-22T08:30:00+09:00")
    expected = FixtureTools(fixture).call("get_instrument_factors", {"instrument_id": fixture["context"]["etf_code"], "factors": ["chart"]})["result"]["chart"]
    fixture["context"]["analysis_at"] = "2026-09-21T23:30:00+00:00"
    assert FixtureTools(fixture).call("get_instrument_factors", {"instrument_id": fixture["context"]["etf_code"], "factors": ["chart"]})["result"]["chart"] == expected
