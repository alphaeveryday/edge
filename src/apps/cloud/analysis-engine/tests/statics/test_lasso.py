"""라쏘 조절자 검정 — **선택이 순열 안에 있는가**가 이 층의 유일한 쟁점이다.

순진하게 적합 → 비영계수 → p 를 내면 그건 선택 후 추론이고 위양성이 명목의 몇 배로
뜬다. 그래서 이 테스트는 세 가지를 본다: 절편이 안 수축되는가(게이트 보호), 위양성이
잡히는가(귀무에서 아무것도 안 뽑히는가), 강상관 쌍이 탈락하는가(안정성 선택).
"""

from __future__ import annotations

import numpy as np

from edge_analysis.statics.lasso import (LAM_GRID, PI_MIN, lasso, moderate,
                                        post_ols, stability)


def _panel(n=300, seed=0, effect=0.05, j=4):
    rng = np.random.default_rng(seed)
    X = rng.random((n, j))
    dates = np.array([f"2026-01-{1 + i % 10:02d}" for i in range(n)])
    y = 0.01 + effect * X[:, 0] + rng.normal(0, 0.002, n)
    return y, X, dates


def test_intercept_is_never_penalized():
    """ATT 가 수축되면 게이트가 무너진다 - 절편은 벌점 밖이다."""
    y, X, _ = _panel()
    for lam in (0.0, 0.001, 1.0, 100.0):
        a, d = lasso(y, X, lam)
        assert abs(a) > 1e-6, f"λ={lam} 에서 절편이 0 으로 수축됐다"
    # λ 가 크면 기울기는 전부 죽고 절편은 표본평균으로 남는다
    a_big, d_big = lasso(y, X, 100.0)
    assert (d_big == 0).all() and abs(a_big - y.mean()) < 1e-9


def test_true_moderator_beats_noise_and_noise_is_not_selected():
    """진짜 조절자만 뽑힌다 - 잡음이 뽑히면 산문이 없는 조건을 말한다."""
    y, X, dates = _panel()
    r = moderate(y, X, dates, ["진짜", "잡음1", "잡음2", "잡음3"], perms=60)
    assert r["verdict"] == "계산됨"
    assert r["p_max"] < 0.05, r["p_max"]
    assert set(r["selected"]) == {"진짜"}, r["selected"]
    assert r["pi"]["진짜"] >= PI_MIN


def test_no_moderator_means_nothing_selected():
    """조절자가 없으면 아무것도 안 뽑힌다 - **위양성 검사**.

    라쏘는 예측 도구다. 벌점만 걸고 비영계수를 세면 잡음에서도 뭔가 나온다. maxT
    순열이 그것을 막는지가 이 층이 통계인지 아닌지를 가른다.
    """
    rng = np.random.default_rng(1)
    n = 300
    X = rng.random((n, 4))
    dates = np.array([f"2026-01-{1 + i % 10:02d}" for i in range(n)])
    y = 0.01 + rng.normal(0, 0.002, n)
    r = moderate(y, X, dates, ["a", "b", "c", "d"], perms=60)
    assert not r["selected"], r["selected"]
    assert r["p_max"] >= 0.05, r["p_max"]


def test_near_duplicate_columns_are_pruned_before_fitting():
    """준중복은 **적합 전에** 걷는다 - 안정성 선택으로는 안 잡힌다(실측).

    두 열이 거의 같으면 좌표하강은 하나를 고르는 게 아니라 계수를 쪼개 나눈다: 둘 다
    Π=1.0 으로 뽑히고 크기는 절반씩 실린다. 그러면 산문이 같은 조건을 두 이름으로
    말하면서 각각을 과소보고한다. λ 를 어떻게 잡아도(격자 전량 + 0.02·0.05 실측)
    '번갈아 뽑힘' 은 나오지 않았다 - 둘 다 들어가거나 둘 다 죽는다.
    """
    from edge_analysis.statics.lasso import prune_collinear

    rng = np.random.default_rng(2)
    n = 400
    x = rng.random(n)
    X = np.column_stack([x, x + rng.normal(0, 1e-4, n), rng.random(n)])
    dates = np.array([f"2026-01-{1 + i % 10:02d}" for i in range(n)])
    y = 0.01 + 0.05 * x + rng.normal(0, 0.002, n)

    # 걷지 않으면 둘 다 뽑히고 계수가 쪼개진다 - 이것이 문제의 실물이다
    pi_raw = stability(y, X, dates, 0.001, b=40)
    assert pi_raw[0] >= PI_MIN and pi_raw[1] >= PI_MIN, pi_raw
    _, d_raw = lasso(y, X, 0.001)
    assert abs(d_raw[0]) < 0.04 and abs(d_raw[1]) < 0.04, "쪼개져 있다"

    keep, dropped = prune_collinear(X, ["fr_lvl", "fr_chg", "무관"])
    assert list(keep) == [True, False, True], keep
    assert dropped == {"fr_chg": dropped["fr_chg"]} and "fr_lvl" in dropped["fr_chg"]

    # 걷고 나면 하나만 남고 크기가 온전하다
    r = moderate(y, X, dates, ["fr_lvl", "fr_chg", "무관"], perms=40)
    assert set(r["selected"]) == {"fr_lvl"}, r["selected"]
    assert abs(r["selected"]["fr_lvl"]) > 0.04, r["selected"]
    assert "fr_chg" in r["dropped_collinear"], r["dropped_collinear"]


def test_post_lasso_undoes_the_shrinkage():
    """크기는 post-LASSO 다 - 라쏘 계수를 인용하면 효과를 과소보고한다."""
    y, X, _ = _panel()
    _, d = lasso(y, X, 0.005)
    keep = np.array([True, False, False, False])
    _, d2 = post_ols(y, X, keep)
    assert abs(d2[0]) > abs(d[0]), (d[0], d2[0])
    # 선택 집합이 비면 절편만 남는다
    a0, d0 = post_ols(y, X, np.zeros(4, dtype=bool))
    assert (d0 == 0).all() and abs(a0 - y.mean()) < 1e-12


def test_lambda_grid_is_reported_in_full():
    """λ 하나만 쓰면 스펙 쇼핑이 안 보인다 - 격자 전량을 싣는다."""
    y, X, dates = _panel()
    r = moderate(y, X, dates, ["진짜", "b", "c", "d"], perms=40)
    assert set(r["lam_sensitivity"]) == {f"{lm:g}" for lm in LAM_GRID}
    assert r["lam"] in LAM_GRID


def test_permutation_is_deterministic_and_stratified_by_date():
    """같은 SEED 면 같은 p - 재실행에서 판정이 뒤집히면 그건 검정이 아니다."""
    y, X, dates = _panel()
    a = moderate(y, X, dates, ["진짜", "b", "c", "d"], perms=40)
    b = moderate(y, X, dates, ["진짜", "b", "c", "d"], perms=40)
    assert a["p_max"] == b["p_max"] and a["selected"] == b["selected"]


def test_thin_sample_is_undecided_not_zero():
    """표본이 얇으면 판정불가다 - 0 이라고 말하지 않는다."""
    r = moderate(np.array([0.1, 0.2]), np.zeros((2, 3)),
                 np.array(["d", "d"]), ["a", "b", "c"], perms=10)
    assert r["verdict"] == "판정불가" and "n=2" in r["reason"]
