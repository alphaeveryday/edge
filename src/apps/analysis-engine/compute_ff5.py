#!/usr/bin/env python3
"""Compute US Fama-French 5 factors from FMP over the FULL NASDAQ common-stock
universe (faithful), no Kenneth-French dependency.

Factor universe : ALL active NASDAQ common stocks (ETFs/funds excluded).
Methodology     : annual end-June rebalance, breakpoints WITHIN the universe,
                  value-weighted 2x3 sorts (FF5 standard):
  Mkt-RF = VW(universe) - rf
  SMB    = mean(small legs of BM/OP/INV) - mean(big legs)
  HML    = 1/2(SH+BH) - 1/2(SL+BL)        B/M = BE_{t-1}/ME_{Dec t-1}
  RMW    = 1/2(SR+BR) - 1/2(SW+BW)        OP  = operatingIncome_{t-1}/BE_{t-1}
  CMA    = 1/2(SC+BC) - 1/2(SA+BA)        INV = dAssets_{t-1}
  rf     = treasury 1-month / 252 (decimal daily)
Returns from adjClose; ME = close * weightedAverageShsOut(FY t-1).

Fundamentals via annual bulk CSV (few calls); prices per-symbol full history.
Raw pulls cached under data/ff5_build/. Output: data/ff5_build/us_ff5_computed.parquet (decimals).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "ff5_build"
OUT.mkdir(parents=True, exist_ok=True)
KEY = json.loads(os.environ["FMPSECRET"])["apikey"] if "FMPSECRET" in os.environ else os.environ["FMP_API_KEY"]
BASE = "https://financialmodelingprep.com/stable"
START = "2021-01-01"
END = os.environ.get("FF5_END", "2026-06-22")
FUND_YEARS = list(range(2019, 2027))
WORKERS = 12
_SYM_RE = re.compile(r"^[A-Z]{1,5}$")


def _raw(path: str) -> str:
    u = f"{BASE}/{path}{'&' if '?' in path else '?'}apikey={KEY}"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(u, timeout=90) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(1.0 * (attempt + 1)); continue
            return ""
        except Exception:
            time.sleep(0.8 * (attempt + 1))
    return ""


def _js(path: str):
    t = _raw(path)
    try:
        return json.loads(t)
    except Exception:
        return []


def fetch_universe() -> list[str]:
    d = _js("company-screener?exchange=NASDAQ&isActivelyTrading=true&isEtf=false&isFund=false&limit=10000")
    meta = {}
    for x in d:
        s = x.get("symbol")
        if s and _SYM_RE.match(s) and x.get("sector") and (x.get("price") or 0) > 0 and (x.get("marketCap") or 0) > 0:
            meta[s] = x["marketCap"]
    pd.DataFrame([{"sym": s, "mktcap": v} for s, v in sorted(meta.items())]).to_parquet(OUT / "universe.parquet", index=False)
    return sorted(meta)


def fetch_prices(syms: list[str]) -> pd.DataFrame:
    def one(s):
        d = _js(f"historical-price-eod/full?symbol={s}&from={START}&to={END}")
        return [(s, r["date"], r.get("close")) for r in d if r.get("close")]
    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(one, s): s for s in syms}
        done = 0
        for f in as_completed(futs):
            try:
                rows += f.result() or []
            except Exception:
                pass
            done += 1
            if done % 500 == 0:
                print(f"  prices {done}/{len(syms)}", flush=True)
    px = pd.DataFrame(rows, columns=["sym", "date", "close"])
    px["date"] = pd.to_datetime(px["date"])
    px.to_parquet(OUT / "prices.parquet", index=False)
    return px


def fetch_fundamentals(universe: set[str]) -> pd.DataFrame:
    def bulk(stmt, year):
        txt = _raw(f"{stmt}?year={year}&period=annual")
        if not txt:
            return []
        return list(csv.DictReader(io.StringIO(txt)))
    inc_rows, bal_rows = {}, {}
    for y in FUND_YEARS:
        for r in bulk("income-statement-bulk", y):
            s = r.get("symbol")
            if s in universe and r.get("fiscalYear"):
                inc_rows[(s, r["fiscalYear"])] = r
        for r in bulk("balance-sheet-statement-bulk", y):
            s = r.get("symbol")
            if s in universe and r.get("fiscalYear"):
                bal_rows[(s, r["fiscalYear"])] = r
        print(f"  fundamentals year {y}: inc={len(inc_rows)} bal={len(bal_rows)}", flush=True)

    def fnum(d, k):
        try:
            return float(d.get(k)) if d.get(k) not in (None, "") else None
        except Exception:
            return None
    rows = []
    for (s, fy), b in bal_rows.items():
        i = inc_rows.get((s, fy), {})
        be = (fnum(b, "totalStockholdersEquity") or 0) - (fnum(b, "preferredStock") or 0)
        rows.append({"sym": s, "fy": int(fy), "be": be, "op_income": fnum(i, "operatingIncome"),
                     "assets": fnum(b, "totalAssets"), "shares": fnum(i, "weightedAverageShsOut")})
    fund = pd.DataFrame(rows)
    fund.to_parquet(OUT / "fundamentals.parquet", index=False)
    return fund


def fetch_rf() -> pd.Series:
    t = pd.DataFrame(_js(f"treasury-rates?from={START}&to={END}"))
    t["date"] = pd.to_datetime(t["date"])
    return (t.sort_values("date").set_index("date")["month1"].astype(float) / 100.0) / 252.0


def _asof(s: pd.Series, when: pd.Timestamp):
    s = s[s.index <= when]
    return float(s.iloc[-1]) if len(s) else None


def compute_ff5(px: pd.DataFrame, fund: pd.DataFrame, rf: pd.Series, shares_map: dict) -> pd.DataFrame:
    px = px.sort_values(["sym", "date"])
    px["ret"] = px.groupby("sym")["close"].pct_change().clip(-0.5, 0.5)
    ret_wide = px.pivot_table(index="date", columns="sym", values="ret")
    close_by = {s: g.set_index("date")["close"].sort_index() for s, g in px.groupby("sym")}
    fund = fund.dropna(subset=["fy"]).copy(); fund["fy"] = fund["fy"].astype(int)
    fund_by = {s: g.set_index("fy").sort_index() for s, g in fund.groupby("sym")}
    all_dates = ret_wide.index
    factor_rows = []

    for Y in range(2021, 2027):
        june, dec_prev = pd.Timestamp(Y, 6, 30), pd.Timestamp(Y - 1, 12, 31)
        recs = []
        for s in ret_wide.columns:
            fb = fund_by.get(s)
            cb = close_by.get(s)
            if fb is None or cb is None or (Y - 1) not in fb.index:
                continue
            r = fb.loc[Y - 1]
            be, assets, op = r["be"], r["assets"], r["op_income"]
            mc = shares_map.get(s)
            assets_prev = fb.loc[Y - 2, "assets"] if (Y - 2) in fb.index else None
            if not be or be <= 0 or not mc or mc < 5e7:
                continue
            c_jun, c_dec = _asof(cb, june), _asof(cb, dec_prev)
            if not c_jun or not c_dec:
                continue
            me_jun, me_dec = mc, mc
            inv = ((assets - assets_prev) / assets_prev) if (assets and assets_prev and assets_prev > 0) else np.nan
            recs.append({"sym": s, "me": me_jun, "bm": be / me_dec,
                         "op": (op / be) if op is not None else np.nan, "inv": inv})
        if not recs:
            continue
        u = pd.DataFrame(recs).dropna(subset=["me", "bm"])
        if len(u) < 50:
            continue
        u["sz"] = np.where(u["me"] <= u["me"].median(), "S", "B")
        for col in ("bm", "op", "inv"):
            lo, hi = u[col].quantile(0.3), u[col].quantile(0.7)
            u[col + "_g"] = np.where(u[col] <= lo, "L", np.where(u[col] >= hi, "H", "M"))

        start, stop = pd.Timestamp(Y, 7, 1), pd.Timestamp(Y + 1, 6, 30)
        window = all_dates[(all_dates >= start) & (all_dates <= stop)]
        if not len(window):
            continue
        members = [s for s in u["sym"] if s in ret_wide.columns]
        u = u.set_index("sym").loc[members]
        R = ret_wide.loc[window, members]
        w = u["me"]

        def vw(mask) -> pd.Series:
            cols = list(mask.index[mask.values])
            if not cols:
                return pd.Series(0.0, index=window)
            ww = w[cols]
            return (R[cols].mul(ww, axis=1)).sum(axis=1) / ww.sum()

        sz = u["sz"]
        def leg(col, lab):
            g = u[col]
            return vw((sz == "S") & (g == lab)), vw((sz == "B") & (g == lab))
        SH, BH = leg("bm_g", "H"); SL, BL = leg("bm_g", "L")
        SR, BR = leg("op_g", "H"); SW, BW = leg("op_g", "L")
        SC, BC = leg("inv_g", "L"); SA, BA = leg("inv_g", "H")
        hml = 0.5 * (SH + BH) - 0.5 * (SL + BL)
        rmw = 0.5 * (SR + BR) - 0.5 * (SW + BW)
        cma = 0.5 * (SC + BC) - 0.5 * (SA + BA)
        smb = (SH + SL + SR + SW + SC + SA) / 6.0 - (BH + BL + BR + BW + BC + BA) / 6.0
        mkt = (R.mul(w, axis=1)).sum(axis=1) / w.sum()
        factor_rows.append(pd.DataFrame({"mkt": mkt, "smb": smb, "hml": hml, "rmw": rmw, "cma": cma}, index=window))

    ff = pd.concat(factor_rows).sort_index()
    ff = ff[~ff.index.duplicated(keep="last")].join(rf.rename("rf"), how="left")
    ff["rf"] = ff["rf"].ffill().fillna(0.0)
    ff["mkt_rf"] = ff["mkt"] - ff["rf"]
    out = ff.rename_axis("trade_date").reset_index()
    return out[["trade_date", "mkt_rf", "smb", "hml", "rmw", "cma", "rf"]].dropna(subset=["mkt_rf"])


def validate(ff: pd.DataFrame):
    import glob
    fr = sorted(glob.glob(str(ROOT / "data" / "analysis_outputs" / "us_ff5_public_daily_*.parquet")))
    if not fr:
        print("  (no French parquet to compare)"); return
    f = pd.read_parquet(fr[-1]); f["trade_date"] = pd.to_datetime(f["trade_date"])
    m = ff.assign(trade_date=pd.to_datetime(ff["trade_date"])).merge(f, on="trade_date", suffixes=("_o", "_f"))
    print(f"  overlap={len(m)} (NASDAQ-only ours vs all-US French; mkt corr high, styles moderate expected)")
    for c in ("mkt_rf", "smb", "hml", "rmw", "cma"):
        if f"{c}_f" in m and len(m) > 10:
            print(f"    {c}: corr={np.corrcoef(m[f'{c}_o'], m[f'{c}_f'])[0,1]:+.3f}  std ours={m[f'{c}_o'].std():.4f} fr={m[f'{c}_f'].std():.4f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute US (NASDAQ-universe) FF5 from FMP")
    ap.add_argument("--use-cache", action="store_true")
    args = ap.parse_args(argv)
    syms = fetch_universe()
    print(f"NASDAQ common-stock universe: {len(syms)} symbols", flush=True)
    um = pd.read_parquet(OUT / "universe.parquet"); shares_map = dict(zip(um["sym"], um["mktcap"]))
    fund_path = OUT / "fundamentals.parquet"
    if args.use_cache and fund_path.exists():
        fund = pd.read_parquet(fund_path); print(f"  fundamentals cache: {len(fund)} rows", flush=True)
    else:
        fund = fetch_fundamentals(set(syms))
    px_path = OUT / "prices.parquet"
    if args.use_cache and px_path.exists() and len(pd.read_parquet(px_path)):
        px = pd.read_parquet(px_path); print(f"  prices cache: {len(px)} rows", flush=True)
    else:
        px = fetch_prices(syms)
    print(f"fetched: price rows={len(px)} syms={px['sym'].nunique()} fund rows={len(fund)}", flush=True)
    rf = fetch_rf()
    ff = compute_ff5(px, fund, rf, shares_map)
    ff.to_parquet(OUT / "us_ff5_computed.parquet", index=False)
    print(f"\nFF5: {len(ff)} days {ff['trade_date'].min().date()} -> {ff['trade_date'].max().date()}")
    print(ff.tail(3).to_string(index=False))
    print("\n=== validation vs French (all-US) ===")
    validate(ff)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
