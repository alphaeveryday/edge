"""지수 일봉 적재 E2E — 실 PostgreSQL (ALPHA-1260).

적재 규칙은 `ON CONFLICT … DO UPDATE … WHERE` 한 문장에 있다. 가짜 커넥션은 그 문장을 실행하지
않으므로 실 스키마에서 본다: 마스터에 등록된 지수만 실리는가, 더 늦은 관측만 행을 바꾸는가
(같은 값의 재확인이 시각을 올리고, 그 사이의 옛 관측은 뒤늦게 와도 값을 되돌리지 못한다).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime
from decimal import Decimal

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

SERIES_ID = "series_e2e_alpha_1260"
CODE, UNREGISTERED = "9960", "9961"
D1, D2 = "2026-07-01", "2026-07-02"
T1, T2, T3, T4 = (f"2026-07-0{day}T06:41:00+00:00" for day in (2, 3, 4, 5))


def _pg_kwargs() -> dict:
    """CI ephemeral PostgreSQL 접속 인자를 반환한다."""
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _row(code: str, trade_date: str, close: float, fetched_at: str) -> dict:
    """시가·고가·저가를 종가에서 서로 다르게 벌린다 — 컬럼이 뒤바뀌면 드러난다."""
    return {"market": "KR", "code": code, "trade_date": trade_date,
            "open": close - 1, "high": close + 2, "low": close - 3, "close": close,
            "source_vendor": "kis", "fetched_at": fetched_at}


def _parquet(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from data_pipeline.steps.normalize_sector_index_daily import SPEC

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=SPEC.schema()), buf)
    return buf.getvalue()


def _write_partition(storage, trade_date: str, rows: list[dict]) -> None:
    from data_pipeline.steps.normalize_sector_index_daily import SPEC

    storage.put_bytes(
        f"{SPEC.canonical_partition('KR', trade_date)}/part-00000.parquet", _parquet(rows))


def _loaded(pg: dict) -> list[tuple]:
    import psycopg

    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date::text, open_value, high_value, low_value, close_value,"
            " available_at, data_version FROM market_series_daily"
            " WHERE market_series_id = %s ORDER BY trade_date", (SERIES_ID,))
        return cur.fetchall()


def _expected(trade_date: str, close: str, fetched_at: str, data_version: str) -> tuple:
    c = Decimal(close)
    return (trade_date, c - 1, c + 2, c - 3, c, datetime.fromisoformat(fetched_at), data_version)


@pytest.fixture
def env(tmp_path):
    """등록된 시험 지수 한 개와 빈 레이크. 끝나면 지수와 그 값 행을 지운다(FK CASCADE)."""
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage

    pg = _pg_kwargs()
    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM market_series WHERE market_series_id = %s", (SERIES_ID,))
        cur.execute(
            "INSERT INTO market_series (market_series_id, series_type, series_name,"
            " source_code, source_series_id, market_code)"
            " VALUES (%s, 'INDEX', 'e2e 지수', 'KRX', %s, 'XKRX')", (SERIES_ID, CODE))
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    try:
        yield pg, db, LocalStorage(tmp_path / "lake")
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM market_series WHERE market_series_id = %s", (SERIES_ID,))


def test_등록된_지수만_실리고_더_늦은_관측만_행을_바꾼다(env):
    from data_pipeline.steps.load_market_series_daily import run

    pg, db, storage = env

    # 미등록 코드는 실패가 아니다 — canonical 은 업종 전부를 싣고 DB 에 둘 지수는 마스터가 고른다.
    _write_partition(storage, D1, [_row(CODE, D1, 100.5, T1), _row(UNREGISTERED, D1, 50.0, T1)])
    assert run(storage, "e2e-1260-a", db=db, from_date=D1, to_date=D1) == 0
    assert _loaded(pg) == [_expected(D1, "100.5", T1, "e2e-1260-a")]

    # 같은 값을 나중에 다시 확인했다(T3). 값은 같아도 관측 시각이 올라가야 한다 — 안 올리면
    # 바로 아래의 옛 관측(T2)이 "T1 보다 늦다"는 이유로 최신 값을 되돌린다.
    _write_partition(storage, D1, [_row(CODE, D1, 100.5, T3)])
    assert run(storage, "e2e-1260-b", db=db, from_date=D1, to_date=D1) == 0
    confirmed = [_expected(D1, "100.5", T3, "e2e-1260-b")]
    assert _loaded(pg) == confirmed

    # T1 < T2 < T3 인 옛 관측이 다른 값을 들고 뒤늦게 재시도됐다. 최신 값이 그대로여야 한다.
    _write_partition(storage, D1, [_row(CODE, D1, 90.0, T2)])
    assert run(storage, "e2e-1260-c", db=db, from_date=D1, to_date=D1) == 0
    assert _loaded(pg) == confirmed

    # 같은 관측 시각의 다른 실행도 행을 바꾸지 못한다(data_version 을 되돌리지 않는다).
    _write_partition(storage, D1, [_row(CODE, D1, 100.5, T3)])
    assert run(storage, "e2e-1260-c2", db=db, from_date=D1, to_date=D1) == 0
    assert _loaded(pg) == confirmed

    # 벤더 정정 — 더 늦은 관측이 다른 값을 들고 왔다. 값과 시각이 함께 바뀌어야 두 계층이 맞는다.
    _write_partition(storage, D1, [_row(CODE, D1, 101.0, T4)])
    assert run(storage, "e2e-1260-d", db=db, from_date=D1, to_date=D1) == 0
    assert _loaded(pg) == [_expected(D1, "101.0", T4, "e2e-1260-d")]


def test_정기_경로는_정제_manifest가_지목한_winner만_싣는다(env):
    """SFN 이 쓰는 경로다. 정제 사양(이름·행 키)과 어긋나면 manifest 를 못 찾거나 winner 를 거부한다."""
    from data_pipeline.lake import canonical_run_manifest_key, canonical_run_partition_key
    from data_pipeline.steps.load_market_series_daily import run
    from data_pipeline.steps.normalize_sector_index_daily import SPEC

    pg, db, storage = env
    parquet = _parquet([_row(CODE, D2, 200.0, T3), _row("9962", D2, 70.0, T3)])
    key = canonical_run_partition_key(SPEC.dataset, "e2e-1260-n", D2)
    storage.put_bytes(key, parquet)
    storage.put_bytes(canonical_run_manifest_key(SPEC.dataset, "e2e-1260-n"), json.dumps({
        "run_id": "e2e-1260-n", "producer": SPEC.job_name, "canonical_written": True,
        "canonical_partitions": [{
            "market": "KR", "trade_date": D2, "key": key,
            "sha256": hashlib.sha256(parquet).hexdigest(),
            "winner_ids": [{SPEC.row_key: CODE}],
        }],
    }, sort_keys=True).encode("utf-8"))

    assert run(storage, "e2e-1260-load", db=db, input_run_id="e2e-1260-n") == 0
    # data_version 은 값을 확정한 정제 실행이다(적재 실행 ID 가 아니다).
    assert _loaded(pg) == [_expected(D2, "200.0", T3, "e2e-1260-n")]
