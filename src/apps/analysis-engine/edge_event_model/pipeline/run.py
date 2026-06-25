"""End-to-end pipeline: data -> FF5 -> attention news NN (direct abnormal
regression) -> spread model -> predict -> calibrate -> persist to SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

import numpy as np
import pandas as pd

from .. import config
from ..calibration import apply_calibration_gate
from ..db import connect, init_schema, upsert_daily
from ..features import (
    build_dataset,
    build_news_windows,
    chronological_split,
    load_ff5,
    load_news,
    load_ohlc,
    spread_feature_columns,
)
from ..models import SpreadModel, TemporalNewsModel, ZeroNewsModel, build_predictions

_MIN_NEWS_ROWS = 20


@dataclass(slots=True)
class PipelineResult:
    start_date: date
    end_date: date
    tickers: list[str]
    rows: int
    saved_rows: int
    fail_rows: int
    news_model: str
    metrics: dict[str, Any] = field(default_factory=dict)


def _trading_dates(ohlc: pd.DataFrame) -> dict[str, np.ndarray]:
    return {t: np.sort(g["trade_date"].unique()) for t, g in ohlc.groupby("ticker", sort=False)}


def _f(value: Any) -> Any:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def _d(value: Any) -> Any:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    return pd.Timestamp(value).date().isoformat()


def _result_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in frame.to_dict("records"):
        cp = r.get("calibration_pass")
        cp_val = None if cp is None or (isinstance(cp, float) and np.isnan(cp)) else bool(cp)
        pred_ret = _f(r.get("close_return_pred"))
        pred_close = _f(r.get("predicted_close"))
        actual_close = _f(r.get("close"))
        abnormal = _f(r.get("abnormal_return_pred"))
        rows.append({
            "trade_date": _d(r.get("trade_date")), "market": "US", "asset_code": r.get("ticker"),
            "sector": r.get("sector"), "status": "ok", "split": r.get("split"),
            "news_count": int(r.get("news_count") or 0), "news_ids": None, "input_factor_values": None,
            "prev_close_price": _f(r.get("prev_close")),
            "layer1_ff5_normal_return": _f(r.get("normal_return")), "layer1_ff5_alpha": _f(r.get("alpha")),
            "layer1_ff5_importance_betas": {
                "mkt_rf": _f(r.get("beta_mkt_rf")), "smb": _f(r.get("beta_smb")), "hml": _f(r.get("beta_hml")),
                "rmw": _f(r.get("beta_rmw")), "cma": _f(r.get("beta_cma")),
            },
            "layer1_ff5_r2": _f(r.get("r_squared")), "layer1_ff5_residual_std": _f(r.get("residual_std")),
            "layer2_news_abnormal_return": abnormal,
            "layer2_news_uncertainty": _f(r.get("news_uncertainty")),
            "layer3_final_abnormal_return": abnormal,
            "predicted_return": pred_ret, "predicted_close_price": pred_close,
            "predicted_high_price": _f(r.get("predicted_high")),
            "predicted_direction": None if pred_ret is None else int(np.sign(pred_ret)),
            "close_confidence": _f(r.get("close_confidence")), "high_confidence": _f(r.get("high_confidence")),
            "actual_return": _f(r.get(config.LABEL_CLOSE)), "actual_close_price": actual_close,
            "actual_high_price": _f(r.get("high")), "actual_abnormal_return": _f(r.get(config.LABEL_ABNORMAL)),
            "return_error": _f(r.get("error_return")),
            "close_price_error": (None if pred_close is None or actual_close is None else pred_close - actual_close),
            "is_event": bool(r.get("is_event_candidate")), "calibration_pass": cp_val,
            "error_code": None, "error_message": None,
            "debug_payload": {"spread_pred": _f(r.get("spread_pred")),
                              "window_start": _d(r.get("window_start_date")), "window_end": _d(r.get("window_end_date"))},
            "model_version": "ff5_attn_news_v2", "embed_model": config.EMBED_MODEL, "ff5_available": True,
        })
    return rows


def _metrics(frame: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for split in ("validation", "test"):
        part = frame[frame["split"] == split]
        if part.empty:
            continue
        real = part[config.LABEL_CLOSE].to_numpy(dtype="float64")
        pred = part["close_return_pred"].to_numpy(dtype="float64")
        base = part["normal_return"].to_numpy(dtype="float64")
        real_high = np.log(part["high"] / part["prev_close"]).to_numpy(dtype="float64")
        pred_high = np.log(part["predicted_high"] / part["prev_close"]).to_numpy(dtype="float64")
        ev = part[part["news_count"] > 0]
        out[split] = {
            "rows": int(len(part)),
            "close_mae": float(np.mean(np.abs(pred - real))),
            "close_mae_baseline": float(np.mean(np.abs(base - real))),
            "close_dir_acc": float(np.mean(np.sign(pred) == np.sign(real))),
            "abnormal_pred_std": float(part["abnormal_return_pred"].std()),
            "abnormal_real_std": float(part[config.LABEL_ABNORMAL].std()),
            "corr_pred_real_abnormal": float(np.corrcoef(part["abnormal_return_pred"], part[config.LABEL_ABNORMAL])[0, 1])
            if part["abnormal_return_pred"].std() > 1e-12 else float("nan"),
            "high_mae": float(np.mean(np.abs(pred_high - real_high))),
            "calibration_pass_rate": float(np.mean(part["calibration_pass"].astype("float64"))),
        }
    return out


def run(
    start: date = config.ANALYSIS_START,
    end: date = config.ANALYSIS_END,
    tickers: Sequence[str] = config.TICKERS,
    *,
    db_path: Any = config.DB_PATH,
    min_obs: int = config.MIN_OBS,
    persist: bool = True,
    news_limit: int | None = None,
    save_dir: str | None = None,
) -> PipelineResult:
    tickers = list(tickers)
    fails: list[str] = []

    ohlc = load_ohlc(tickers, end=end)
    ff5 = load_ff5(end=end)
    trading_dates = _trading_dates(ohlc)

    news = load_news(tickers, start=start, end=end)
    if news_limit is not None and len(news) > news_limit:
        news = news.sample(n=news_limit, random_state=17).sort_values("published_at").reset_index(drop=True)
    news_daily, windows = build_news_windows(news, trading_dates)

    dataset = build_dataset(ohlc, ff5, news_daily, min_obs=min_obs)
    if dataset.empty:
        raise RuntimeError("Empty dataset after factor arm; check price/FF5 coverage.")
    dataset = dataset[dataset["trade_date"] >= pd.Timestamp(start)].reset_index(drop=True)
    dataset = chronological_split(dataset)

    train = dataset[dataset["split"] == "train"]
    val = dataset[dataset["split"] == "validation"]
    train_news = train[train["news_count"] > 0]
    val_news = val[val["news_count"] > 0]

    if len(train_news) >= _MIN_NEWS_ROWS:
        news_model: Any = TemporalNewsModel(target_col=config.LABEL_ABNORMAL, emb_dim=config.EMBED_DIM)
        news_model.fit(train, windows, val if not val.empty else None)
        news_model_name = "TemporalNewsModel"
    else:
        news_model = ZeroNewsModel(sigma=float(train[config.LABEL_ABNORMAL].std() or 0.0))
        news_model_name = "ZeroNewsModel"
        fails.append(f"INSUFFICIENT_NEWS: only {len(train_news)} train news rows (<{_MIN_NEWS_ROWS}).")

    spread_model = SpreadModel(spread_feature_cols=spread_feature_columns(), spread_col=config.LABEL_SPREAD)
    spread_model.fit(train, val if not val.empty else None)

    # close-confidence scale = median abnormal-sigma on validation news rows
    ref = val_news if not val_news.empty else train_news
    if len(ref):
        _, sig_ref = news_model.predict_abnormal(ref, windows)
        sig_ref = sig_ref[np.isfinite(sig_ref)]
        s_close = float(np.median(sig_ref)) if sig_ref.size else float(train[config.LABEL_ABNORMAL].std() or 1.0)
    else:
        s_close = float(train[config.LABEL_ABNORMAL].std() or 1.0)
    s_close = s_close if (np.isfinite(s_close) and s_close > 0) else 1.0

    predicted = build_predictions(dataset, news_model, spread_model, windows, s_close)
    predicted = apply_calibration_gate(predicted)
    metrics = _metrics(predicted)

    if save_dir is not None and news_model_name == "TemporalNewsModel":
        from ..model_io import build_factor_latest, save_artifacts

        save_artifacts(
            save_dir, news_model, spread_model, build_factor_latest(predicted),
            meta={"start": str(start), "end": str(end), "tickers": tickers,
                  "s_close": s_close, "model_version": "ff5_temporal_news_v3"},
        )

    saved = 0
    if persist:
        conn = connect(db_path)
        try:
            init_schema(conn)
            saved = upsert_daily(conn, _result_rows(predicted))
        finally:
            conn.close()

    return PipelineResult(
        start_date=start, end_date=end, tickers=tickers, rows=int(len(predicted)),
        saved_rows=saved, fail_rows=len(fails), news_model=news_model_name, metrics=metrics,
    )
