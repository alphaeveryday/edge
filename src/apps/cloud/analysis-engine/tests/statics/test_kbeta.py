"""kbeta 칼만 코어 (ALPHA-803) — 테스트가 지키는 계약.

왜 이 테스트들인가 (Rule 9):
  - fit_qr 은 손튜닝이 아니라 **EM 최대우도**라는 게 설계 결정이다. 그 계약은
    "참 잡음 스케일을 자릿수 안에서 회복"으로만 검증할 수 있다 - 값 스냅샷은
    EM 구현이 바뀌면 무의미하고, 스케일 회복은 모형이 틀리면 반드시 깨진다.
  - β 필터의 존재 이유는 "하루 안에서 β 가 움직인다"를 좇는 것이다. 상수 β 수렴
    + 계단 점프 추적(랙 유한)이 그 존재 이유를 직접 검정한다.
  - 재실행 결정론(§13)은 파이프라인 계약이다 - 같은 입력이면 같은 출력.
  - ρ(잔차 공통상관) 판정은 사용자 결정으로 **제외**됐다(2026-08-08) - 부활하면
    깨지도록 부재를 명시 검사한다.
"""
from __future__ import annotations

import numpy as np
import pytest

from edge_analysis.statics import kbeta
from edge_analysis.statics.kbeta import (
    BARS_PER_DAY,
    beta_filter_params,
    fit_qr,
    kalman,
)

Q0, R0 = 2.5e-6, 5e-7          # 참 잡음: 봉당 수익 std ~0.16%, 관측잡음 std ~0.07%
VAR_M = 2.25e-6                # 시장 5분 수익 분산 (std 0.15%)


def _local_level_prices(rng, n=BARS_PER_DAY, q=Q0, r=R0):
    lp = np.log(50_000) + np.cumsum(rng.normal(0, np.sqrt(q), n)) \
        + rng.normal(0, np.sqrt(r), n)
    return np.exp(lp)


def _day(rng, beta, n):
    """(시장수익 x, 종목수익 y) — 참 β 경로 `beta` (스칼라 또는 길이 n 배열)."""
    x = rng.normal(0, np.sqrt(VAR_M), n)
    y = np.asarray(beta) * x + rng.normal(0, 0.002, n)
    return x, y


# ── fit_qr: EM 이 참 잡음 스케일을 회복한다 ──────────────────────────────

def test_fit_qr_recovers_noise_scale_within_order_of_magnitude():
    rng = np.random.default_rng(7)
    prices = _local_level_prices(rng)
    Q, R = fit_qr(prices)
    assert Q0 / 10 < Q < Q0 * 10, f"Q={Q} vs 참 {Q0}"
    assert R0 / 10 < R < R0 * 10, f"R={R} vs 참 {R0}"


def test_fit_qr_deterministic():
    prices = _local_level_prices(np.random.default_rng(11))
    assert fit_qr(prices) == fit_qr(prices)   # 같은 입력 → 같은 출력 (§13)


def test_fit_qr_rejects_short_input():
    with pytest.raises(ValueError):
        fit_qr(np.full(5, 50_000.0))


# ── beta_filter_params: 가격 (Q,R) → β (Q_β,R_β) 이식 규칙 ───────────────

def test_beta_filter_params_units_and_guards():
    q, r = beta_filter_params(Q0, R0, b0=1.0, var_m=VAR_M)
    assert r >= 2 * R0                        # 차분잡음 2R 은 반드시 들어간다
    assert q > 0
    # 하루 β 표류 std 가 병적이지 않다 (0 도 아니고 O(1) 초과도 아니다)
    drift = np.sqrt(BARS_PER_DAY * q)
    assert 0.01 < drift < 1.0, drift
    with pytest.raises(ValueError):
        beta_filter_params(Q0, R0, b0=1.0, var_m=0.0)


# ── β 필터: 상수 β 수렴 · 계단 점프 추적 ────────────────────────────────

def _em_params(rng, b0=1.0):
    """전일 합성 하루에서 실제 경로(fit_qr → beta_filter_params)로 Q_β·R_β."""
    Q, R = fit_qr(_local_level_prices(rng))
    return beta_filter_params(Q, R, b0=b0, var_m=VAR_M)


def test_constant_beta_converges():
    rng = np.random.default_rng(21)
    q, r = _em_params(rng)
    x, y = _day(rng, 1.3, BARS_PER_DAY)
    b, p = kalman(y, x, b0=1.0, p0=0.09, q=q, r=r)
    assert abs(b[-1] - 1.3) < 0.15, b[-1]     # 초기값 1.0 에서 참값으로 수렴
    assert p[-1] < 0.09                        # 사후분산이 사전분산보다 좁다
    assert (p > 0).all()


def test_step_jump_is_tracked_with_finite_lag():
    rng = np.random.default_rng(42)
    q, r = _em_params(rng)
    n = 400                                    # 랙이 유한함을 보이려면 점프 뒤 표본이 필요
    true = np.concatenate([np.full(100, 1.0), np.full(n - 100, 2.0)])
    x, y = _day(rng, true, n)
    b, _ = kalman(y, x, b0=1.0, p0=0.09, q=q, r=r)
    assert abs(b[99] - 1.0) < 0.2              # 점프 전엔 1 근방
    assert abs(b[-1] - 2.0) < 0.3, b[-1]      # 점프를 따라잡는다 (랙 유한)
    b0_, _ = kalman(y, x, b0=1.0, p0=0.09, q=0.0, r=r)
    assert abs(b0_[-1] - 2.0) > abs(b[-1] - 2.0)   # Q=0 이면 초기값에 갇힌다


def test_filter_deterministic():
    rng = np.random.default_rng(3)
    x, y = _day(rng, 1.0, BARS_PER_DAY)
    a = kalman(y, x, 1.0, 0.09, 1e-4, 4e-6)
    b = kalman(y, x, 1.0, 0.09, 1e-4, 4e-6)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


# ── ρ 제외 (사용자 결정 2026-08-08): 부활하면 깨진다 ─────────────────────

def test_rho_surface_removed():
    for name in ("_residual_rho", "_resid_rho", "RHO_IDIO_MAX"):
        assert not hasattr(kbeta, name), f"{name} 는 제거됐어야 한다 (ρ 판정 제외)"
    assert not hasattr(kbeta, "daily_beta"), "daily_beta(60일 회귀)는 fit_qr 로 대체됐다"


# ── 경로 분해 표면 유지 (2단계 배선의 입력) ─────────────────────────────

def test_path_layers_surface_kept():
    rng = np.random.default_rng(5)
    x, y = _day(rng, 1.0, BARS_PER_DAY)
    import pandas as pd
    ts = pd.date_range("2026-08-07 09:05", periods=len(y), freq="5min")
    res = {"verdict": "성립", "ts": list(ts), "beta": np.ones(len(y)),
           "x": x, "y": y}
    rows = kbeta.path_layers(res)
    assert len(rows) == len(y)
    hm, m, i, bt = rows[0]
    assert np.isclose(m + i, y[0])             # 층 합 = 수익 (로그 가법 회계)
    assert kbeta.path_layers3({"verdict": "성립"}) == []
