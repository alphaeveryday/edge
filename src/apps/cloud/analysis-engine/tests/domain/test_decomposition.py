"""ETF 등락 분해·라우팅 테스트.

엔진의 핵심 산술이다. proxy 등락 산식은 파이프라인 트리거 writer 와 같아야
발화한 트리거와 그 설명의 분해가 일치한다.
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
    returns = {"A": 0.10}  # B 는 그날 가격이 없다.

    d = compute_decomposition(holdings, returns)

    assert d.proxy_ret == pytest.approx(0.10)  # A 비중으로만 정규화
    assert d.covered_weight == pytest.approx(0.6)
    assert d.total_priced == 1
    assert d.n_constituents == 2


def test_members_are_ranked_by_absolute_contribution():
    holdings = [Holding("A", "A", 0.2), Holding("B", "B", 0.8)]
    returns = {"A": 0.10, "B": -0.05}  # |0.02| < |0.04| → B 가 1위

    d = compute_decomposition(holdings, returns)

    assert [m.ticker for m in d.members] == ["B", "A"]
    assert [m.rank for m in d.members] == [1, 2]


def test_no_priced_constituent_yields_no_proxy():
    d = compute_decomposition([Holding("A", "A", 1.0)], {})

    assert d.proxy_ret is None
    assert d.coverage == 0.0
    assert d.total_priced == 0


def test_route_is_concentrated_at_the_inclusive_threshold():
    # top1 == 0.5 은 집중으로 친다(임계값 이상 포함).
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
