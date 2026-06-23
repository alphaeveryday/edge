from __future__ import annotations

import numpy as np
import pandas as pd

from edge_event_model import config
from edge_event_model.features.dataset import spread_feature_columns
from edge_event_model.models.combine import SpreadModel
from edge_event_model.models.predictor import build_predictions


class _DummyNews:
    """Returns a fixed abnormal return + sigma for every row."""

    def __init__(self, abnormal=0.01, sigma=0.02):
        self.abnormal, self.sigma = abnormal, sigma

    def predict_abnormal(self, df, day_emb):
        n = len(df)
        return np.full(n, self.abnormal), np.full(n, self.sigma)


def _fit_spread() -> SpreadModel:
    rng = np.random.default_rng(0)
    n = 200
    train = pd.DataFrame({
        "normal_return": rng.normal(0, 0.01, n),
        "spread_lag_mean": np.abs(rng.normal(0.01, 0.002, n)),
        "spread_lag_std": np.abs(rng.normal(0.002, 0.001, n)),
        "news_count": rng.integers(0, 5, n),
        config.LABEL_SPREAD: np.abs(rng.normal(0.012, 0.004, n)),
    })
    return SpreadModel(spread_feature_cols=spread_feature_columns(), spread_col=config.LABEL_SPREAD).fit(train)


def test_predictor_reconstruction_math():
    sm = _fit_spread()
    df = pd.DataFrame({
        "ticker": ["A", "A"], "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "prev_close": [100.0, 200.0], "normal_return": [0.0, 0.005],
        "close": [101.0, 201.0], "high": [102.0, 202.0], "news_count": [1, 0],
        "spread_lag_mean": [0.01, 0.01], "spread_lag_std": [0.002, 0.002],
    })
    out = build_predictions(df, _DummyNews(abnormal=0.01, sigma=0.02), sm, {}, s_close=0.02)
    assert np.allclose(out["close_return_pred"], [0.01, 0.015])
    assert np.allclose(out["predicted_close"], [100.0 * np.exp(0.01), 200.0 * np.exp(0.015)])
    assert np.allclose(out["predicted_high"], out["predicted_close"] * np.exp(np.clip(out["spread_pred"], 0, None)))
    # close confidence = exp(-sigma/s_close) = exp(-1)
    assert np.allclose(out["close_confidence"], np.exp(-1.0), atol=1e-6)


def test_spread_model_bounds():
    sm = _fit_spread()
    rng = np.random.default_rng(1)
    df = pd.DataFrame({
        "normal_return": rng.normal(0, 0.01, 50), "spread_lag_mean": np.abs(rng.normal(0.01, 0.002, 50)),
        "spread_lag_std": np.abs(rng.normal(0.002, 0.001, 50)), "news_count": rng.integers(0, 5, 50),
    })
    out = sm.predict(df)
    assert (out["spread_pred"] >= 0).all()
    assert out["high_confidence"].between(0.0, 1.0).all()
