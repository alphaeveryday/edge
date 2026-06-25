#!/usr/bin/env python3
"""Upload computed US FF5 -> RDS ``market.us_ff5_factors`` (through the bastion tunnel).

Env: NEWSDB_HOST/PORT/NAME/USER + (PGPW | RDS_SECRET_FILE), sslmode=require.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

HERE = Path(__file__).resolve().parent
PARQUET = HERE.parent / "data" / "ff5_build" / "us_ff5_computed.parquet"

DDL = """
CREATE SCHEMA IF NOT EXISTS market;
CREATE TABLE IF NOT EXISTS market.us_ff5_factors (
    trade_date date PRIMARY KEY,
    mkt_rf double precision,
    smb    double precision,
    hml    double precision,
    rmw    double precision,
    cma    double precision,
    rf     double precision,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE market.us_ff5_factors IS 'Self-computed daily US (NASDAQ-universe) Fama-French 5 factors (decimal)';
"""

UPSERT = (
    "INSERT INTO market.us_ff5_factors (trade_date,mkt_rf,smb,hml,rmw,cma,rf) VALUES %s "
    "ON CONFLICT (trade_date) DO UPDATE SET "
    "mkt_rf=EXCLUDED.mkt_rf, smb=EXCLUDED.smb, hml=EXCLUDED.hml, "
    "rmw=EXCLUDED.rmw, cma=EXCLUDED.cma, rf=EXCLUDED.rf"
)


def _dsn() -> dict:
    pw = os.environ.get("PGPW")
    if not pw and os.environ.get("RDS_SECRET_FILE"):
        pw = json.loads(Path(os.environ["RDS_SECRET_FILE"]).read_text(encoding="utf-8"))["password"]
    return dict(
        host=os.environ.get("NEWSDB_HOST", "127.0.0.1"),
        port=int(os.environ.get("NEWSDB_PORT", "15433")),
        dbname=os.environ.get("NEWSDB_NAME", "newspipeline"),
        user=os.environ.get("NEWSDB_USER", "pipeline_admin"),
        password=pw, sslmode="require", connect_timeout=30,
    )


def main() -> int:
    df = pd.read_parquet(PARQUET)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    cols = ["trade_date", "mkt_rf", "smb", "hml", "rmw", "cma", "rf"]
    rows = [
        tuple(None if (not isinstance(v, str) and pd.isna(v)) else (v.item() if hasattr(v, "item") else v)
              for v in rec)
        for rec in df[cols].itertuples(index=False, name=None)
    ]
    conn = psycopg2.connect(**_dsn())
    try:
        cur = conn.cursor()
        cur.execute(DDL)
        execute_values(cur, UPSERT, rows, page_size=1000)
        conn.commit()
        cur.execute("SELECT count(*), min(trade_date), max(trade_date) FROM market.us_ff5_factors")
        n, lo, hi = cur.fetchone()
        print(f"us_ff5_factors rows={n} span={lo}->{hi}")
    finally:
        conn.close()
    print(f"uploaded {len(rows)} rows from {PARQUET.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
