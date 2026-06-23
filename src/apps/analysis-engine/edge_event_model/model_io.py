"""Persist / restore the trained model for daily inference (no retrain).

* ``news_nn.pt``           -- attention NN weights + input scaler + target z-score.
* ``spread_model.joblib``  -- fitted high-spread model.
* ``factor_latest.parquet``-- per-ticker latest FF5 alpha/betas + last close + spread lags.
* ``meta.json``            -- s_close, embedding model id, train window, etc.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .features.returns import ZScore
from .models.combine import SpreadModel
from .models.news_nn import _InputScaler
from .models.temporal_nn import TemporalNewsModel, _build_temporal_module

FACTOR_LATEST_COLUMNS = (
    "ticker", "alpha", "beta_mkt_rf", "beta_smb", "beta_hml", "beta_rmw", "beta_cma",
    "residual_std", "last_close", "last_trade_date", "spread_lag_mean", "spread_lag_std",
)


def build_factor_latest(predicted: pd.DataFrame) -> pd.DataFrame:
    last = predicted.sort_values("trade_date").groupby("ticker", as_index=False).tail(1).copy()
    last = last.rename(columns={"close": "last_close", "trade_date": "last_trade_date"})
    for col in ("spread_lag_mean", "spread_lag_std"):
        if col not in last.columns:
            last[col] = 0.0
    return last[list(FACTOR_LATEST_COLUMNS)].reset_index(drop=True)


def save_artifacts(out_dir: str | Path, news_model: TemporalNewsModel, spread_model: SpreadModel,
                   factor_latest: pd.DataFrame, meta: dict[str, Any] | None = None) -> Path:
    import joblib
    import torch

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if news_model.module is None or news_model.scaler is None or news_model.target_z is None:
        raise RuntimeError("news_model must be fitted before saving.")
    torch.save(
        {
            "state_dict": news_model.module.state_dict(),
            "emb_dim": news_model.emb_dim, "proj": news_model.proj,
            "hidden": news_model.hidden, "dropout": news_model.dropout, "seq_len": news_model.seq_len,
            "target_col": news_model.target_col,
            "scaler_mean": np.asarray(news_model.scaler.mean).tolist(),
            "scaler_std": np.asarray(news_model.scaler.std).tolist(),
            "tz_mean": float(news_model.target_z.mean), "tz_std": float(news_model.target_z.std),
        },
        out / "news_nn.pt",
    )
    joblib.dump(spread_model, out / "spread_model.joblib")
    factor_latest.to_parquet(out / "factor_latest.parquet", index=False)
    (out / "meta.json").write_text(json.dumps(meta or {}, default=str, indent=2), encoding="utf-8")
    return out


def load_artifacts(in_dir: str | Path) -> tuple[TemporalNewsModel, SpreadModel, pd.DataFrame, dict[str, Any]]:
    import joblib
    import torch

    p = Path(in_dir)
    ckpt = torch.load(p / "news_nn.pt", map_location="cpu", weights_only=False)
    nm = TemporalNewsModel(
        target_col=ckpt["target_col"], emb_dim=ckpt["emb_dim"], proj=ckpt["proj"],
        hidden=ckpt["hidden"], dropout=ckpt["dropout"], seq_len=int(ckpt.get("seq_len", 30)),
    )
    nm.scaler = _InputScaler(np.asarray(ckpt["scaler_mean"], dtype="float32"), np.asarray(ckpt["scaler_std"], dtype="float32"))
    nm.target_z = ZScore(ckpt["tz_mean"], ckpt["tz_std"])
    module = _build_temporal_module(ckpt["emb_dim"], ckpt["proj"], ckpt["hidden"], ckpt["dropout"], int(ckpt.get("seq_len", 30)))
    module.load_state_dict(ckpt["state_dict"])
    module.eval()
    nm.module = module
    spread_model = joblib.load(p / "spread_model.joblib")
    factor_latest = pd.read_parquet(p / "factor_latest.parquet")
    meta = json.loads((p / "meta.json").read_text(encoding="utf-8")) if (p / "meta.json").exists() else {}
    return nm, spread_model, factor_latest, meta
