from __future__ import annotations

import numpy as np
import pandas as pd

from edge_event_model import config
from edge_event_model.features.factor_arm import (
    clean_window_positions,
    compute_factor_arm,
    ols_fit,
)


def test_ols_recovers_known_coefficients():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 5))
    beta_true = np.array([0.001, 0.5, -0.3, 0.2, 0.1])
    y = 0.0 + x @ beta_true
    beta, r2 = ols_fit(x, y)
    assert np.allclose(beta[1:], beta_true, atol=1e-8)
    assert r2 > 0.999


def test_clean_window_excludes_events_and_future():
    is_event = np.array([False, True, False, False, False])
    assert clean_window_positions(is_event, 4, 10) == [0, 2, 3]
    # strictly before t and never the event index
    assert clean_window_positions(is_event, 2, 10) == [0]


def test_abnormal_return_isolates_injected_spike():
    n = 120
    dates = pd.bdate_range("2023-01-02", periods=n)
    rng = np.random.default_rng(1)
    factors = rng.normal(scale=0.005, size=(n, 5))
    rf = np.full(n, 1e-4)
    betas = np.array([1.2, -0.5, 0.3, 0.4, -0.2])
    abnormal_true = np.zeros(n)
    abnormal_true[100] = 0.10
    excess = factors @ betas + abnormal_true
    close_logret = excess + rf

    rets = pd.DataFrame({"ticker": "A", "trade_date": dates, config.LABEL_CLOSE: close_logret})
    ff5 = pd.DataFrame(
        {
            "trade_date": dates,
            "mkt_rf": factors[:, 0],
            "smb": factors[:, 1],
            "hml": factors[:, 2],
            "rmw": factors[:, 3],
            "cma": factors[:, 4],
            "rf": rf,
        }
    )
    out = compute_factor_arm(rets, ff5, window=60, min_obs=30, abn_threshold=0.05)

    spike = out.loc[out["trade_date"] == dates[100]].iloc[0]
    assert abs(spike[config.LABEL_ABNORMAL] - 0.10) < 1e-3
    assert bool(spike["is_event_candidate"]) is True
    # a clean day: noiseless linear -> OLS recovers betas -> abnormal ~ 0
    clean = out.loc[out["trade_date"] == dates[80]].iloc[0]
    assert abs(clean[config.LABEL_ABNORMAL]) < 1e-3
    # recovered betas match truth on a clean day
    assert abs(clean["beta_mkt_rf"] - betas[0]) < 1e-3
    # warm-up rows (< min_obs clean history) are omitted
    assert out["trade_date"].min() >= dates[30]
