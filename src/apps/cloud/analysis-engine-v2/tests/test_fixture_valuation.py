"""Valuation uses released earnings and complete holdings, never subset averages."""
import pytest

from edge_analysis_v2.tools.fixture_data import FixtureTools


def valuation_fixture():
    return {"context": {"etf_code": "ETF", "analysis_at": "2026-09-21T10:00:00+09:00", "flow_as_of_date": "2026-09-18"},
            "holdings": [{"instrument_id": name, "weight": w, "as_of_date": "2026-09-18", "available_at": "2026-09-18T18:00:00+09:00"} for name, w in (("A", .6), ("B", .4))],
            "prices": [{"instrument_id": name, "date": "2026-09-18", "close": price, "available_at": "2026-09-18T18:00:00+09:00"} for name, price in (("A", 100), ("B", 200))],
            "financials": [{"instrument_id": name, "period": period, "eps": 2.5, "bps": 50, "available_at": "2026-08-15T18:00:00+09:00"} for name in ("A", "B") for period in ("2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2")]}


def test_weighted_ratios_are_not_largest_holding_proxy():
    result = FixtureTools(valuation_fixture()).call("calculate_weighted_valuation", {})["result"]
    assert result["weighted_per"] == 14
    assert result["weighted_pbr"] == 2.8
    assert len(result["constituents"]) == 2


@pytest.mark.parametrize("mode", ["missing", "future", "loss", "zero_bps", "gap"])
def test_invalid_financial_domain_never_becomes_neutral(mode):
    fixture = valuation_fixture()
    if mode == "missing":
        fixture["financials"].pop()
    elif mode == "future":
        fixture["financials"][-1]["available_at"] = "2026-09-22T18:00:00+09:00"
    elif mode == "loss":
        fixture["financials"][-1]["eps"] = -100
    elif mode == "zero_bps":
        fixture["financials"][-1]["bps"] = 0
    else:
        fixture["financials"][-1]["period"] = "2026-Q3"
    with pytest.raises(ValueError):
        FixtureTools(fixture).call("calculate_weighted_valuation", {})


def test_derived_q4_is_declared_and_withheld_from_the_bare_card():
    # Policy (2026-09-30): FY−9M Q4 EPS is an approximation. Every consumer path that shows a PER must say so:
    # calculate/weighted (approximate·derived_periods) and the factor screen (eps_approximate·weighted_per_approximate).
    fixture = valuation_fixture()
    for row in fixture["financials"]:
        if row["period"] == "2025-Q4":
            row["eps_derivation"] = "FY_MINUS_9M"
    tools = FixtureTools(fixture)
    single = tools.call("calculate_valuation", {"instrument_id": "A"})["result"]
    assert single["approximate"] is True and single["derived_periods"] == ["2025-Q4"]
    weighted = tools.call("calculate_weighted_valuation", {})["result"]
    assert weighted["approximate"] is True and weighted["derived_constituents"] == ["A", "B"]
    assert weighted["coverage"] == {"constituents": 2, "weight": 1}
    screen = tools.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]
    assert screen["weighted_per"] is not None and screen["weighted_per_approximate"] is True
    company = tools.call("get_instrument_factors", {"instrument_id": "A", "factors": ["valuation"]})["result"]["valuation"]
    assert company["eps_approximate"] is True and company["eps_derived_periods"] == ["2025-Q4"]
    plain = FixtureTools(valuation_fixture())
    assert plain.call("calculate_valuation", {"instrument_id": "A"})["result"]["approximate"] is False
    assert plain.call("get_instrument_factors", {"instrument_id": "ETF", "factors": ["valuation"]})["result"]["valuation"]["weighted_per_approximate"] is False


def test_ttm_never_double_counts_or_skips_a_quarter():
    # A hole in a released quarter (BPS blocked) must fail, not slide to the four older quarters; and a
    # re-released quarter is one period, not two.
    fixture = valuation_fixture()
    fixture["financials"].append({"instrument_id": "A", "period": "2025-Q2", "eps": 2.5, "bps": 50, "available_at": "2025-08-15T18:00:00+09:00"})
    for row in fixture["financials"]:
        if row["instrument_id"] == "A" and row["period"] == "2026-Q2":
            row["bps"] = None
    with pytest.raises(ValueError, match="without EPS or BPS"):
        FixtureTools(fixture).call("calculate_valuation", {"instrument_id": "A"})
    fixture = valuation_fixture()
    fixture["financials"].append({"instrument_id": "A", "period": "2026-Q2", "eps": 9, "bps": 50, "available_at": "2026-09-01T18:00:00+09:00"})
    result = FixtureTools(fixture).call("calculate_valuation", {"instrument_id": "A"})["result"]
    assert result["periods"] == ["2025-Q3", "2025-Q4", "2026-Q1", "2026-Q2"] and result["ttm_eps"] == 16.5
