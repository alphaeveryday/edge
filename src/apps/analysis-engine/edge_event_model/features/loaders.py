"""Input loaders -- local parquet mirror first, Postgres ``edge`` fallback for news.

All frames use tz-naive, day-normalized ``trade_date`` Timestamps.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .. import config
from ..errors import FactorDataNotFound, PriceOhlcNotFound

_OHLC_COLS = ["market", "ticker", "trade_date", "open", "high", "low", "close", "volume"]


def _normalize_dates(frame: pd.DataFrame, column: str = "trade_date") -> pd.DataFrame:
    out = frame.copy()
    out[column] = pd.to_datetime(out[column]).dt.tz_localize(None).dt.normalize()
    return out


def _clip_window(frame: pd.DataFrame, start: date | None, end: date | None, column: str = "trade_date") -> pd.DataFrame:
    if start is not None:
        frame = frame[frame[column] >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame[column] <= pd.Timestamp(end)]
    return frame


def load_ohlc(
    tickers: Iterable[str] = config.TICKERS,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Return ``ticker, trade_date, open, high, low, close, volume`` for the universe."""
    if not config.PRICE_PARQUET.exists():
        raise PriceOhlcNotFound(f"Missing OHLC parquet: {config.PRICE_PARQUET}")
    want = set(tickers)
    df = pd.read_parquet(config.PRICE_PARQUET, columns=_OHLC_COLS)
    df = df[df["ticker"].isin(want)].copy()
    if df.empty:
        raise PriceOhlcNotFound(f"No OHLC rows for tickers {sorted(want)} in {config.PRICE_PARQUET}")
    df = _normalize_dates(df)
    df = _clip_window(df, start, end)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    df = df.sort_values(["ticker", "trade_date"]).reset_index(drop=True)
    return df[["ticker", "trade_date", "open", "high", "low", "close", "volume"]]


def _latest_ff5_path() -> Path:
    files = sorted(config.ANALYSIS_OUTPUTS_DIR.glob(config.FF5_GLOB))
    if not files:
        raise FactorDataNotFound(
            f"No FF5 parquet matching {config.FF5_GLOB!r} under {config.ANALYSIS_OUTPUTS_DIR}"
        )
    return files[-1]


def _ff5_db_frame() -> pd.DataFrame:
    import json as _json
    import psycopg2

    pw = os.environ.get("PGPW")
    if not pw and os.environ.get("RDS_SECRET_FILE"):
        pw = _json.loads(Path(os.environ["RDS_SECRET_FILE"]).read_text(encoding="utf-8"))["password"]
    conn = psycopg2.connect(
        host=os.environ.get("NEWSDB_HOST", "127.0.0.1"), port=int(os.environ.get("NEWSDB_PORT", "15433")),
        dbname=os.environ.get("NEWSDB_NAME", "newspipeline"), user=os.environ.get("NEWSDB_USER", "pipeline_admin"),
        password=pw, sslmode="require", connect_timeout=30,
    )
    try:
        return pd.read_sql_query("SELECT trade_date, mkt_rf, smb, hml, rmw, cma, rf FROM market.us_ff5_factors", conn)
    finally:
        conn.close()


def _ff5_source_frame() -> pd.DataFrame:
    """Self-computed US FF5: DB (EDGE_FF5_FROM_DB=1) -> computed parquet -> legacy French parquet."""
    if os.environ.get("EDGE_FF5_FROM_DB") == "1":
        return _ff5_db_frame()
    computed = config.ROOT / "data" / "ff5_build" / "us_ff5_computed.parquet"
    if computed.exists():
        return pd.read_parquet(computed)
    return pd.read_parquet(_latest_ff5_path())


def _normalize_factor_unit(frame: pd.DataFrame) -> pd.DataFrame:
    cols = [*config.FACTOR_COLUMNS, config.RF_COLUMN]
    unit = config.FF5_FACTOR_UNIT
    if unit == "auto":
        # Daily FF5 in decimal: mean |mkt_rf| ~ 0.7%. In percent it would be ~0.7.
        unit = "percent" if frame["mkt_rf"].abs().mean() > 0.5 else "decimal"
    if unit == "percent":
        frame = frame.copy()
        frame[cols] = frame[cols] / 100.0
    return frame


def load_ff5(start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Return ``trade_date, mkt_rf, smb, hml, rmw, cma, rf`` (decimal)."""
    df = _ff5_source_frame()
    if "market" in df.columns:
        df = df[df["market"].astype(str).str.upper() == "US"]
    missing = [c for c in (*config.FACTOR_COLUMNS, config.RF_COLUMN) if c not in df.columns]
    if missing:
        raise FactorDataNotFound(f"FF5 parquet missing columns: {missing}")
    df = df[["trade_date", *config.FACTOR_COLUMNS, config.RF_COLUMN]].copy()
    df = _normalize_dates(df)
    for col in (*config.FACTOR_COLUMNS, config.RF_COLUMN):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=list(config.FACTOR_COLUMNS))
    df = _normalize_factor_unit(df)
    df = _clip_window(df, start, end)
    return df.drop_duplicates("trade_date").sort_values("trade_date").reset_index(drop=True)


def load_news(
    tickers: Iterable[str] = config.TICKERS,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Return ``ticker, published_at, news_id, title, url`` (UTC-naive published_at).

    Parquet mirror first; Postgres ``etf.news_articles`` fallback if absent.
    """
    want = set(tickers)
    if config.NEWS_PARQUET.exists():
        df = pd.read_parquet(config.NEWS_PARQUET)
        df = df[df["ticker"].isin(want)].copy()
        df = df.rename(columns={"article_id": "news_id", "content": "title"})
        df["published_at"] = pd.to_datetime(df["published_at"]).dt.tz_localize(None)
    else:
        df = _load_news_pg(want)
    df = df.dropna(subset=["title", "published_at"])
    df["title"] = df["title"].astype(str).str.strip()
    df = df[df["title"].str.len() > 0]
    if start is not None:
        df = df[df["published_at"] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df["published_at"] <= pd.Timestamp(end) + pd.Timedelta(days=1)]
    cols = ["ticker", "published_at", "news_id", "title", "url"]
    for col in cols:
        if col not in df.columns:
            df[col] = None
    return df[cols].sort_values(["ticker", "published_at"]).reset_index(drop=True)


def _load_news_pg(tickers: set[str]) -> pd.DataFrame:
    import psycopg2

    cfg = config.load_pg_config()
    if not cfg.password:
        raise PriceOhlcNotFound(
            "News parquet missing and PGPASSWORD unset; cannot reach Postgres fallback."
        )
    conn = psycopg2.connect(
        host=cfg.host, port=cfg.port, dbname=cfg.database, user=cfg.user, password=cfg.password,
    )
    try:
        placeholders = ", ".join(["%s"] * len(tickers))
        query = (
            f"SELECT ticker, published_at, article_id AS news_id, title, url "
            f"FROM {cfg.schema}.news_articles WHERE ticker IN ({placeholders})"
        )
        df = pd.read_sql_query(query, conn, params=list(tickers))
    finally:
        conn.close()
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True).dt.tz_convert("UTC").dt.tz_localize(None)
    return df
