"""Tests for ETF-move decomposition and routing.

This is the engine's core arithmetic; the proxy-return formula must match the
pipeline's trigger writer so a fired trigger and its explanation agree.
"""

import pytest

from edge_analysis.domain.decomposition import compute_decomposition, decide_route
from edge_analysis.domain.models import Holding


def test_proxy_return_is_weight_averaged_over_priced_subset():
    holdings = [Holding("A", "A", 0.6), Holding("B", "B", 0.4)]
    returns = {"A": 0.10, "B": -0.05}

    d = compute_decomposition(holdings, returns)

    assert d.proxy_ret == pytest.approx((0.6 * 0.10 + 0.4 * -0.05) / (0.6 + 0.4))
    assert d.coverage == pytest.approx(1.0)


def test_unpriced_constituents_reduce_coverage_but_not_count():
    holdings = [Holding("A", "A", 0.6), Holding("B", "B", 0.4)]
    returns = {"A": 0.10}  # B has no price for the day.

    d = compute_decomposition(holdings, returns)

    assert d.proxy_ret == pytest.approx(0.10)  # normalized over A's weight only
    assert d.covered_weight == pytest.approx(0.6)
    assert d.total_priced == 1
    assert d.n_constituents == 2


def test_members_are_ranked_by_absolute_contribution():
    holdings = [Holding("A", "A", 0.2), Holding("B", "B", 0.8)]
    returns = {"A": 0.10, "B": -0.05}  # |0.02| < |0.04| -> B ranks first

    d = compute_decomposition(holdings, returns)

    assert [m.ticker for m in d.members] == ["B", "A"]
    assert [m.rank for m in d.members] == [1, 2]


def test_no_priced_constituent_yields_no_proxy():
    d = compute_decomposition([Holding("A", "A", 1.0)], {})

    assert d.proxy_ret is None
    assert d.coverage == 0.0
    assert d.total_priced == 0


def test_route_is_concentrated_at_the_inclusive_threshold():
    # top1 == 0.5 counts as concentrated (>= threshold).
    holdings = [Holding("A", "A", 0.5), Holding("B", "B", 0.5)]
    returns = {"A": 0.10, "B": 0.10}

    d = compute_decomposition(holdings, returns)

    assert d.top1 == pytest.approx(0.5)
    assert decide_route(d) == ("CONCENTRATED", True)


def test_route_is_common_factor_when_dispersed():
    holdings = [Holding(t, t, 1 / 3) for t in ("A", "B", "C")]
    returns = {"A": 0.05, "B": 0.05, "C": 0.05}  # top1 ~= 1/3 < 0.5

    d = compute_decomposition(holdings, returns)

    assert decide_route(d) == ("COMMON_FACTOR", True)
