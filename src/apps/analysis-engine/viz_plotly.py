#!/usr/bin/env python3
"""Interactive Plotly HTML visualizations for the 9-ticker predictions.

Outputs (under artifacts/):
* ``ohlc_predictions.html``      -- daily OHLC candlestick + predicted Close & High.
* ``return_decomposition.html``  -- candlestick + FF5 normal-return predicted close
  and the (normal + news) summed predicted close (the marked points).

Only dates that have a model prediction are drawn, i.e. clipped to where FF5 data
actually exists. Ticker selector via dropdown; fully zoom/pan-able.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edge_event_model import config

HERE = Path(__file__).resolve().parent
OUT = HERE / "artifacts"
OUT.mkdir(parents=True, exist_ok=True)

_OHLC_COLS = ["ticker", "trade_date", "open", "high", "low", "close"]
_RANGEBREAKS = [dict(bounds=["sat", "mon"])]  # hide weekend gaps


def _load() -> dict[str, pd.DataFrame]:
    con = sqlite3.connect(str(config.DB_PATH))
    pred = pd.read_sql_query(
        "SELECT asset_code, trade_date, prev_close_price, predicted_close_price, predicted_high_price, "
        "layer1_ff5_normal_return, layer3_final_abnormal_return, predicted_return "
        "FROM daily_prediction WHERE status='ok'",
        con,
    )
    con.close()
    pred["trade_date"] = pd.to_datetime(pred["trade_date"])

    ohlc = pd.read_parquet(config.PRICE_PARQUET, columns=_OHLC_COLS)
    ohlc = ohlc[ohlc["ticker"].isin(config.TICKERS)].copy()
    ohlc["trade_date"] = pd.to_datetime(ohlc["trade_date"]).dt.tz_localize(None).dt.normalize()

    out: dict[str, pd.DataFrame] = {}
    for ticker in config.TICKERS:
        p = pred[pred["asset_code"] == ticker]
        o = ohlc[ohlc["ticker"] == ticker]
        merged = o.merge(p, on="trade_date", how="inner").sort_values("trade_date")
        if merged.empty:
            continue
        merged["normal_only_close"] = merged["prev_close_price"] * np.exp(merged["layer1_ff5_normal_return"])
        out[ticker] = merged.reset_index(drop=True)
    return out


def _candle(d: pd.DataFrame, ticker: str, visible: bool) -> go.Candlestick:
    return go.Candlestick(
        x=d["trade_date"], open=d["open"], high=d["high"], low=d["low"], close=d["close"],
        name=f"{ticker} OHLC", visible=visible, increasing_line_color="#d33", decreasing_line_color="#39c",
    )


def _dropdown(tickers: list[str], traces_per: int, title_fmt: str) -> list[dict]:
    buttons = []
    n = len(tickers)
    for i, ticker in enumerate(tickers):
        vis = [False] * (n * traces_per)
        for k in range(traces_per):
            vis[i * traces_per + k] = True
        buttons.append(dict(label=ticker, method="update",
                            args=[{"visible": vis}, {"title.text": title_fmt.format(ticker=ticker)}]))
    return [dict(active=0, x=1.0, xanchor="right", y=1.13, yanchor="top", showactive=True, buttons=buttons)]


def build_ohlc_predictions(data: dict[str, pd.DataFrame]) -> Path:
    tickers = list(data)
    fig = go.Figure()
    for i, ticker in enumerate(tickers):
        d = data[ticker]
        first = i == 0
        fig.add_trace(_candle(d, ticker, first))
        fig.add_trace(go.Scatter(x=d["trade_date"], y=d["predicted_close_price"], mode="lines",
                                 line=dict(color="#1565c0", width=1.4), name="예측 종가(C)", visible=first))
        fig.add_trace(go.Scatter(x=d["trade_date"], y=d["predicted_high_price"], mode="lines",
                                 line=dict(color="#ef6c00", width=1.2, dash="dot"), name="예측 고가(H)", visible=first))
    fig.update_layout(
        title=f"{tickers[0]} — 일봉 OHLC + 예측 종가/고가 (FF5 가용 구간)",
        updatemenus=_dropdown(tickers, 3, "{ticker} — 일봉 OHLC + 예측 종가/고가 (FF5 가용 구간)"),
        xaxis=dict(rangeslider=dict(visible=False), rangebreaks=_RANGEBREAKS),
        height=720, hovermode="x unified", legend=dict(orientation="h", y=1.06),
        margin=dict(t=110),
    )
    path = OUT / "ohlc_predictions.html"
    fig.write_html(str(path), include_plotlyjs=True, full_html=True)
    return path


def build_return_decomposition(data: dict[str, pd.DataFrame]) -> Path:
    tickers = list(data)
    fig = go.Figure()
    for i, ticker in enumerate(tickers):
        d = data[ticker]
        first = i == 0
        fig.add_trace(_candle(d, ticker, first))
        fig.add_trace(go.Scatter(
            x=d["trade_date"], y=d["normal_only_close"], mode="lines",
            line=dict(color="#9e9e9e", width=1.3), name="정상수익률만 예측 종가 (FF5)", visible=first,
        ))
        custom = np.stack([
            d["layer1_ff5_normal_return"].to_numpy(),
            d["layer3_final_abnormal_return"].to_numpy(),
            d["predicted_return"].to_numpy(),
        ], axis=-1)
        fig.add_trace(go.Scatter(
            x=d["trade_date"], y=d["predicted_close_price"], mode="markers+lines",
            line=dict(color="#2e7d32", width=1.0), marker=dict(size=5, color="#2e7d32", symbol="circle"),
            name="정상+뉴스 예측 종가 (합산 지점)", visible=first, customdata=custom,
            hovertemplate=("정상수익률=%{customdata[0]:.4f}<br>뉴스(비정상)=%{customdata[1]:.4f}"
                           "<br>합산수익률=%{customdata[2]:.4f}<br>예측종가=%{y:.2f}<extra></extra>"),
        ))
    fig.update_layout(
        title=f"{tickers[0]} — 정상수익률 예측 + 뉴스수익률 예측 합산 지점",
        updatemenus=_dropdown(tickers, 3, "{ticker} — 정상수익률 예측 + 뉴스수익률 예측 합산 지점"),
        xaxis=dict(rangeslider=dict(visible=False), rangebreaks=_RANGEBREAKS),
        height=720, hovermode="x unified", legend=dict(orientation="h", y=1.06),
        margin=dict(t=110),
    )
    path = OUT / "return_decomposition.html"
    fig.write_html(str(path), include_plotlyjs=True, full_html=True)
    return path


def main() -> int:
    data = _load()
    if not data:
        print("No prediction rows found.")
        return 1
    p1 = build_ohlc_predictions(data)
    p2 = build_return_decomposition(data)
    for ticker, d in data.items():
        print(f"  {ticker}: {len(d)} bars  {d['trade_date'].min().date()}..{d['trade_date'].max().date()}")
    print(f"\nwrote:\n  {p1}\n  {p2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
