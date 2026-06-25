"""Calibration gate (spec section 5.5 / screenshot top block).

A prediction "passes" when it is both close in magnitude and directionally
correct versus the realized return:

    pass = (|pred_return - real_return| < CALIB_ERROR) and (sign(pred) == sign(real))

Passing rows are candidates for the (future) LLM explanation stage; failing rows
are written to the fail log.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config


def calibration_pass(
    pred_return: np.ndarray | pd.Series,
    real_return: np.ndarray | pd.Series,
    *,
    max_error: float = config.CALIB_ERROR,
) -> np.ndarray:
    pred = np.asarray(pred_return, dtype="float64")
    real = np.asarray(real_return, dtype="float64")
    within = np.abs(pred - real) < max_error
    same_direction = np.sign(pred) == np.sign(real)
    return within & same_direction & np.isfinite(pred) & np.isfinite(real)


def apply_calibration_gate(
    frame: pd.DataFrame,
    *,
    pred_col: str = "close_return_pred",
    real_col: str = config.LABEL_CLOSE,
    max_error: float = config.CALIB_ERROR,
) -> pd.DataFrame:
    """Add ``error_return`` and ``calibration_pass`` columns (real may be NaN at inference)."""
    out = frame.copy()
    if real_col in out.columns:
        out["error_return"] = out[pred_col] - out[real_col]
        out["calibration_pass"] = calibration_pass(out[pred_col], out[real_col], max_error=max_error)
    else:
        out["error_return"] = np.nan
        out["calibration_pass"] = pd.NA
    return out
