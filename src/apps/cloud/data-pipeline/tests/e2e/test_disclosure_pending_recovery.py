"""LoadDisclosure bootstrap 회수 E2E — 실 PostgreSQL 실패 잔존 계약 (ALPHA-1055).

단위 테스트의 fake connection은 SAVEPOINT 뒤 실제 transaction abort 상태와 별도 connection
commit 경계를 증명하지 못한다. canonical bootstrap이 pending에 먼저 commit된 뒤 typed insert가
실패해도 잔존하고, canonical을 다시 읽지 않는 pending-only 실행이 회수하는지를 실물로 검사한다.
"""
from __future__ import annotations

import io
import hashlib
import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

RCEPT_NO = "e2e-disclosure-pending-1055"
REPORT_DATE = "2026-06-30"


def _pg_kwargs() -> dict:
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _write_canonical(storage) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_supply_contract_fact_partition

    row = {
        "rcept_no": RCEPT_NO, "source_vendor": "dart", "corp_code": "00126380",
        "ticker": "005930", "corp_name": "삼성전자", "counterparty": "고객사",
        "counterparty_raw": "고객사", "counterparty_withheld": False,
        "object": "반도체 공급", "amount_krw": 1000000, "ratio_pct": 12.5,
        "contract_start": "2026-01-01", "contract_end": "2026-12-31",
        "confidence": "high", "report_date": REPORT_DATE,
        "source_url": "https://dart.fss.or.kr/e2e/1055", "parser_version": "e2e-v1",
        "fetched_at": "2026-07-16T01:00:00+00:00",
    }
    schema = pa.schema([
        ("rcept_no", pa.string()), ("source_vendor", pa.string()),
        ("corp_code", pa.string()), ("ticker", pa.string()), ("corp_name", pa.string()),
        ("counterparty", pa.string()), ("counterparty_raw", pa.string()),
        ("counterparty_withheld", pa.bool_()), ("object", pa.string()),
        ("amount_krw", pa.int64()), ("ratio_pct", pa.float64()),
        ("contract_start", pa.string()), ("contract_end", pa.string()),
        ("confidence", pa.string()), ("report_date", pa.string()),
        ("source_url", pa.string()), ("parser_version", pa.string()),
        ("fetched_at", pa.string()),
    ])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist([row], schema=schema), buf)
    storage.put_bytes(
        f"{canonical_supply_contract_fact_partition(REPORT_DATE)}/part-00000.parquet",
        buf.getvalue(),
    )


def _write_run_manifests(storage, run_id, *, supply=True):
    """장중/배치 producer의 완료 manifest를 동일 공시로 만든다."""
    from data_pipeline.lake import (
        canonical_run_manifest_key, canonical_run_partition_key,
        canonical_supply_contract_fact_partition,
    )

    for dataset, producer in (
        ("supply_contract_fact", "normalize_disclosure"),
        ("business_segment_fact", "normalize_disclosure_segment"),
    ):
        partitions = []
        if supply and dataset == "supply_contract_fact":
            data = storage.get_bytes(
                f"{canonical_supply_contract_fact_partition(REPORT_DATE)}/part-00000.parquet")
            key = canonical_run_partition_key(dataset, run_id, REPORT_DATE)
            storage.put_bytes(key, data)
            partitions.append({
                "report_date": REPORT_DATE, "key": key,
                "sha256": hashlib.sha256(data).hexdigest(),
                "winner_ids": [{"rcept_no": RCEPT_NO}],
            })
        storage.put_bytes(canonical_run_manifest_key(dataset, run_id), json.dumps({
            "run_id": run_id, "producer": producer, "canonical_written": True,
            "canonical_partitions": partitions,
        }).encode())


