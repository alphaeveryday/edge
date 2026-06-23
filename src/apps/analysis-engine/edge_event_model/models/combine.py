"""High-spread regression + confidence scaling.

The abnormal return now comes DIRECTLY from the attention NN (no news-score ->
abnormal linear regression). This module only models the high arm
``high = close * exp(spread)`` via a z-scored linear regression, and converts
prediction standard errors into a high-confidence in [0, 1].
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..features.returns import ZScore


def _ols_with_cov(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    design = np.column_stack([np.ones(len(x)), x])
    xtx_inv = np.linalg.pinv(design.T @ design)
    beta = xtx_inv @ design.T @ y
    resid = y - design @ beta
    dof = max(len(x) - design.shape[1], 1)
    return beta, xtx_inv, float(np.sqrt((resid @ resid) / dof))


def _predict_with_se(beta, xtx_inv, resid_std, x) -> tuple[np.ndarray, np.ndarray]:
    design = np.column_stack([np.ones(len(x)), x])
    point = design @ beta
    leverage = np.einsum("ij,jk,ik->i", design, xtx_inv, design)
    se = resid_std * np.sqrt(np.clip(1.0 + leverage, 0.0, None))
    return point, se


@dataclass(slots=True)
class SpreadModel:
    """z-scored linear regression of the high spread + per-row high-confidence."""

    spread_feature_cols: list[str]
    spread_col: str
    spread_z: ZScore | None = None
    beta: np.ndarray | None = None
    xtx_inv: np.ndarray | None = None
    resid_std: float = 1.0
    s_high: float = 1.0

    def fit(self, train_df: pd.DataFrame, val_df: pd.DataFrame | None = None) -> "SpreadModel":
        self.spread_z = ZScore.fit(train_df[self.spread_col].to_numpy(dtype="float64"))
        x = train_df[self.spread_feature_cols].to_numpy(dtype="float64")
        y = self.spread_z.transform(train_df[self.spread_col].to_numpy(dtype="float64"))
        self.beta, self.xtx_inv, self.resid_std = _ols_with_cov(x, y)

        ref = val_df if (val_df is not None and not val_df.empty) else train_df
        _, se = _predict_with_se(self.beta, self.xtx_inv, self.resid_std,
                                 ref[self.spread_feature_cols].to_numpy(dtype="float64"))
        se_ret = self.spread_z.inverse_std(se)
        self.s_high = float(np.median(se_ret)) if se_ret.size else 1.0
        if not np.isfinite(self.s_high) or self.s_high <= 0:
            self.s_high = 1.0
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        if self.beta is None or self.spread_z is None:
            raise RuntimeError("SpreadModel is not fitted.")
        x = df[self.spread_feature_cols].to_numpy(dtype="float64")
        z_pred, se = _predict_with_se(self.beta, self.xtx_inv, self.resid_std, x)
        spread_pred = np.clip(self.spread_z.inverse_transform(z_pred), 0.0, None)
        high_conf = np.exp(-self.spread_z.inverse_std(se) / self.s_high)
        return pd.DataFrame(
            {"spread_pred": spread_pred, "high_confidence": np.clip(high_conf, 0.0, 1.0)},
            index=df.index,
        )


def close_confidence(sigma_return: np.ndarray, s_close: float) -> np.ndarray:
    """Map per-row abnormal-return sigma to a [0,1] close confidence."""
    s = s_close if (np.isfinite(s_close) and s_close > 0) else 1.0
    return np.clip(np.exp(-np.asarray(sigma_return, dtype="float64") / s), 0.0, 1.0)
