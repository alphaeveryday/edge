"""Stage A -- FF5 rolling OLS normal/abnormal return (spec section 5.1).

For each (ticker, day t) the betas are fit ONLY on past clean (non-event) days
strictly before t, so the day-t normal return is out-of-sample and never peeks at
the day-t close. OLS math mirrors ``scripts/analysis/common/normal_return`` but is
inlined to keep this module dependency-light and unit-testable.

    y_t = ln(close_t/close_{t-1}) - rf_t
    y_t = alpha + beta . [mkt_rf, smb, hml, rmw, cma]_t + eps_t
    normal_return_t   = rf_t + alpha + beta . FF5_t
    abnormal_return_t = ln(close_t/close_{t-1}) - normal_return_t   (= eps_t)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config

_FACTORS = list(config.FACTOR_COLUMNS)


def ols_fit(train_x: np.ndarray, train_y: np.ndarray) -> tuple[np.ndarray, float]:
    """Return ``(beta, r2)`` where ``beta[0]`` is the intercept (alpha)."""
    design = np.column_stack([np.ones(len(train_x)), train_x])
    beta, *_ = np.linalg.lstsq(design, train_y, rcond=None)
    resid = train_y - design @ beta
    ss_res = float(resid @ resid)
    centered = train_y - train_y.mean()
    ss_tot = float(centered @ centered)
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return beta, r2


def ols_predict(beta: np.ndarray, x_t: np.ndarray) -> float:
    return float(np.concatenate([[1.0], x_t]) @ beta)


def clean_window_positions(is_event: np.ndarray, t_idx: int, n_samples: int) -> list[int]:
    """Most recent ``n_samples`` non-event positions strictly before ``t_idx``."""
    picked: list[int] = []
    j = t_idx - 1
    while j >= 0 and len(picked) < n_samples:
        if not is_event[j]:
            picked.append(j)
        j -= 1
    picked.reverse()
    return picked


def compute_factor_arm(
    returns_frame: pd.DataFrame,
    ff5: pd.DataFrame,
    *,
    window: int = config.ROLLING_WINDOW,
    min_obs: int = config.MIN_OBS,
    abn_threshold: float = config.ABS_ABNORMAL_THRESHOLD,
) -> pd.DataFrame:
    """Per (ticker, trade_date) FF5 normal/abnormal return + betas/alpha/r2/residual_std.

    ``returns_frame`` must carry ``ticker, trade_date, close_logret, prev_close, close, high``.
    Rows without enough clean history are omitted (caller logs them as skips).
    """
    merged = returns_frame.merge(ff5, on="trade_date", how="inner")
    merged = merged.dropna(subset=[config.LABEL_CLOSE, config.RF_COLUMN, *_FACTORS])
    merged["excess"] = merged[config.LABEL_CLOSE] - merged[config.RF_COLUMN]

    records: list[dict] = []
    for ticker, group in merged.groupby("ticker", sort=False):
        g = group.sort_values("trade_date").reset_index(drop=True)
        factors = g[_FACTORS].to_numpy(dtype="float64")
        y = g["excess"].to_numpy(dtype="float64")
        rf = g[config.RF_COLUMN].to_numpy(dtype="float64")
        logret = g[config.LABEL_CLOSE].to_numpy(dtype="float64")
        dates = g["trade_date"].to_list()
        finite = np.isfinite(y) & np.isfinite(factors).all(axis=1)
        is_event = np.abs(y) > abn_threshold  # big raw moves excluded from training window

        for t in range(len(g)):
            if not finite[t]:
                continue
            positions = [p for p in clean_window_positions(is_event, t, window) if finite[p]]
            if len(positions) < min_obs:
                continue
            train_x = factors[positions]
            train_y = y[positions]
            beta, r2 = ols_fit(train_x, train_y)
            pred_excess = ols_predict(beta, factors[t])
            normal_return = float(rf[t] + pred_excess)
            abnormal_return = float(logret[t] - normal_return)
            train_resid = train_y - np.column_stack([np.ones(len(train_x)), train_x]) @ beta
            residual_std = float(train_resid.std(ddof=1)) if len(positions) > 1 else float("nan")
            records.append(
                {
                    "ticker": ticker,
                    "trade_date": dates[t],
                    "normal_return": normal_return,
                    config.LABEL_ABNORMAL: abnormal_return,
                    "alpha": float(beta[0]),
                    "beta_mkt_rf": float(beta[1]),
                    "beta_smb": float(beta[2]),
                    "beta_hml": float(beta[3]),
                    "beta_rmw": float(beta[4]),
                    "beta_cma": float(beta[5]),
                    "residual_std": residual_std,
                    "r_squared": float(r2),
                    "n_train": len(positions),
                    "window_start_date": dates[positions[0]],
                    "window_end_date": dates[positions[-1]],
                    "is_event_candidate": bool(abs(abnormal_return) >= abn_threshold),
                }
            )
    return pd.DataFrame.from_records(records)
