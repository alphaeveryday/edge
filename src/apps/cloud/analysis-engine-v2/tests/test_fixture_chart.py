"""Chart calculations use finalized state once per intraday observation."""
from datetime import date, timedelta

import pytest

from edge_analysis_v2.fixture_tools import FixtureTools


def chart_fixture():
    days = []
    day = date(2025, 9, 1)
    while day < date(2026, 9, 21):
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    prices = [{"instrument_id": "ETF", "date": day, "high": 101 + i, "low": 99 + i, "close": 100 + i, "volume": 100, "turnover": 10000, "available_at": day + "T18:00:00+09:00"} for i, day in enumerate(days)]
    last = prices[-1]["close"]
    return {"context": {"etf_code": "ETF", "analysis_at": "2026-09-21T10:00:00+09:00", "flow_as_of_date": days[-1]}, "trading_dates": days,
            "holdings": [{"instrument_id": "A", "weight": 1, "as_of_date": days[0], "available_at": days[0] + "T08:00:00+09:00"}], "prices": prices,
            "price_snapshots": [{"instrument_id": "ETF", "price": last + 1, "high": last + 2, "low": last, "observed_at": f"2026-09-21T09:5{i}:00+09:00", "available_at": f"2026-09-21T09:5{i}:00+09:00"} for i in range(5)]}


def test_monotone_price_and_distinct_intraday_snapshots():
    tools = FixtureTools(chart_fixture())
    result = tools.call("calculate_chart_indicators", {})["result"]
    assert result["momentum"] == 100
    transition = tools.call("evaluate_indicator_transition", {"indicator": "momentum"})["result"]
    assert transition["held_zone"] == "upper"
    assert transition["transitions"] == []


def test_each_snapshot_reuses_prior_finalized_rma():
    fixture = chart_fixture()
    fixture["price_snapshots"][0]["price"] -= 10
    fixture["price_snapshots"][0]["low"] -= 10
    assert FixtureTools(fixture).call("calculate_chart_indicators", {})["result"]["momentum"] == 100


def test_cards_have_20_strict_closing_highs_and_true_turnover():
    from edge_analysis_v2.fixture_tools.chart import metrics
    cards = {r["key"]: r["value"] for r in metrics(chart_fixture())}
    assert cards["new_closing_high_count_20d"] == 20
    assert cards["turnover_ratio_previous_day"] == 1
    assert cards["ma60_direction"] == "상승"


def test_missing_expected_daily_price_is_not_shortened_window():
    fixture = chart_fixture()
    fixture["prices"].pop(-3)
    with pytest.raises(ValueError, match="missing"):
        FixtureTools(fixture).call("calculate_chart_indicators", {})


@pytest.mark.parametrize("ratio, expected", [(1.01, 1), (1, 0), (.99, -1)])
def test_high_distance_preserves_breakthrough_sign_without_clipping(ratio, expected):
    fixture = chart_fixture()
    high = max(row["close"] for row in fixture["prices"])
    current = fixture["price_snapshots"][-1]
    current.update(price=high*ratio, high=high*ratio+1, low=high*ratio-1)
    result = FixtureTools(fixture).call("get_factor_metrics", {"type": "차트"})["result"]
    cards = {row["key"]: row for row in result["metrics"]}
    assert cards["distance_from_52w_closing_high_pct"]["value"] == pytest.approx(expected)
    assert cards["distance_from_52w_closing_high_pct"]["observed_at"] == current["observed_at"]
    assert cards["new_closing_high_count_20d"]["observed_at"] == fixture["prices"][-1]["available_at"]
    assert cards["turnover_ratio_previous_day"]["observed_at"] == fixture["prices"][-1]["available_at"]
