"""Same-day inference (void main) -- attention news model.

For the tickers chosen in ``main``, pull that day's news from
``market.us_news_articles``, embed every article, **attention-pool the day's
articles and regress the abnormal return directly**, then upsert one row per
ticker into ``market.daily_prediction``.

    PGPW=... python -m edge_event_model.pipeline.daily
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from edge_event_model import config
from edge_event_model.db import schema
from edge_event_model.features import build_news_windows, load_ohlc
from edge_event_model.features.news_arm import TitleEmbedder
from edge_event_model.models.combine import close_confidence
from edge_event_model.model_io import load_artifacts

PG_TABLE = "market.daily_prediction"


def _dsn() -> dict:
    return dict(
        host=os.environ.get("NEWSDB_HOST", "127.0.0.1"), port=int(os.environ.get("NEWSDB_PORT", "15433")),
        dbname=os.environ.get("NEWSDB_NAME", "newspipeline"), user=os.environ.get("NEWSDB_USER", "pipeline_admin"),
        password=os.environ["PGPW"],
    )


def _coerce_pg(name: str, value):
    from psycopg2.extras import Json

    if name in schema.JSON_COLUMNS:
        return None if value is None else Json(value)
    if name in schema.BOOL_COLUMNS:
        return None if value is None else bool(value)
    return value


def _fetch_recent_news(conn, tickers: list[str], start_date: date, end_date: date) -> pd.DataFrame:
    sql = """
        SELECT ticker, article_id AS news_id, title, published_at
        FROM market.us_news_articles
        WHERE ticker = ANY(%s)
          AND (published_at AT TIME ZONE 'America/New_York')::date BETWEEN %s AND %s
    """
    df = pd.read_sql_query(sql, conn, params=(list(tickers), start_date, end_date))
    if not df.empty:
        df["published_at"] = pd.to_datetime(df["published_at"], utc=True).dt.tz_localize(None)
    return df


def run_daily(
    tickers: list[str] | tuple[str, ...] = config.TICKERS,
    *,
    artifacts_dir: str | Path,
    target_date: date | None = None,
    market: str = "US",
    embedder: TitleEmbedder | None = None,
) -> None:
    import psycopg2

    news_model, spread_model, factor_latest, meta = load_artifacts(artifacts_dir)
    latest = {row.ticker: row for row in factor_latest.itertuples(index=False)}
    s_close = float(meta.get("s_close", 1.0)) or 1.0
    model_version = str(meta.get("model_version", "ff5_attn_news_v2"))
    spread_cols = spread_model.spread_feature_cols

    conn = psycopg2.connect(connect_timeout=30, **_dsn())
    try:
        cur = conn.cursor()
        cur.execute(schema.pg_ddl(PG_TABLE))
        for stmt in schema.pg_comment_sql(PG_TABLE):
            cur.execute(stmt)
        conn.commit()

        if target_date is None:
            cur.execute("SELECT max((published_at AT TIME ZONE 'America/New_York')::date) "
                        "FROM market.us_news_articles WHERE ticker = ANY(%s)", (list(tickers),))
            target_date = cur.fetchone()[0]
        if target_date is None:
            print("No news available; nothing to score.")
            return
        ts = pd.Timestamp(target_date)

        news = _fetch_recent_news(conn, list(tickers), target_date - timedelta(days=60), target_date)
        ohlc = load_ohlc(list(tickers), end=target_date)
        trading_dates = {t: np.sort(g["trade_date"].unique()) for t, g in ohlc.groupby("ticker", sort=False)}
        embedder = embedder or TitleEmbedder()
        news_daily, windows = build_news_windows(news, trading_dates, embedder=embedder)
        counts: dict[str, int] = {}
        if not news_daily.empty:
            counts = {r.ticker: int(r.news_count)
                      for r in news_daily[news_daily["trade_date"] == ts].itertuples(index=False)}
        news_ids_by_ticker: dict[str, list] = {}
        if not news.empty:
            from edge_event_model.features.news_arm import assign_trade_dates, dedupe_news
            today_news = dedupe_news(assign_trade_dates(news, trading_dates))
            today_news = today_news[today_news["trade_date"] == ts]
            for tk, g in today_news.groupby("ticker"):
                news_ids_by_ticker[tk] = list(g["news_id"].astype(str))

        # one feature row per ticker that has trained state
        rows_meta = []
        feat_rows = []
        for ticker in tickers:
            fl = latest.get(ticker)
            if fl is None:
                rows_meta.append((ticker, None, 0))
                continue
            feat_rows.append({
                "ticker": ticker, "trade_date": ts, "normal_return": float(fl.alpha),
                "spread_lag_mean": float(fl.spread_lag_mean), "spread_lag_std": float(fl.spread_lag_std),
                "news_count": counts.get(ticker, 0),
            })
            rows_meta.append((ticker, fl, counts.get(ticker, 0)))

        records: list[dict] = []
        if feat_rows:
            fdf = pd.DataFrame(feat_rows)
            abnormal, sigma = news_model.predict_abnormal(fdf, windows)
            spread_out = spread_model.predict(fdf[spread_cols] if set(spread_cols).issubset(fdf.columns) else fdf)
            cconf = close_confidence(sigma, s_close)
            by_ticker = {row.ticker: i for i, row in enumerate(fdf.itertuples(index=False))}
        for ticker, fl, ncount in rows_meta:
            if fl is None:
                records.append({"trade_date": target_date, "market": market, "asset_code": ticker,
                                "sector": config.SECTOR_BY_TICKER.get(ticker), "status": "skipped",
                                "error_code": "NO_MODEL_STATE", "error_message": "no trained FF5 state",
                                "model_version": model_version, "embed_model": config.EMBED_MODEL, "ff5_available": False})
                continue
            i = by_ticker[ticker]
            abn = float(abnormal[i]); sig = float(sigma[i])
            spread = float(spread_out["spread_pred"].iloc[i]); hconf = float(spread_out["high_confidence"].iloc[i])
            alpha = float(fl.alpha); close_ret = alpha + abn
            prev_close = float(fl.last_close)
            predicted_close = prev_close * float(np.exp(close_ret))
            predicted_high = predicted_close * float(np.exp(max(spread, 0.0)))
            records.append({
                "trade_date": target_date, "market": market, "asset_code": ticker,
                "sector": config.SECTOR_BY_TICKER.get(ticker), "status": "ok",
                "news_count": ncount, "news_ids": news_ids_by_ticker.get(ticker, []),
                "input_factor_values": None, "prev_close_price": prev_close,
                "layer1_ff5_normal_return": alpha, "layer1_ff5_alpha": alpha,
                "layer1_ff5_importance_betas": {"mkt_rf": float(fl.beta_mkt_rf), "smb": float(fl.beta_smb),
                                                "hml": float(fl.beta_hml), "rmw": float(fl.beta_rmw), "cma": float(fl.beta_cma)},
                "layer1_ff5_r2": None, "layer1_ff5_residual_std": float(fl.residual_std),
                "layer2_news_abnormal_return": abn if ncount else None,
                "layer2_news_uncertainty": sig if ncount else None,
                "layer3_final_abnormal_return": abn,
                "predicted_return": close_ret, "predicted_close_price": predicted_close,
                "predicted_high_price": predicted_high, "predicted_direction": int(np.sign(close_ret)),
                "close_confidence": float(cconf[i]), "high_confidence": hconf,
                "actual_return": None, "actual_close_price": None, "actual_high_price": None, "actual_abnormal_return": None,
                "return_error": None, "close_price_error": None,
                "is_event": bool(abs(abn) >= config.ABS_ABNORMAL_THRESHOLD), "calibration_pass": None,
                "error_code": None, "error_message": None,
                "debug_payload": {"spread_pred": spread, "sigma": sig},
                "model_version": model_version, "embed_model": config.EMBED_MODEL, "ff5_available": False,
            })

        now = schema.utc_now()
        payload = []
        for rec in records:
            rec.setdefault("created_at", now)
            payload.append([_coerce_pg(c, rec.get(c)) for c in schema.COLUMN_NAMES])
        cur.executemany(schema.upsert_sql(PG_TABLE, "pg"), payload)
        conn.commit()

        ok = [r for r in records if r["status"] == "ok"]
        print(f"[daily] target_date={target_date} saved={len(records)} (ok={len(ok)}) news_rows={len(news)} -> {PG_TABLE}")
        for r in ok:
            print(f"  {r['asset_code']:6s} news={r['news_count']:>3d} abn={r['layer3_final_abnormal_return']:+.4f} "
                  f"pred_ret={r['predicted_return']:+.4f} pred_close={r['predicted_close_price']:.2f} "
                  f"conf(c/h)={r['close_confidence']:.2f}/{r['high_confidence']:.2f} dir={r['predicted_direction']:+d}")
    finally:
        conn.close()


def main() -> None:
    artifacts = os.environ.get("ARTIFACTS_DIR", str(config.PACKAGE_ROOT / "model_artifacts"))
    run_daily(tickers=config.TICKERS, artifacts_dir=artifacts)


if __name__ == "__main__":
    main()
