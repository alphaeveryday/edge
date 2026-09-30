"""ETF-specific cards require complete actual distributions and units history."""
import pytest
from edge_analysis_v2.storage.factors import FACTOR_KEYS

from edge_analysis_v2.tools.fixture_data import FixtureTools, make_fixture


def card(fixture, factor, key):
    return FixtureTools(fixture).call("get_instrument_factors", {"instrument_id": fixture["context"]["etf_code"], "factors": [FACTOR_KEYS[factor]]})["result"][FACTOR_KEYS[factor]][key]


def test_distribution_yield_uses_paid_cash_per_unit_and_current_price():
    fixture = make_fixture()
    price = fixture["price_snapshots"][-1]["price"]
    assert card(fixture, "밸류", "distribution_yield_12m_pct") == pytest.approx(100*300/price)


def test_future_payment_and_unknown_history_do_not_become_known_income():
    fixture = make_fixture()
    fixture["distributions"].append({"instrument_id": "091160", "paid_at": "2026-09-22T09:00:00+09:00", "amount_per_unit": 9000, "available_at": "2026-09-20T09:00:00+09:00"})
    assert card(fixture, "밸류", "distribution_yield_12m_pct") < 10
    fixture["distribution_history_start"] = "2026-09-01"
    assert card(fixture, "밸류", "distribution_yield_12m_pct") is None


def test_units_change_is_20_intervals_not_20_rows():
    fixture = make_fixture()
    assert card(fixture, "수급", "etf_units_change_20d_pct") == 2


def test_missing_units_day_cannot_shorten_comparison_period():
    fixture = make_fixture()
    fixture["etf_units"].pop(-2)
    assert card(fixture, "수급", "etf_units_change_20d_pct") is None
    assert card(fixture, "수급", "weighted_foreign_net_amount_20d") is not None
