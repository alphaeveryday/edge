"""분봉 4레인이 공유하는 실패 허용 계약 회귀."""

import pytest

from data_pipeline.minute.price_collect import status_of
from data_pipeline.minute.states import (
    WINDOW_INCOMPLETE,
    WINDOW_INVALID,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)
from data_pipeline.minute.worker import _failed_units


def units(count, prefix):
    return [f"{prefix}{i:03d}" for i in range(count)]


@pytest.mark.parametrize("axis", ["missing", "invalid"])
def test_one_failure_in_400_units_is_valid_for_each_failure_axis(axis):
    failed = units(1, axis)
    args = {"received": units(399, "ok"), "no_trade": [], "missing": [], "invalid": []}
    args[axis] = failed
    assert status_of(**args) == WINDOW_VALID


@pytest.mark.parametrize(
    ("missing_count", "invalid_count", "expected_status"),
    [(4, 0, WINDOW_INCOMPLETE), (0, 4, WINDOW_INVALID), (2, 2, WINDOW_INVALID)],
)
def test_absolute_failure_limit_is_three(missing_count, invalid_count, expected_status):
    assert status_of(
        units(396, "ok"), [], units(missing_count, "m"), units(invalid_count, "i")
    ) == expected_status


def test_missing_and_invalid_are_summed_for_ratio_limit():
    assert status_of(units(197, "ok"), [], ["m"], ["i"]) == WINDOW_INVALID


def test_combined_failures_at_exactly_one_percent_are_valid():
    assert status_of(units(198, "ok"), [], ["m"], ["i"]) == WINDOW_VALID


def test_one_failure_in_45_unit_sector_lane_stays_incomplete():
    assert status_of(units(44, "ok"), [], ["m"], []) == WINDOW_INCOMPLETE


def test_valid_empty_uses_the_same_contract():
    assert status_of([], units(399, "flat"), ["m"], []) == WINDOW_VALID_EMPTY


def test_ledger_problem_units_include_both_failure_axes():
    manifest = {"missing": ["retryable"], "invalid": ["malformed"]}
    assert _failed_units(manifest) == ["retryable", "malformed"]
