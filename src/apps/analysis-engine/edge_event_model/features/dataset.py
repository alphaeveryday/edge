"""Combine factor arm + targets + per-day news counts, with a leakage guard.

Per (ticker, trade_date):
* targets   : close_logret, spread, abnormal_return  (never features)
* leakage   : close, high, low, volume               (same-day realized; never features)
* allowed   : normal_return (same-day FF5), prev_close (t-1), lagged spread stats, news_count

Article embeddings are NOT columns here -- they live in the ``day_emb`` dict
(``news_arm.build_day_embeddings``) and are attention-pooled inside the NN.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import config
from .factor_arm import compute_factor_arm
from .returns import add_log_returns, add_spread

TARGET_COLUMNS: tuple[str, ...] = (config.LABEL_CLOSE, config.LABEL_SPREAD, config.LABEL_ABNORMAL)
LEAKAGE_COLUMNS: tuple[str, ...] = ("close", "high", "low", "volume")
SPREAD_LAG_MEAN = "spread_lag_mean"
SPREAD_LAG_STD = "spread_lag_std"


def spread_feature_columns() -> list[str]:
    """Features fed to the high-spread linear regression."""
    return ["normal_return", SPREAD_LAG_MEAN, SPREAD_LAG_STD, "news_count"]


def assert_no_leakage(feature_columns: list[str]) -> None:
    """Raise if any feature is a target or same-day realized price/volume."""
    banned = set(TARGET_COLUMNS) | set(LEAKAGE_COLUMNS)
    offenders = sorted(set(feature_columns) & banned)
    if offenders:
        raise ValueError(f"Leakage: same-day target/realized columns used as features: {offenders}")


def _add_spread_lags(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values(["ticker", "trade_date"]).copy()
    prev = out.groupby("ticker", sort=False)[config.LABEL_SPREAD].shift(1)
    out[SPREAD_LAG_MEAN] = prev.groupby(out["ticker"], sort=False).transform(lambda s: s.rolling(5, min_periods=1).mean())
    out[SPREAD_LAG_STD] = prev.groupby(out["ticker"], sort=False).transform(lambda s: s.rolling(5, min_periods=2).std())
    out[SPREAD_LAG_MEAN] = out[SPREAD_LAG_MEAN].fillna(0.0)
    out[SPREAD_LAG_STD] = out[SPREAD_LAG_STD].fillna(0.0)
    return out


def build_dataset(
    ohlc: pd.DataFrame,
    ff5: pd.DataFrame,
    news_daily: pd.DataFrame | None = None,
    *,
    window: int = config.ROLLING_WINDOW,
    min_obs: int = config.MIN_OBS,
    abn_threshold: float = config.ABS_ABNORMAL_THRESHOLD,
) -> pd.DataFrame:
    """Return the joined modeling frame (FF5 + targets + news_count), sorted by date/ticker."""
    returns = add_spread(add_log_returns(ohlc))
    factor = compute_factor_arm(
        returns[["ticker", "trade_date", config.LABEL_CLOSE]],
        ff5, window=window, min_obs=min_obs, abn_threshold=abn_threshold,
    )
    if factor.empty:
        return factor
    base = returns.merge(factor, on=["ticker", "trade_date"], how="inner")

    if news_daily is not None and not news_daily.empty:
        base = base.merge(news_daily[["ticker", "trade_date", "news_count"]], on=["ticker", "trade_date"], how="left")
    base["news_count"] = base.get("news_count", pd.Series(0, index=base.index)).fillna(0).astype(int)

    base["sector"] = base["ticker"].map(config.SECTOR_BY_TICKER)
    base = _add_spread_lags(base)
    return base.sort_values(["trade_date", "ticker"]).reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class SplitConfig:
    train_ratio: float = config.TRAIN_RATIO
    valid_ratio: float = config.VALID_RATIO


def chronological_split(frame: pd.DataFrame, cfg: SplitConfig = SplitConfig()) -> pd.DataFrame:
    """Add a ``split`` column (train/validation/test) by unique trade_date boundaries."""
    out = frame.copy()
    dates = np.sort(out["trade_date"].unique())
    n = len(dates)
    if n == 0:
        out["split"] = pd.Series(dtype="object")
        return out
    train_end = max(1, int(n * cfg.train_ratio))
    valid_end = min(n, max(train_end + 1, int(n * (cfg.train_ratio + cfg.valid_ratio))))
    train_dates = set(dates[:train_end])
    valid_dates = set(dates[train_end:valid_end])
    out["split"] = "test"
    out.loc[out["trade_date"].isin(train_dates), "split"] = "train"
    out.loc[out["trade_date"].isin(valid_dates), "split"] = "validation"
    return out
