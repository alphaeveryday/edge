"""적합도 테스트 — 국소가 어디인지 말하고, 전역이 그것을 합성한다.

Shipley 의 d-분리 검정을 쓴다: C = -2 Σ ln p_i ~ chi2(2k). SEM 의 ML 카이제곱을 쓰지
않는 이유는 선형·정규를 다 받아야 하는데 우리 데이터는 둘 다 아니라서다. 그리고 전역
카이제곱은 "어디가 틀렸나"를 안 알려준다 - 진단은 국소가 한다.
"""

import numpy as np
import pytest

from edge_analysis.causal import fit as F


def _world(n: int = 400, seed: int = 0):
    """참 모형: S -> X -> Y, S -> Y. W 는 아무것과도 연결되지 않는다."""
    rng = np.random.default_rng(seed)
    s = rng.normal(size=n)
    x = 0.8 * s + rng.normal(size=n)
    y = 0.6 * x + 0.5 * s + rng.normal(size=n)
    cols = {"S@t0": s, "X@t1": x, "Y@t2": y, "W@t0": rng.normal(size=n)}
    return {k: {"kind": "OBSERVABLE"} for k in cols}, cols


TRUE = [{"from": "S@t0", "to": "X@t1"}, {"from": "S@t0", "to": "Y@t2"},
        {"from": "X@t1", "to": "Y@t2"}]
MISSING = [{"from": "S@t0", "to": "X@t1"}, {"from": "X@t1", "to": "Y@t2"}]


def test_true_model_is_not_rejected():
    nodes, cols = _world()

    g = F.global_fit(F.local_fit(nodes, TRUE, cols))

    assert g["testable"] and g["p"] > 0.05


def test_missing_edge_is_rejected_and_localised():
    """전역이 기각하면 국소 1위가 **빠진 그 간선**을 지목해야 한다 - 이게 수정지수다."""
    nodes, cols = _world()

    local = F.local_fit(nodes, MISSING, cols)
    g = F.global_fit(local)

    assert g["p"] < 0.01
    assert {local[0]["X"], local[0]["Y"]} == {"S@t0", "Y@t2"}
    assert local[0]["p"] < 0.05


def test_saturated_graph_has_no_testable_content():
    """모수를 다 추정할 수 있다는 건 좋은 신호가 아니다 - 검정할 것이 없다는 뜻이다."""
    nodes, cols = _world()
    three = {k: nodes[k] for k in ("S@t0", "X@t1", "Y@t2")}

    local = F.local_fit(three, TRUE, cols)

    assert local == []
    assert F.global_fit(local)["testable"] is False


def test_bidirected_edge_removes_the_pair_from_testable_implications():
    """양방향을 선언하면 그 쌍은 검정 대상에서 빠진다 - 대가가 df 로 치러진다."""
    nodes, cols = _world()
    bi = [*MISSING, {"from": "S@t0", "to": "Y@t2", "kind": "bidirected"}]

    local = F.local_fit(nodes, bi, cols)

    assert all({r["X"], r["Y"]} != {"S@t0", "Y@t2"} for r in local)
    assert F.global_fit(local)["df"] < F.global_fit(F.local_fit(nodes, MISSING, cols))["df"]


def test_latent_node_is_reported_untestable_not_passing():
    """열이 없으면 '통과'가 아니라 '미검정'이다 - 잠재가 있으면 CI 만으로 불완비다.

    잠재가 **조건집합**에 든 함의도 검정 불가다 - 통제해야 할 것을 관측 못 하니까.
    그래서 미검정은 X·Y 에 잠재가 든 쌍보다 많다.
    """
    nodes, cols = _world()
    nodes["L@t0"] = {"kind": "MECHANISM"}
    edges = [*MISSING, {"from": "L@t0", "to": "Y@t2"}, {"from": "L@t0", "to": "X@t1"}]

    local = F.local_fit(nodes, edges, cols)
    untestable = [r for r in local if not r["testable"]]

    assert all("잠재" in r["reason"] for r in untestable)
    assert any("L@t0" in r["Z"] for r in untestable), "조건집합의 잠재도 잡혀야 한다"
    assert F.global_fit(local)["n_untestable"] == len(untestable)


def test_ci_test_detects_conditional_independence():
    _, cols = _world()

    dep = F.ci_test(cols, "S@t0", "Y@t2", ())
    indep = F.ci_test(cols, "W@t0", "Y@t2", ())

    assert dep["p"] < 0.01
    assert indep["p"] > 0.05


@pytest.mark.parametrize("df", [2, 4, 10])
def test_chi2_survival_is_monotone_and_bounded(df):
    assert F.chi2_sf(0.0, df) == 1.0
    assert F.chi2_sf(1e6, df) < 1e-9
    assert F.chi2_sf(1.0, df) > F.chi2_sf(50.0, df)
