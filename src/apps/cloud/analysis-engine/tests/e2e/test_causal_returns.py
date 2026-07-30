"""Causal return derivation — real Postgres contract (ALPHA-629)."""
from __future__ import annotations

import os
from datetime import date

import numpy as np
import psycopg2
import pytest

from edge_analysis.adapters.causal_data import CausalData


pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)


def _pg_kwargs() -> dict:
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def test_null_ledger_returns_still_produce_causal_cohort_and_excess_return():
    """원장의 NULL 파생 컬럼이 최근 코호트·AR을 지우면 안 된다."""
    conn = psycopg2.connect(**_pg_kwargs())
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE price_daily (instrument_id text, trade_date date, close_price double precision)")
            cur.execute("CREATE TEMP TABLE instrument (instrument_id text, ticker text)")
            cur.execute("CREATE TEMP TABLE instrument_classification (instrument_id text, as_of_date date, sector_name text, industry_name text, market_cap double precision, listing_market text)")
            cur.executemany(
                "INSERT INTO price_daily VALUES (%s, %s, %s)",
                [("I_A", date(2026, 7, 28), 100.0), ("I_A", date(2026, 7, 29), 110.0),
                 ("I_B", date(2026, 7, 28), 100.0), ("I_B", date(2026, 7, 29), 90.0)],
            )
            cur.executemany("INSERT INTO instrument VALUES (%s, %s)", [("I_A", "AAA"), ("I_B", "BBB")])
            cur.executemany(
                "INSERT INTO instrument_classification VALUES (%s, %s, %s, %s, %s, %s)",
                [("I_A", date(2026, 7, 28), "Technology", "Semiconductors", None, "KOSPI"),
                 ("I_B", date(2026, 7, 28), "Technology", "Semiconductors", None, "KOSPI")],
            )

        data = CausalData(conn)
        pairs = data.universe("industry_name = 'Semiconductors'", [date(2026, 7, 29)])

        assert pairs == [("I_A", date(2026, 7, 29)), ("I_B", date(2026, 7, 29))]
        assert np.allclose(data.ar(pairs, min_cross=2), [0.1, -0.1])
    finally:
        conn.close()
