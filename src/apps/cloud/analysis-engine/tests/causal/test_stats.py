"""추론 원시함수 테스트 — 귀무를 어떻게 만들었는지가 곧 식별전략이다.

여기서 지키는 것 둘:
  1. 층화 순열은 **설계가 조건화한 것을 보존**한다. 대조군을 날짜 안에서 골랐으면
     귀무도 날짜 안에서 섞어야 한다 - 안 그러면 귀무 분산이 층 효과로 부푼다.
  2. 통계량이 순열에 반응하지 않으면 **검정이 아니다**. 실측으로 obs=0.045,
     null_sd=0.0, p=1/1001 인 "유의한" 결과가 통과한 적이 있다.
"""

import numpy as np
import pytest

from edge_analysis.causal import stats as S


def test_stratified_permutation_preserves_treated_count_per_block():
    x = np.array([1.0, 0, 0, 1.0, 0, 0])
    strata = np.array(["d1"] * 3 + ["d2"] * 3)

    worlds = S.permute(x, strata=strata, n=50, seed=1)

    assert all(w["x"][:3].sum() == 1 and w["x"][3:].sum() == 1 for w in worlds)


def test_free_permutation_does_not_preserve_blocks():
    """대조를 층 안에서 만들고 자유롭게 섞으면 다른 가설을 검정하게 된다."""
    x = np.array([1.0, 0, 0, 1.0, 0, 0])

    worlds = S.permute(x, n=200, seed=1)

    assert not all(w["x"][:3].sum() == 1 for w in worlds)


def test_placebo_rejects_null_that_does_not_respond_to_permutation():
    """stat 이 world 를 안 읽으면 모든 순열이 같은 값을 낸다 - 검정이 아니다."""
    x = np.array([1.0, 0, 1, 0, 1, 0])
    y = np.array([1.0, 2, 3, 4, 5, 6])

    frozen = S.placebo(lambda w: float(np.corrcoef(x, y)[0, 1]),
                       {"x": x}, S.permute(x, n=100), null_kind="label")

    assert frozen["testable"] is False
    assert "반응하지 않는다" in frozen["reason"]


def test_placebo_reports_p_from_the_null_it_built():
    x = np.array([1.0, 0, 1, 0, 1, 0])
    y = np.array([1.0, 2, 3, 4, 5, 6])

    r = S.placebo(lambda w: float(np.corrcoef(w["x"], y)[0, 1]),
                  {"x": x}, S.permute(x, n=200, seed=0), null_kind="label")

    assert r["testable"] and 0 < r["p"] <= 1
    assert r["null_kind"] == "label"
    assert r["n_null"] == 200


def test_placebo_needs_a_minimum_null_sample():
    x = np.array([1.0, 0, 1, 0])

    r = S.placebo(lambda w: float(w["x"].sum()), {"x": x},
                  S.permute(x, n=5), null_kind="label")

    assert r["testable"] is False


def test_residualize_sums_to_zero_and_must_not_be_used_for_cumulative_return():
    """창내 잔차 누적은 구조적으로 0 이다 - CAR 에 쓰면 안 된다는 함정을 고정한다."""
    rng = np.random.default_rng(0)
    y = rng.normal(size=100)
    m = rng.normal(size=100)

    assert S.residualize(y, [m]).sum() == pytest.approx(0.0, abs=1e-9)


def test_fit_then_predict_gives_out_of_sample_residuals_that_need_not_sum_to_zero():
    rng = np.random.default_rng(0)
    m_in, m_out = rng.normal(size=200), rng.normal(size=40)
    y_in = 1.5 * m_in + rng.normal(size=200)
    y_out = 1.5 * m_out + 0.4 + rng.normal(size=40)

    coef = S.fit(y_in, [m_in])
    resid = y_out - S.predict(coef, [m_out])

    assert abs(resid.sum()) > 1e-6
