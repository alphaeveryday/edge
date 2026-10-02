"""LoadPriceDaily available_at 앞당김 E2E — 실 PostgreSQL upsert WHERE 계약 (ALPHA-1120).

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

INSTRUMENT_ID = "inst_e2e_alpha_1120"
TICKER = "991120"
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


def _write_canonical(storage, fetched_at: str, **over) -> None:
    """같은 가격을 주어진 수집 시각으로 canonical 파티션에 쓴다(기존 part 를 덮는다)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.lake import canonical_price_daily_partition

    row = {"market": "KR", "ticker": TICKER, "trade_date": TRADE_DATE,
           "close": 71500.0, "adj_close": 71500.0, "volume": 1000,
           "currency": "KRW", "source_vendor": "kis", "fetched_at": fetched_at}
    row.update(over)
    schema = pa.schema([
        ("market", pa.string()), ("ticker", pa.string()), ("trade_date", pa.string()),
        ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()),
        ("close", pa.float64()), ("adj_close", pa.float64()), ("volume", pa.int64()),
        ("currency", pa.string()), ("source_vendor", pa.string()), ("fetched_at", pa.string()),
    ])
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist([row], schema=schema), buf)
    key = f"{canonical_price_daily_partition('KR', TRADE_DATE)}/part-00000.parquet"
    storage.put_bytes(key, buf.getvalue())


def _cleanup(cur) -> None:
    """이 테스트가 만든 행만 지운다."""
    cur.execute("DELETE FROM price_daily WHERE instrument_id = %s", (INSTRUMENT_ID,))
    cur.execute("DELETE FROM instrument WHERE instrument_id = %s", (INSTRUMENT_ID,))
    cur.execute("DELETE FROM entity WHERE entity_id = %s", (INSTRUMENT_ID,))


def _seed_instrument(cur) -> None:
    """이 테스트 전용 종목을 마스터에 넣는다."""
    cur.execute("INSERT INTO entity (entity_id, entity_type, display_name)"
                " VALUES (%s, 'INSTRUMENT', %s)", (INSTRUMENT_ID, TICKER))
    cur.execute("INSERT INTO instrument"
                " (instrument_id, market_code, ticker, instrument_type, currency_code)"
                " VALUES (%s, 'XKRX', %s, 'EQUITY', 'KRW')", (INSTRUMENT_ID, TICKER))


def test_filling_empty_ohl_keeps_available_at_but_a_correction_moves_it_on_real_postgres(tmp_path):
    """시·고·저가 비어 있던 행을 채울 때는 available_at 이 그대로고, 고가 정정은 시각을 옮긴다(ALPHA-1148).

    컬럼 추가 전에 적재된 행은 시·고·저가 NULL 이다. 그 행에 canonical 의 시·고·저를 채우면서
    available_at 을 수집 시각으로 덮으면, 이미 쓰이던 종가가 그 사이 시점 조회에서 사라진다.
    반대로 채워진 뒤의 벤더 정정까지 시각을 붙들면 정정값이 과거에 알려진 것처럼 보인다.
    수정종가는 KIS 실물과 같이 NULL 이다 — 값 비교가 NULL 을 같다고 봐야 한다.
    """
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import load_price_daily

    pg = _pg_kwargs()
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    storage = LocalStorage(tmp_path / "lake")

    def row() -> tuple:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("SELECT open_price, high_price, low_price, close_price, volume,"
                        " (available_at AT TIME ZONE 'UTC')::text FROM price_daily"
                        " WHERE instrument_id = %s AND trade_date = %s",
                        (INSTRUMENT_ID, TRADE_DATE))
            [(o, h, low, c, v, at)] = cur.fetchall()
            return (None if o is None else float(o), None if h is None else float(h),
                    None if low is None else float(low), float(c), v, at)

    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        _cleanup(cur)
        _seed_instrument(cur)
        # 컬럼 추가 전 형태의 기존 행 — 시·고·저 없음, 거래일 장 마감 시각에 알려진 종가.
        cur.execute("INSERT INTO price_daily (instrument_id, trade_date, close_price, volume,"
                    " available_at, data_version) VALUES (%s, %s, 71500, 1000,"
                    " '2026-07-16T06:41:00+00:00', 'before-ohl')", (INSTRUMENT_ID, TRADE_DATE))
    ohl = {"open": 71000.0, "high": 72000.0, "low": 70500.0, "adj_close": None}
    try:
        _write_canonical(storage, "2026-08-15T12:48:00+00:00", **ohl)    # 같은 종가·거래량, 늦은 수집
        assert load_price_daily.run(storage, "e2e-1148-a", db=db) == 0
        assert row() == (71000.0, 72000.0, 70500.0, 71500.0, 1000, "2026-07-16 06:41:00")

        _write_canonical(storage, "2026-08-20T06:48:00+00:00", **{**ohl, "high": 72500.0})  # 고가 정정
        assert load_price_daily.run(storage, "e2e-1148-b", db=db) == 0
        assert row() == (71000.0, 72500.0, 70500.0, 71500.0, 1000, "2026-08-20 06:48:00")
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            _cleanup(cur)


def test_same_price_with_earlier_available_at_moves_the_mart_back_on_real_postgres(tmp_path):
    """D+1 값으로 먼저 적재된 행이 D일 수집분 재정제로 available_at 만 앞당겨지고, 늦은 시각으론 안 밀린다."""
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import load_price_daily

    pg = _pg_kwargs()
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    storage = LocalStorage(tmp_path / "lake")

    def available_at() -> str:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("SELECT available_at AT TIME ZONE 'UTC' FROM price_daily"
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
        assert load_price_daily.run(storage, "e2e-1120-a", db=db) == 0
        assert available_at() == "2026-07-17T06:41:00"

        _write_canonical(storage, "2026-07-16T06:41:00+00:00")   # 옛 raw 재정제 — 값 동일
        assert load_price_daily.run(storage, "e2e-1120-b", db=db) == 0
        assert available_at() == "2026-07-16T06:41:00"

        _write_canonical(storage, "2026-07-18T06:41:00+00:00")   # 늦은 시각 — 밀지 않는다
        assert load_price_daily.run(storage, "e2e-1120-c", db=db) == 0
        assert available_at() == "2026-07-16T06:41:00"
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            _cleanup(cur)
