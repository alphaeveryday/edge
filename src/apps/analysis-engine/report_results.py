#!/usr/bin/env python3
"""Summarize + visualize the unified daily_prediction SQLite log.

    python report_results.py [db_path] [out_dir]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DB_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "db" / "edge_analysis.sqlite"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "artifacts"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"figure.dpi": 110, "axes.grid": True, "grid.alpha": 0.3, "font.size": 9})


def load() -> pd.DataFrame:
    con = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query("SELECT * FROM daily_prediction", con)
    con.close()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["high_ret_actual"] = np.log(df["actual_high_price"] / df["prev_close_price"])
    df["high_ret_pred"] = np.log(df["predicted_high_price"] / df["prev_close_price"])
    df["close_abs_err"] = (df["predicted_close_price"] - df["actual_close_price"]).abs()
    df["close_pct_err"] = df["close_abs_err"] / df["actual_close_price"]
    df["high_pct_err"] = (df["predicted_high_price"] - df["actual_high_price"]).abs() / df["actual_high_price"]
    return df


def _metrics(g: pd.DataFrame) -> dict:
    real = g["actual_return"].to_numpy()
    pred = g["predicted_return"].to_numpy()
    base = g["layer1_ff5_normal_return"].to_numpy()
    return {
        "rows": len(g),
        "news_days": int((g["news_count"] > 0).sum()),
        "close_MAE": float(np.mean(np.abs(pred - real))),
        "close_MAE_base": float(np.mean(np.abs(base - real))),
        "close_dir_acc": float(np.mean(np.sign(pred) == np.sign(real))),
        "high_MAE_ret": float(np.mean(np.abs(g["high_ret_actual"] - g["high_ret_pred"]))),
        "close_pct_err_med": float(g["close_pct_err"].median()),
        "high_pct_err_med": float(g["high_pct_err"].median()),
        "mean_close_conf": float(g["close_confidence"].mean()),
        "calib_pass": float(g["calibration_pass"].mean()) if g["calibration_pass"].notna().any() else float("nan"),
    }


def summarize(df: pd.DataFrame) -> str:
    lines = ["# daily_prediction — Result Summary", ""]
    lines.append(f"- rows: **{len(df)}**, tickers: **{df['asset_code'].nunique()}**, "
                 f"dates: **{df['trade_date'].min().date()} → {df['trade_date'].max().date()}**")
    lines.append(f"- news-day coverage: **{(df['news_count'] > 0).mean():.1%}**; "
                 f"event candidates: **{int(df['is_event'].sum())}**")
    lines.append("\n## Metrics by split (close-return, log)")
    lines.append("| split | rows | close MAE | baseline MAE | dir acc | high MAE | close conf | calib pass |")
    lines.append("|---|--:|--:|--:|--:|--:|--:|--:|")
    for split in ("train", "validation", "test"):
        g = df[df["split"] == split]
        if g.empty:
            continue
        m = _metrics(g)
        lines.append(f"| {split} | {m['rows']} | {m['close_MAE']:.5f} | {m['close_MAE_base']:.5f} | "
                     f"{m['close_dir_acc']:.3f} | {m['high_MAE_ret']:.5f} | {m['mean_close_conf']:.3f} | {m['calib_pass']:.3f} |")
    lines.append("\n## Per-ticker (test split)")
    lines.append("| ticker | sector | rows | close MAE | dir acc | close %err(med) | high %err(med) |")
    lines.append("|---|---|--:|--:|--:|--:|--:|")
    for ticker, g in df[df["split"] == "test"].groupby("asset_code"):
        m = _metrics(g)
        lines.append(f"| {ticker} | {g['sector'].iloc[0]} | {m['rows']} | {m['close_MAE']:.5f} | "
                     f"{m['close_dir_acc']:.3f} | {m['close_pct_err_med']:.4%} | {m['high_pct_err_med']:.4%} |")
    return "\n".join(lines)


def make_plots(df: pd.DataFrame) -> list[Path]:
    paths: list[Path] = []
    test = df[df["split"] == "test"]

    for fname, pcol, acol, title in (
        ("01_close_pred_vs_actual.png", "predicted_close_price", "actual_close_price", "Close: predicted vs actual (test)"),
        ("02_high_pred_vs_actual.png", "predicted_high_price", "actual_high_price", "High: predicted vs actual (test)"),
    ):
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(test[acol], test[pcol], s=6, alpha=0.4)
        lim = [test[acol].min(), test[acol].max()]
        ax.plot(lim, lim, "r--", lw=1, label="y=x")
        ax.set_xlabel(acol); ax.set_ylabel(pcol); ax.set_title(title); ax.legend()
        fig.tight_layout(); fig.savefig(OUT / fname); plt.close(fig)
        paths.append(OUT / fname)

    rows = []
    for ticker, g in test.groupby("asset_code"):
        m = _metrics(g)
        rows.append((ticker, m["close_dir_acc"], m["close_MAE"], m["close_MAE_base"]))
    rows.sort(key=lambda r: r[1], reverse=True)
    tickers = [r[0] for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    a1.bar(tickers, [r[1] for r in rows], color="steelblue")
    a1.axhline(0.5, color="r", ls="--", lw=1, label="coin flip"); a1.set_ylim(0, 1)
    a1.set_title("Close direction accuracy by ticker (test)"); a1.legend()
    x = np.arange(len(tickers))
    a2.bar(x - 0.2, [r[2] for r in rows], 0.4, label="model", color="seagreen")
    a2.bar(x + 0.2, [r[3] for r in rows], 0.4, label="normal-only", color="gray")
    a2.set_xticks(x); a2.set_xticklabels(tickers); a2.set_title("Close MAE: model vs baseline"); a2.legend()
    fig.tight_layout(); fig.savefig(OUT / "03_per_ticker_metrics.png"); plt.close(fig)
    paths.append(OUT / "03_per_ticker_metrics.png")

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(test["close_confidence"], bins=40, alpha=0.6, label="close conf")
    ax.hist(test["high_confidence"], bins=40, alpha=0.6, label="high conf")
    ax.set_title("Confidence distribution (test)"); ax.set_xlabel("confidence [0,1]"); ax.legend()
    fig.tight_layout(); fig.savefig(OUT / "04_confidence_hist.png"); plt.close(fig)
    paths.append(OUT / "04_confidence_hist.png")

    g = df[(df["split"] == "test") & (df["news_count"] > 0)]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(g["layer3_final_abnormal_return"], g["actual_abnormal_return"], s=8, alpha=0.4)
    lim = [g["actual_abnormal_return"].min(), g["actual_abnormal_return"].max()]
    ax.plot(lim, lim, "r--", lw=1)
    ax.set_xlabel("predicted abnormal (NN)"); ax.set_ylabel("actual abnormal")
    ax.set_title("Predicted vs actual abnormal return (test)")
    fig.tight_layout(); fig.savefig(OUT / "05_abnormal_pred_vs_actual.png"); plt.close(fig)
    paths.append(OUT / "05_abnormal_pred_vs_actual.png")

    top = df.groupby("asset_code")["news_count"].sum().idxmax()
    g = df[df["asset_code"] == top].sort_values("trade_date")
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(g["trade_date"], g["actual_close_price"], label="actual close", lw=1)
    ax.plot(g["trade_date"], g["predicted_close_price"], label="predicted close", lw=1, alpha=0.8)
    ax.set_title(f"{top}: actual vs predicted close"); ax.legend()
    fig.tight_layout(); fig.savefig(OUT / "06_timeseries_close.png"); plt.close(fig)
    paths.append(OUT / "06_timeseries_close.png")

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    a1.hist(test["return_error"].dropna().abs(), bins=40, color="indianred")
    a1.axvline(0.02, color="k", ls="--", label="2% gate"); a1.set_title("|return_error| (test)"); a1.legend()
    pr = test.groupby("asset_code")["calibration_pass"].mean().sort_values()
    a2.barh(pr.index, pr.values, color="mediumpurple"); a2.set_xlim(0, 1)
    a2.set_title("Calibration pass rate by ticker")
    fig.tight_layout(); fig.savefig(OUT / "07_calibration.png"); plt.close(fig)
    paths.append(OUT / "07_calibration.png")
    return paths


def main() -> int:
    df = load()
    summary = summarize(df)
    (OUT / "summary.md").write_text(summary, encoding="utf-8")
    paths = make_plots(df)
    print(summary)
    print("\n## Charts")
    for p in paths:
        print(f"- {p.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
