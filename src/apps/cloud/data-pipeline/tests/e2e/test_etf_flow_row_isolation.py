"""LoadEtfFlow 행별 savepoint E2E — 실 PostgreSQL 격리 계약 (ALPHA-1041).

단위 fake는 ROLLBACK 문 실행만 증명한다. 한 winner의 SQL 오류 뒤 실제 트랜잭션이 회복돼
다른 winner가 commit되는지는 PostgreSQL 위에서 확인해야 한다.
"""
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

GOOD_ID = "inst_e2e_alpha_1041_good"
BAD_ID = "inst_e2e_alpha_1041_bad"
GOOD_TICKER = "991040"
BAD_TICKER = "991041"
TRADE_DATE = "2026-08-31"


def _pg_kwargs() -> dict:
    """CI ephemeral PostgreSQL 접속 인자를 반환한다."""
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _write_input(storage) -> None:
    """두 winner의 canonical parquet와 completed manifest를 기록한다."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import (
        canonical_investor_flow_partition,
        canonical_run_manifest_key,
    )
    from data_pipeline.steps import load_etf_flow

    rows = []
    for ticker in (GOOD_TICKER, BAD_TICKER):
        row = {column: 1 for column in load_etf_flow._NET_COLUMNS}
        row.update({
            "market": "KR", "ticker": ticker, "trade_date": TRADE_DATE,
            "currency": "KRW", "source_vendor": "kis",
            "fetched_at": "2026-08-31T06:00:00+00:00",
        })
        rows.append(row)
    schema = pa.schema([
        ("market", pa.string()), ("ticker", pa.string()), ("trade_date", pa.string()),
        *((column, pa.int64()) for column in load_etf_flow._NET_COLUMNS),
        ("currency", pa.string()), ("source_vendor", pa.string()),
        ("fetched_at", pa.string()),
    ])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), buf)
    parquet = buf.getvalue()
    key = f"{canonical_investor_flow_partition('KR', TRADE_DATE)}/part-00000.parquet"
    storage.put_bytes(key, parquet)
    storage.put_bytes(canonical_run_manifest_key("investor_flow_daily", "e2e-1041"),
                      json.dumps({
                          "run_id": "e2e-1041", "producer": "normalize_investor",
                          "canonical_written": True,
                          "canonical_partitions": [{
                              "market": "KR", "trade_date": TRADE_DATE, "key": key,
                              "sha256": hashlib.sha256(parquet).hexdigest(),
                              "winner_ids": [
                                  {"ticker": GOOD_TICKER}, {"ticker": BAD_TICKER},
                              ],
                          }],
                      }, sort_keys=True).encode("utf-8"))


def test_one_SQL_failure_rolls_back_only_that_winner_on_real_postgres(tmp_path):
    """한 winner의 trigger 오류 뒤 다른 winner가 commit되고 loader는 exit 2를 반환한다."""
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import load_etf_flow

    pg = _pg_kwargs()
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    storage = LocalStorage(tmp_path / "lake")
    _write_input(storage)

    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM investor_flow_daily WHERE instrument_id = ANY(%s)",
                    ([GOOD_ID, BAD_ID],))
        cur.execute("DELETE FROM instrument WHERE instrument_id = ANY(%s)",
                    ([GOOD_ID, BAD_ID],))
        cur.execute("DELETE FROM entity WHERE entity_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
        for instrument_id, ticker in ((GOOD_ID, GOOD_TICKER), (BAD_ID, BAD_TICKER)):
            cur.execute("INSERT INTO entity (entity_id, entity_type, display_name)"
                        " VALUES (%s, 'INSTRUMENT', %s)", (instrument_id, ticker))
            cur.execute("INSERT INTO instrument"
                        " (instrument_id, market_code, ticker, instrument_type, currency_code)"
                        " VALUES (%s, 'XKRX', %s, 'EQUITY', 'KRW')",
                        (instrument_id, ticker))
        cur.execute("""
            CREATE OR REPLACE FUNCTION e2e_fail_etf_flow_1041() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
              IF NEW.instrument_id = 'inst_e2e_alpha_1041_bad' THEN
                RAISE EXCEPTION 'e2e row failure';
              END IF;
              RETURN NEW;
            END $$
        """)
        cur.execute("DROP TRIGGER IF EXISTS e2e_fail_etf_flow_1041 ON investor_flow_daily")
        cur.execute("CREATE TRIGGER e2e_fail_etf_flow_1041 BEFORE INSERT OR UPDATE"
                    " ON investor_flow_daily FOR EACH ROW"
                    " EXECUTE FUNCTION e2e_fail_etf_flow_1041()")

    try:
        assert load_etf_flow.run(
            storage, "e2e-load-1041", db=db, input_run_id="e2e-1041",
        ) == 2
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("SELECT instrument_id FROM investor_flow_daily"
                        " WHERE instrument_id = ANY(%s) ORDER BY instrument_id",
                        ([GOOD_ID, BAD_ID],))
            assert [row[0] for row in cur.fetchall()] == [GOOD_ID]
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("DROP TRIGGER IF EXISTS e2e_fail_etf_flow_1041 ON investor_flow_daily")
            cur.execute("DROP FUNCTION IF EXISTS e2e_fail_etf_flow_1041()")
            cur.execute("DELETE FROM investor_flow_daily WHERE instrument_id = ANY(%s)",
                        ([GOOD_ID, BAD_ID],))
            cur.execute("DELETE FROM instrument WHERE instrument_id = ANY(%s)",
                        ([GOOD_ID, BAD_ID],))
            cur.execute("DELETE FROM entity WHERE entity_id = ANY(%s)", ([GOOD_ID, BAD_ID],))
