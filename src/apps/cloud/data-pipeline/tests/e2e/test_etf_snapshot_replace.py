"""ALPHA-1059: raw 축소 정정이 실제 DB와 트리거 입력에서도 삭제로 이어져야 한다."""
import hashlib
import json
import os

import pytest

from data_pipeline.config import DbConfig
from data_pipeline.lake import LocalStorage, collection_log_key, latest_good_pointer_key
from data_pipeline.steps import load_etf_holdings, load_price_triggers, normalize_etf

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"), reason="ephemeral Postgres 필요(E2E_PGHOST 미설정)",
)

ETF, A, B = "inst_e2e_1059_etf", "inst_e2e_1059_a", "inst_e2e_1059_b"
DAY = "2026-08-31"


def _raw(storage, run_id, weights, *, status="success", malformed=False):
    rows = [{
        "our_etf_id": "991059", "market": "KR", "trd_dd": "20260831",
        "COMPST_ISU_CD": ticker, "COMPST_RTO": str(weight),
        "MKT_ID": "STK", "SECUGRP_ID": "ST",
        "fetched_at": f"2026-08-31T0{run_id[-1]}:00:00+00:00",
    } for ticker, weight in weights]
    if malformed:
        rows.append({**rows[0], "COMPST_ISU_CD": ""})
    key = (f"raw/source=krx/dataset=etf_holdings/market=KR/ingest_date={DAY}"
           f"/run_id={run_id}/part-00000.ndjson")
    data = "".join(json.dumps(row) + "\n" for row in rows).encode()
    storage.put_bytes(key, data)
    storage.put_bytes(collection_log_key("krx", "etf_holdings", DAY, run_id), json.dumps({
        "run_id": run_id, "source_vendor": "krx", "status": status,
        "records_fetched": len(rows), "records_saved": len(rows),
        "records_failed_etfs": int(status != "success"),
        "raw_sha256": {key: hashlib.sha256(data).hexdigest()},
    }).encode())


def test_raw_correction_and_partial_preservation_reach_trigger_input(tmp_path):
    """정규화를 우회하면 A+B→A 버그를 놓친다. 실 SQL version 필터까지 검증한다."""
    import psycopg

    pg = dict(host=os.environ["E2E_PGHOST"], port=int(os.getenv("E2E_PGPORT", "5432")),
              dbname=os.getenv("E2E_PGDATABASE", "edge"), user=os.getenv("E2E_PGUSER", "edge"),
              password=os.getenv("E2E_PGPASSWORD", "edge"))
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    storage = LocalStorage(tmp_path / "lake")

    def cleanup():
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM etf_holding_snapshot WHERE etf_instrument_id = %s", (ETF,))
            cur.execute("DELETE FROM etf_holding_snapshot_status WHERE etf_instrument_id = %s", (ETF,))
            cur.execute("DELETE FROM etf_profile WHERE instrument_id = %s", (ETF,))
            cur.execute("DELETE FROM instrument WHERE instrument_id = ANY(%s)", ([ETF, A, B],))
            cur.execute("DELETE FROM entity WHERE entity_id = ANY(%s)", ([ETF, A, B],))

    def current():
        with psycopg.connect(**pg) as conn:
            return load_price_triggers._latest_good_holdings(conn, [ETF], DAY)

    cleanup()
    try:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            for ident, ticker, kind in ((ETF, "991059", "ETF"), (A, "991057", "EQUITY"),
                                        (B, "991058", "EQUITY")):
                cur.execute("INSERT INTO entity (entity_id, entity_type, display_name)"
                            " VALUES (%s, 'INSTRUMENT', %s)", (ident, ticker))
                cur.execute("INSERT INTO instrument (instrument_id, market_code, ticker,"
                            " instrument_type, currency_code) VALUES (%s, 'XKRX', %s, %s, 'KRW')",
                            (ident, ticker, kind))
        _raw(storage, "R1", [("991057", 60), ("991058", 40)])
        assert normalize_etf.run(storage, "N1", input_run_id="R1") == 0
        assert load_etf_holdings.run(storage, "N1", db=db, input_run_id="N1") == 0
        assert dict(current()[ETF][1]) == {A: .6, B: .4}

        _raw(storage, "R2", [("991057", 100)])
        assert normalize_etf.run(storage, "N2", input_run_id="R2") == 0
        assert load_etf_holdings.run(storage, "N2", db=db, input_run_id="N2") == 0
        assert current() == {ETF: (DAY, [(A, 1.0)])}
        pointer = storage.get_bytes(latest_good_pointer_key("etf_holdings", "KR"))

        # 부분 수집과 행 탈락 모두 기존 값 및 DB version을 보존해야 한다.
        for run_id, status, malformed in (("R3", "partial", False), ("R4", "success", True)):
            _raw(storage, run_id, [("991057", 20)], status=status, malformed=malformed)
            norm_id = "N" + run_id[-1]
            assert normalize_etf.run(storage, norm_id, input_run_id=run_id) == 2
            assert load_etf_holdings.run(storage, norm_id, db=db, input_run_id=norm_id) == 0
            assert current() == {ETF: (DAY, [(A, 1.0)])}
            assert storage.get_bytes(latest_good_pointer_key("etf_holdings", "KR")) == pointer
            with psycopg.connect(**pg) as conn, conn.cursor() as cur:
                cur.execute("SELECT data_version FROM etf_holding_snapshot_status"
                            " WHERE etf_instrument_id = %s", (ETF,))
                assert cur.fetchone() == ("N2",)
    finally:
        cleanup()
