"""LoadEtfNav profile+NAV savepoint E2E — 실 PostgreSQL 격리 계약 (ALPHA-1043)."""
from __future__ import annotations

import hashlib
import io
import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

GOOD_ID, BAD_ID = "inst_e2e_alpha_1043_good", "inst_e2e_alpha_1043_bad"
GOOD_TICKER, BAD_TICKER = "991042", "991043"
TRADE_DATE = "2026-08-31"


def _pg_kwargs() -> dict:
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _write_input(storage) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_run_manifest_key, canonical_run_partition_key

    rows = [{
        "market": "KR", "etf_id": ticker, "trade_date": TRADE_DATE, "nav": 100.0,
        "currency": "KRW", "source_vendor": "kis",
        "fetched_at": "2026-08-31T06:00:00+00:00",
    } for ticker in (GOOD_TICKER, BAD_TICKER)]
    schema = pa.schema([
        ("market", pa.string()), ("etf_id", pa.string()), ("trade_date", pa.string()),
        ("nav", pa.float64()), ("currency", pa.string()),
        ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), buf)
    parquet = buf.getvalue()
    key = canonical_run_partition_key("etf_nav", "e2e-1043", TRADE_DATE)
    storage.put_bytes(key, parquet)
    storage.put_bytes(canonical_run_manifest_key("etf_nav", "e2e-1043"), json.dumps({
        "run_id": "e2e-1043", "producer": "normalize_etf_nav",
        "canonical_written": True,
        "canonical_partitions": [{
            "market": "KR", "trade_date": TRADE_DATE, "key": key,
            "sha256": hashlib.sha256(parquet).hexdigest(),
            "winner_ids": [{"etf_id": GOOD_TICKER}, {"etf_id": BAD_TICKER}],
        }],
    }, sort_keys=True).encode("utf-8"))


def test_one_NAV_SQL_failure_rolls_back_its_profile_only_on_real_postgres(tmp_path):
    """실패 winner의 profile도 rollback되고 다른 winner의 profile+NAV는 commit된다."""
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import load_etf_nav

    pg = _pg_kwargs()
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    storage = LocalStorage(tmp_path / "lake")
    _write_input(storage)

    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM etf_nav_daily WHERE etf_instrument_id = ANY(%s)",
                    ([GOOD_ID, BAD_ID],))
        cur.execute("DELETE FROM etf_profile WHERE instrument_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
        cur.execute("DELETE FROM instrument WHERE instrument_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
        cur.execute("DELETE FROM entity WHERE entity_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
        for instrument_id, ticker in ((GOOD_ID, GOOD_TICKER), (BAD_ID, BAD_TICKER)):
            cur.execute("INSERT INTO entity (entity_id, entity_type, display_name)"
                        " VALUES (%s, 'INSTRUMENT', %s)", (instrument_id, ticker))
            cur.execute("INSERT INTO instrument"
                        " (instrument_id, market_code, ticker, instrument_type, currency_code)"
                        " VALUES (%s, 'XKRX', %s, 'ETF', 'KRW')", (instrument_id, ticker))
        cur.execute("""
            CREATE OR REPLACE FUNCTION e2e_fail_etf_nav_1043() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF NEW.etf_instrument_id = 'inst_e2e_alpha_1043_bad' THEN
                RAISE EXCEPTION 'e2e row failure';
              END IF;
              RETURN NEW;
            END $$
        """)
        cur.execute("DROP TRIGGER IF EXISTS e2e_fail_etf_nav_1043 ON etf_nav_daily")
        cur.execute("CREATE TRIGGER e2e_fail_etf_nav_1043 BEFORE INSERT OR UPDATE"
                    " ON etf_nav_daily FOR EACH ROW EXECUTE FUNCTION e2e_fail_etf_nav_1043()")

    try:
        assert load_etf_nav.run(
            storage, "e2e-load-1043", db=db, input_run_id="e2e-1043",
        ) == 2
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("SELECT etf_instrument_id FROM etf_nav_daily"
                        " WHERE etf_instrument_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
            assert [row[0] for row in cur.fetchall()] == [GOOD_ID]
            cur.execute("SELECT instrument_id FROM etf_profile"
                        " WHERE instrument_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
            assert [row[0] for row in cur.fetchall()] == [GOOD_ID]
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("DROP TRIGGER IF EXISTS e2e_fail_etf_nav_1043 ON etf_nav_daily")
            cur.execute("DROP FUNCTION IF EXISTS e2e_fail_etf_nav_1043()")
            cur.execute("DELETE FROM etf_nav_daily WHERE etf_instrument_id = ANY(%s)",
                        ([GOOD_ID, BAD_ID],))
            cur.execute("DELETE FROM etf_profile WHERE instrument_id = ANY(%s)",
                        ([GOOD_ID, BAD_ID],))
            cur.execute("DELETE FROM instrument WHERE instrument_id = ANY(%s)",
                        ([GOOD_ID, BAD_ID],))
            cur.execute("DELETE FROM entity WHERE entity_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
