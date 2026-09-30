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