@pytest.mark.parametrize("ingest_path", ["bootstrap", "minute_manifest"])
def test_failed_ingest_survives_until_pending_or_closing_batch_recovers(
    tmp_path, monkeypatch, ingest_path,
):
    """장중 적재 실패분은 날짜가 지난 뒤 신규 0건인 배치에서도 회수돼야 한다.

    같은 공시를 배치가 재관측해도 DB fact는 하나로 수렴해야 한다. bootstrap 복구도 유지한다.
    """
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.db import stable_domain_id
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import load_disclosure

    pg = _pg_kwargs()
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    document_id = stable_domain_id("doc", "dart", RCEPT_NO)
    storage = LocalStorage(tmp_path / "lake")
    _write_canonical(storage)

    with psycopg.connect(**pg) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM supply_contract_fact WHERE fact_id IN"
                        " (SELECT fact_id FROM disclosure_fact WHERE document_id=%s)",
                        (document_id,))
            cur.execute("DELETE FROM disclosure_fact WHERE document_id=%s", (document_id,))
            cur.execute("DELETE FROM disclosure_document WHERE document_id=%s", (document_id,))
            cur.execute("DELETE FROM document WHERE document_id=%s", (document_id,))
            cur.execute("DELETE FROM disclosure_load_pending WHERE rcept_no=%s", (RCEPT_NO,))
            cur.execute("""
                CREATE OR REPLACE FUNCTION e2e_fail_disclosure_1055() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                  IF NEW.source_document_id = 'e2e-disclosure-pending-1055' THEN
                    RAISE EXCEPTION 'e2e transient disclosure failure';
                  END IF;
                  RETURN NEW;
                END $$
            """)
            cur.execute("DROP TRIGGER IF EXISTS e2e_fail_disclosure_1055 ON document")
            cur.execute("CREATE TRIGGER e2e_fail_disclosure_1055 BEFORE INSERT ON document"
                        " FOR EACH ROW EXECUTE FUNCTION e2e_fail_disclosure_1055()")

    try:
        if ingest_path == "minute_manifest":
            _write_run_manifests(storage, "mdw-e2e-1073")
        ingest_args = ({"bootstrap": True} if ingest_path == "bootstrap" else
                       {"input_run_id": "mdw-e2e-1073",
                        "from_date": REPORT_DATE, "to_date": REPORT_DATE})
        assert load_disclosure.run(
            storage, "e2e-ingest-1073", db=db, **ingest_args) == 2
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("SELECT attempt_count, last_error_code FROM disclosure_load_pending"
                        " WHERE rcept_no=%s", (RCEPT_NO,))
            assert cur.fetchone() == (1, "load_error")
            cur.execute("DROP TRIGGER e2e_fail_disclosure_1055 ON document")
            cur.execute("DROP FUNCTION e2e_fail_disclosure_1055()")

        monkeypatch.setattr(
            load_disclosure, "_read_facts",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("canonical read")),
        )
        if ingest_path == "bootstrap":
            assert load_disclosure.run(
                storage, "e2e-pending-only-1055", db=db, pending_only=True) == 0
        else:
            # 배치의 raw 결과가 비어도 완료 manifest를 거쳐 과거 pending을 회수한다.
            _write_run_manifests(storage, "e2e-empty-batch-1073", supply=False)
            assert load_disclosure.run(
                storage, "e2e-empty-batch-1073", db=db,
                input_run_id="e2e-empty-batch-1073") == 0
            with psycopg.connect(**pg) as conn, conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM disclosure_load_pending WHERE rcept_no=%s",
                            (RCEPT_NO,))
                assert cur.fetchone()[0] == 0
                cur.execute("SELECT count(*) FROM disclosure_fact WHERE document_id=%s",
                            (document_id,))
                assert cur.fetchone()[0] == 1
            _write_run_manifests(storage, "e2e-repeat-batch-1073")
            assert load_disclosure.run(
                storage, "e2e-repeat-batch-1073", db=db,
                input_run_id="e2e-repeat-batch-1073") == 0
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM disclosure_load_pending WHERE rcept_no=%s",
                        (RCEPT_NO,))
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT count(*) FROM disclosure_fact WHERE document_id=%s",
                        (document_id,))
            assert cur.fetchone()[0] == 1
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("DROP TRIGGER IF EXISTS e2e_fail_disclosure_1055 ON document")
            cur.execute("DROP FUNCTION IF EXISTS e2e_fail_disclosure_1055()")
