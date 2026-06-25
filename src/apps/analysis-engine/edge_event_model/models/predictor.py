"""Reconstruct predicted close/high from the attention NN + spread model.

    abnormal, sigma   = news_model.predict_abnormal(df, day_emb)   # NN direct regression
    close_return_pred = normal_return + abnormal
    predicted_close   = prev_close * exp(close_return_pred)
    predicted_high    = predicted_close * exp(max(spread_pred, 0))
    close_confidence  = exp(-sigma / s_close)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .combine import SpreadModel, close_confidence


def build_predictions(df: pd.DataFrame, news_model, spread_model: SpreadModel, news_ctx, s_close: float) -> pd.DataFrame:
    out = df.copy()
    abnormal, sigma = news_model.predict_abnormal(df, news_ctx)
    spread = spread_model.predict(df)

    out["abnormal_return_pred"] = abnormal
    out["news_uncertainty"] = sigma
    out["close_return_pred"] = out["normal_return"].to_numpy() + abnormal
    out["spread_pred"] = spread["spread_pred"].to_numpy()
    out["close_confidence"] = close_confidence(sigma, s_close)
    out["high_confidence"] = spread["high_confidence"].to_numpy()
    out["predicted_close"] = out["prev_close"] * np.exp(out["close_return_pred"])
    out["predicted_high"] = out["predicted_close"] * np.exp(np.clip(out["spread_pred"], 0.0, None))
    return out
