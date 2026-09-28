"""Weighted flow must retain full coverage and latest-day streak semantics."""
import pytest

from edge_analysis_v2.fixture_tools import FixtureTools


def flow_fixture():
    dates = [f"2026-09-{day}" for day in (14, 15, 16, 17, 18)]
    return {"context": {"etf_code": "ETF", "analysis_at": "2026-09-21T10:00:00+09:00", "flow_as_of_date": dates[-1]}, "trading_dates": dates,
            "holdings": [{"instrument_id": name, "weight": weight, "as_of_date": dates[0], "available_at": "2026-09-14T08:00:00+09:00"} for name, weight in (("A", .6), ("B", .4))],
            "flow": [{"instrument_id": name, "date": day, "investor": "foreign", "net_amount_krw": amount, "available_at": day + "T18:00:00+09:00", "finalized": True} for name, amounts in (("A", [10, 10, -10, 10, 10]), ("B", [-5, -5, -5, -5, -5])) for day, amount in zip(dates, amounts)]}


def run(fixture, operation, **extra):
    return FixtureTools(fixture).call("calculate_weighted_flow", {"investor": "foreign", "lookback_days": 5, "operation": operation, "direction": "none" if operation == "sum" else "net_buy", **extra})["result"]


def test_daily_weighting_then_frequency_not_average_of_counts():
    data = flow_fixture()
    assert run(data, "sum")["amount_krw"] == 8
    assert run(data, "frequency")["matched_days"] == 4
    assert run(data, "streak")["streak_days"] == 2
    assert run(data, "streak")["exact"] is True


def test_missing_constituent_is_not_zero_or_renormalized():
    data = flow_fixture()
    data["flow"].pop()
    with pytest.raises(ValueError, match="missing"):
        run(data, "sum")


def test_streak_is_lower_bound_when_boundary_is_unobserved():
    data = flow_fixture()
    for row in data["flow"]:
        row["net_amount_krw"] = 1
    result = run(data, "streak")
    assert result["streak_days"] == 5 and result["exact"] is False


def test_new_holdings_cannot_be_applied_to_earlier_day():
    data = flow_fixture()
    for row in data["holdings"]:
        row["available_at"] = "2026-09-18T08:00:00+09:00"
    with pytest.raises(ValueError, match="weight"):
        run(data, "sum")


@pytest.mark.parametrize("change", ["future", "unfinalized", "duplicate"])
def test_invalid_window_cannot_produce_computable_claim(change):
    data = flow_fixture()
    if change == "future":
        data["flow"][0]["available_at"] = "2026-09-22T18:00:00+09:00"
    elif change == "unfinalized":
        data["flow"][0]["finalized"] = False
    else:
        data["flow"].append(dict(data["flow"][0]))
    with pytest.raises(ValueError):
        run(data, "sum")
