"""Log returns, high spread, and z-score standardization (spec section 2/5).

* ``close_logret = ln(close_t / close_{t-1})`` per ticker (no cross-ticker leak).
* ``spread = ln(high_t / close_t) >= 0`` (intraday high >= close by definition).
* ``ZScore`` standardizes a regression target on train stats and inverts on output.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import config


def add_log_returns(ohlc: pd.DataFrame) -> pd.DataFrame:
    """Add ``prev_close`` and ``close_logret`` per ticker, dropping each ticker's first row."""
    out = ohlc.sort_values(["ticker", "trade_date"]).copy()
    grp = out.groupby("ticker", sort=False)
    out["prev_close"] = grp["close"].shift(1)
    out[config.LABEL_CLOSE] = np.log(out["close"] / out["prev_close"])
    return out.dropna(subset=["prev_close", config.LABEL_CLOSE]).reset_index(drop=True)


def add_spread(frame: pd.DataFrame) -> pd.DataFrame:
    """Add ``spread = ln(high/close)``, clipped to >= 0 for numerical safety."""
    out = frame.copy()
    spread = np.log(out["high"] / out["close"])
    out[config.LABEL_SPREAD] = spread.clip(lower=0.0)
    return out


@dataclass(slots=True)
class ZScore:
    """Fit mean/std on training values; standardize and invert (point + sigma)."""

    mean: float = 0.0
    std: float = 1.0

    @classmethod
    def fit(cls, values: np.ndarray | pd.Series) -> "ZScore":
        arr = np.asarray(values, dtype="float64")
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return cls(0.0, 1.0)
        std = float(arr.std(ddof=0))
        if not np.isfinite(std) or std < 1e-12:
            std = 1.0
        return cls(float(arr.mean()), std)

    def transform(self, values: np.ndarray | pd.Series) -> np.ndarray:
        return (np.asarray(values, dtype="float64") - self.mean) / self.std

    def inverse_transform(self, z: np.ndarray | pd.Series) -> np.ndarray:
        return np.asarray(z, dtype="float64") * self.std + self.mean

    def inverse_std(self, sigma_z: np.ndarray | pd.Series) -> np.ndarray:
        """Map a std expressed in z-space back to raw units (mean-free scaling)."""
        return np.asarray(sigma_z, dtype="float64") * self.std
