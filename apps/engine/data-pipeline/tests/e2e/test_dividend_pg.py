"""Real PostgreSQL: a correction replaces the read model, never the audit trail."""
import os
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DIVIDEND_TEST_DSN"), reason="isolated test PostgreSQL required")
ROOT = Path(__file__).resolve().parents[5]


def test_versions_null_cash_and_existing_identity_are_verified_in_postgresql():
    import psycopg
    from psycopg import sql

    schema = "dividend_test_" + uuid4().hex
    with psycopg.connect(os.environ["DIVIDEND_TEST_DSN"], autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            conn.execute("CREATE TABLE instrument(instrument_id text PRIMARY KEY, instrument_type text, ticker text, market_code text, UNIQUE(market_code,ticker))")
            conn.execute("INSERT INTO instrument VALUES ('existing-id','ETF','069500','XKRX')")
            conn.execute("CREATE TABLE etf_profile(instrument_id text PRIMARY KEY REFERENCES instrument)")
            conn.execute("CREATE TABLE equity_profile(instrument_id text PRIMARY KEY REFERENCES instrument)")
            conn.execute("INSERT INTO etf_profile VALUES ('existing-id')")
            migration = ROOT / "libs/schema/migrations-cloud/V202610181200__add_cash_distribution_observations.sql"
            with conn.transaction():
                conn.execute(migration.read_text(encoding="utf-8"))
            # Keep the SQL unchanged except for isolated test namespaces.
            view = ROOT / "libs/ontology/src/edge_ontology/sql/views/cash_distribution.sql"
            conn.execute(view.read_text(encoding="utf-8").replace("ontology_view.", schema + ".").replace("public.", schema + "."))
            insert = """INSERT INTO cash_distribution_observation
                (market_code,ticker,source_vendor,source_event_key,record_date,pay_date,amount_per_share,
                 currency,distribution_kind,confirmation,received_at,available_at,availability_basis,
                 raw_run_id,raw_key,raw_sha256,canonical_run_id,artifact_key,artifact_sha256)
                VALUES ('XKRX','069500','kis','event','2026-07-31','2026-08-04',%s,
                        'KRW','cash',%s,%s,%s,'received',%s,'raw',%s,'canonical','artifact',%s)
                ON CONFLICT DO NOTHING"""
            for amount, status, run, timestamp in [
                (183, 'published', 'first', '2026-08-01T00:00:00Z'),
                (200, 'published', 'corrected', '2026-08-02T00:00:00Z'),
                (183, 'published', 'first', '2026-08-01T00:00:00Z'),
                (None, 'cancelled', 'cancelled', '2026-08-03T00:00:00Z'),
            ]:
                conn.execute(insert, (amount, status, timestamp, timestamp, run, 'a'*64, 'b'*64))
            assert conn.execute("SELECT count(*) FROM cash_distribution_observation").fetchone()[0] == 3
            assert conn.execute("SELECT amount_per_share,confirmation FROM cash_distribution_latest").fetchone() == (None, 'cancelled')
            obj = conn.execute("SELECT instrument_id,etf_instrument_id,equity_instrument_id,raw_run_id FROM cash_distribution").fetchone()
            assert obj == ('existing-id','existing-id',None,'cancelled')
            assert conn.execute("SELECT count(*),count(DISTINCT id) FROM cash_distribution").fetchone() == (1,1)
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(insert, ('NaN', 'published', '2026-08-04', '2026-08-04', 'bad', 'a'*64, 'b'*64))
            conn.execute(insert.replace("'069500'", "'360750'"),
                         (200, 'published', '2026-08-04', '2026-08-04', 'unmapped', 'a'*64, 'b'*64))
            # A missing master must neither erase the observation nor create a fake link.
            assert conn.execute("SELECT instrument_id FROM cash_distribution WHERE ticker='360750'").fetchone() == (None,)
            conn.execute("INSERT INTO instrument VALUES ('existing-tiger','ETF','360750','XKRX')")
            assert conn.execute("SELECT etf_instrument_id FROM cash_distribution WHERE ticker='360750'").fetchone() == (None,)
            conn.execute("INSERT INTO etf_profile VALUES ('existing-tiger')")
            assert conn.execute("SELECT etf_instrument_id FROM cash_distribution WHERE ticker='360750'").fetchone() == ('existing-tiger',)
        finally:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
