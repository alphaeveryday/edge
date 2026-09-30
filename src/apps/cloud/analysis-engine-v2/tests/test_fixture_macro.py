"""Macro differences retain economic units and publication cutoffs."""
import pytest

from edge_analysis_v2.tools.fixture_data import FixtureTools


def source():
    return {"context": {"etf_code": "ETF", "analysis_at": "2026-09-21T10:00:00+09:00", "flow_as_of_date": "2026-09-18"}, "macro": [
        {"series": "kr_10y_yield", "value": value, "unit": "percent", "observed_at": f"2026-09-{day}T09:00:00+09:00", "available_at": f"2026-09-{day}T09:01:00+09:00"} for day, value in ((18, 3), (21, 3.3))]}


def compare(fixture):
    return FixtureTools(fixture).call("compare_macro_observations", {"series": "kr_10y_yield", "previous_at": "2026-09-18T09:00:00+09:00", "current_at": "2026-09-21T09:00:00+09:00", "operation": "difference"})["result"]


def test_yield_difference_is_percentage_points():
    result = compare(source())
    assert result["change"] == 0.3
    assert result["change_unit"] == "percentage_points"


def test_release_after_cutoff_cannot_substitute_earlier_point():
    fixture = source()
    fixture["macro"][-1]["available_at"] = "2026-09-21T11:00:00+09:00"
    with pytest.raises(ValueError, match="unavailable"):
        compare(fixture)


def test_units_are_not_inferred_from_number():
    fixture = source()
    fixture["macro"][0]["unit"] = "basis_points"
    with pytest.raises(ValueError, match="unit"):
        compare(fixture)
