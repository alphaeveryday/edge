"""LoadEtfFlow available_at 앞당김 E2E — 실 PostgreSQL upsert WHERE 계약 (ALPHA-1107).

단위 fake 는 WHERE 절을 파이썬으로 흉내 낼 뿐이다. 값이 같은데 available_at 만 앞당겨질 때
`ON CONFLICT … WHERE … OR available_at > EXCLUDED.available_at` 가 실제로 갱신하는지(그리고
늦은 시각으로는 밀지 않는지)는 TIMESTAMPTZ 비교가 도는 PostgreSQL 위에서 확인해야 한다.
"""
from __future__ import annotations

import io
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

INSTRUMENT_ID = "inst_e2e_alpha_1107"
TICKER = "991107"
TRADE_DATE = "2026-07-16"


def _pg_kwargs() -> dict:
    """CI ephemeral PostgreSQL 접속 인자를 반환한다."""
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _write_canonical(storage, fetched_at: str) -> None:
    """같은 순매수 값을 주어진 수집 시각으로 canonical 파티션에 쓴다(기존 part 를 덮는다)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_investor_flow_partition
    from data_pipeline.steps import load_etf_flow

    row = {column: 1 for column in load_etf_flow._NET_COLUMNS}
    row.update({"market": "KR", "ticker": TICKER, "trade_date": TRADE_DATE,
                "currency": "KRW", "source_vendor": "kis", "fetched_at": fetched_at})
    schema = pa.schema([
        ("market", pa.string()), ("ticker", pa.string()), ("trade_date", pa.string()),
        *((column, pa.int64()) for column in load_etf_flow._NET_COLUMNS),
        ("currency", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist([row], schema=schema), buf)
    key = f"{canonical_investor_flow_partition('KR', TRADE_DATE)}/part-00000.parquet"
    storage.put_bytes(key, buf.getvalue())


def _cleanup(cur) -> None:
    """이 테스트가 만든 행만 지운다."""
    cur.execute("DELETE FROM investor_flow_daily WHERE instrument_id = %s", (INSTRUMENT_ID,))
    cur.execute("DELETE FROM instrument WHERE instrument_id = %s", (INSTRUMENT_ID,))
    cur.execute("DELETE FROM entity WHERE entity_id = %s", (INSTRUMENT_ID,))


def test_same_values_with_earlier_available_at_move_the_mart_back_on_real_postgres(tmp_path):
    """D+1 값으로 먼저 적재된 행이 D일 수집분 재정제로 available_at 만 앞당겨지고, 늦은 시각으론 안 밀린다."""
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import load_etf_flow

    pg = _pg_kwargs()
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    storage = LocalStorage(tmp_path / "lake")

    def available_at() -> str:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("SELECT available_at AT TIME ZONE 'UTC' FROM investor_flow_daily"
                        " WHERE instrument_id = %s AND trade_date = %s",
                        (INSTRUMENT_ID, TRADE_DATE))
            [(value,)] = cur.fetchall()
            return value.isoformat()

    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        _cleanup(cur)
        cur.execute("INSERT INTO entity (entity_id, entity_type, display_name)"
                    " VALUES (%s, 'INSTRUMENT', %s)", (INSTRUMENT_ID, TICKER))
        cur.execute("INSERT INTO instrument"
                    " (instrument_id, market_code, ticker, instrument_type, currency_code)"
                    " VALUES (%s, 'XKRX', %s, 'EQUITY', 'KRW')", (INSTRUMENT_ID, TICKER))
    try:
        _write_canonical(storage, "2026-07-17T06:41:00+00:00")
        assert load_etf_flow.run(storage, "e2e-1107-a", db=db) == 0
        assert available_at() == "2026-07-17T06:41:00"

        _write_canonical(storage, "2026-07-16T06:41:00+00:00")   # 옛 raw 재정제 — 값 동일
        assert load_etf_flow.run(storage, "e2e-1107-b", db=db) == 0
        assert available_at() == "2026-07-16T06:41:00"

        _write_canonical(storage, "2026-07-18T06:41:00+00:00")   # 늦은 시각 — 밀지 않는다
        assert load_etf_flow.run(storage, "e2e-1107-c", db=db) == 0
        assert available_at() == "2026-07-16T06:41:00"
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            _cleanup(cur)
