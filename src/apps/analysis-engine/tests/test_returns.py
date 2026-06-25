from __future__ import annotations

import numpy as np
import pandas as pd

from edge_event_model import config
from edge_event_model.features.returns import ZScore, add_log_returns, add_spread


def _ohlc() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["A", "A", "A"],
            "trade_date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
            "open": [10.0, 11.0, 12.0],
            "high": [10.5, 11.6, 12.2],
            "low": [9.5, 10.5, 11.5],
            "close": [10.0, 11.0, 12.0],
            "volume": [100, 100, 100],
        }
    )


def test_log_returns_drop_first_and_value():
    out = add_log_returns(_ohlc())
    assert len(out) == 2  # first row per ticker dropped (no prev_close)
    assert np.isclose(out["prev_close"].iloc[0], 10.0)
    assert np.isclose(out[config.LABEL_CLOSE].iloc[0], np.log(11.0 / 10.0))


def test_spread_nonnegative_and_value():
    out = add_spread(add_log_returns(_ohlc()))
    assert (out[config.LABEL_SPREAD] >= 0).all()
    assert np.isclose(out[config.LABEL_SPREAD].iloc[0], np.log(11.6 / 11.0))


def test_zscore_roundtrip_and_sigma_scaling():
    vals = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    z = ZScore.fit(vals)
    standardized = z.transform(vals)
    assert np.isclose(standardized.mean(), 0.0, atol=1e-9)
    assert np.allclose(z.inverse_transform(standardized), vals)
    # a unit std in z-space maps back to the fitted std in raw units
    assert np.isclose(z.inverse_std(np.array([1.0]))[0], z.std)
