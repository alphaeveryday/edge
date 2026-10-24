"""DataGuide 일봉 적재의 충돌 규칙 E2E — 실 PostgreSQL (ALPHA-1148).

단위 테스트는 DB 반영을 가로채므로 "기존 행을 어떻게 다루는가"를 못 본다. 그 규칙은 전부
`ON CONFLICT … DO UPDATE … WHERE` 한 문장에 있고, 틀리면 KIS 가 적재한 행 12만 건을 덮거나
5분봉 집산값이 그대로 남는다. 세 경우(행 없음·5분봉 집산 행·KIS 행)를 한 번에 놓고 본다.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("E2E_PGHOST"),
    reason="ephemeral Postgres 필요 — CI e2e job 전용(E2E_PGHOST 미설정)",
)

INSTRUMENT_ID = "inst_e2e_alpha_1148"
TICKER = "991148"
AS_OF = "2026-08-02"
NEW_DAY, FIVE_MIN_DAY, KIS_DAY = "2026-07-01", "2026-07-02", "2026-07-03"
# 항목별 값 — 서로 달라야 컬럼이 뒤바뀐 것이 드러난다.
VALUES = {"open_price": "71000", "high_price": "72000", "low_price": "70500",
          "close_price": "71500", "adjusted_close_price": "1430", "volume": "1000"}


def _pg_kwargs() -> dict:
    """CI ephemeral PostgreSQL 접속 인자를 반환한다."""
    return {
        "host": os.environ["E2E_PGHOST"],
        "port": int(os.environ.get("E2E_PGPORT", "5432")),
        "dbname": os.environ.get("E2E_PGDATABASE", "edge"),
        "user": os.environ.get("E2E_PGUSER", "edge"),
        "password": os.environ.get("E2E_PGPASSWORD", "edge"),
    }


def _write_snapshot(storage) -> None:
    """세 거래일·한 종목짜리 DataGuide 스냅샷을 쓴다."""
    from data_pipeline.lake import draft_dataguide_price_item_prefix
    from data_pipeline.steps.backfill_price_daily_dataguide import ITEMS

    for column, code in ITEMS.items():
        lines = [f"date,A{TICKER}"] + [f"{day},{VALUES[column]}"
                                       for day in (NEW_DAY, FIVE_MIN_DAY, KIS_DAY)]
        buf = io.BytesIO()
        with gzip.open(buf, "wt", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        storage.put_bytes(
            f"{draft_dataguide_price_item_prefix('KR', AS_OF, code)}/{code}.csv.gz", buf.getvalue())


def _cleanup(cur) -> None:
    """이 테스트가 만든 행만 지운다."""
    cur.execute("DELETE FROM price_daily WHERE instrument_id = %s", (INSTRUMENT_ID,))
    cur.execute("DELETE FROM instrument WHERE instrument_id = %s", (INSTRUMENT_ID,))
    cur.execute("DELETE FROM entity WHERE entity_id = %s", (INSTRUMENT_ID,))


def _seed(cur) -> None:
    """종목과 기존 행 둘(5분봉 집산 행·KIS 일일 적재 행)을 넣는다."""
    cur.execute("INSERT INTO entity (entity_id, entity_type, display_name)"
                " VALUES (%s, 'INSTRUMENT', %s)", (INSTRUMENT_ID, TICKER))
    cur.execute("INSERT INTO instrument"
                " (instrument_id, market_code, ticker, instrument_type, currency_code)"
                " VALUES (%s, 'XKRX', %s, 'EQUITY', 'KRW')", (INSTRUMENT_ID, TICKER))
    # 5분봉 집산 행 — 종가가 실제와 다르고, 그 종가로 계산한 수익률이 있다.
    cur.execute("INSERT INTO price_daily (instrument_id, trade_date, close_price, simple_return,"
                " log_return, volume, available_at, data_version) VALUES"
                " (%s, %s, 71400, 0.01, 0.00995, 900, '2026-07-02T06:30:00+00:00',"
                "  'fmp_5min_20260720')", (INSTRUMENT_ID, FIVE_MIN_DAY))
    # KIS 일일 적재 행 — 거래량·수집 시각이 DataGuide 와 다르다. 그대로 남아야 한다.
    # data_version 은 일일 로더의 run_id 라 무엇이든 될 수 있다. `fmp-5min-…` 은 교체 대상
    # 접두 `fmp_5min` 과 한 글자 다르다 — LIKE 로 비교하면 `_` 가 그 글자를 삼켜 교체된다.
    cur.execute("INSERT INTO price_daily (instrument_id, trade_date, close_price, volume,"
                " available_at, data_version) VALUES"
                " (%s, %s, 71500, 987, '2026-07-03T06:41:00+00:00', 'fmp-5min-recovery')",
                (INSTRUMENT_ID, KIS_DAY))


def _rows(pg) -> dict:
    """거래일 → 검사할 컬럼 dict."""
    import psycopg

    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT trade_date::text, open_price, high_price, low_price, close_price,"
            " adjusted_close_price, volume, simple_return, log_return, price_basis,"
            " (available_at AT TIME ZONE 'UTC')::text, data_version"
            " FROM price_daily WHERE instrument_id = %s ORDER BY trade_date", (INSTRUMENT_ID,))
        names = ("open", "high", "low", "close", "adj", "volume", "simple_return", "log_return",
                 "price_basis", "available_at", "data_version")
        return {
            row[0]: {name: (float(value) if name in ("open", "high", "low", "close", "adj")
                            and value is not None else value)
                     for name, value in zip(names, row[1:])}
            for row in cur.fetchall()
        }


def test_dataguide_backfill_inserts_replaces_5min_rows_and_keeps_kis_rows(tmp_path):
    """없는 날은 삽입, 5분봉 집산 행은 교체(수익률 비움), KIS 행은 한 컬럼도 건드리지 않는다."""
    import psycopg

    from data_pipeline.config import DbConfig
    from data_pipeline.lake import LocalStorage
    from data_pipeline.steps import backfill_price_daily_dataguide as step

    pg = _pg_kwargs()
    db = DbConfig(host=pg["host"], port=pg["port"], name=pg["dbname"],
                  user=pg["user"], password=pg["password"], sslmode="disable")
    storage = LocalStorage(tmp_path / "lake")
    _write_snapshot(storage)
    window = {"as_of_date": AS_OF, "from_date": NEW_DAY, "to_date": KIS_DAY}

    def log(run_id: str) -> dict:
        [key] = [k for k in storage.list_keys("operations_archive/data_quality_logs/")
                 if step.DATASET in k and f"run_id={run_id}/" in k]
        return json.loads(storage.get_bytes(key).decode("utf-8"))

    with psycopg.connect(**pg) as conn, conn.cursor() as cur:
        _cleanup(cur)
        _seed(cur)
    before = _rows(pg)
    loaded = {"open": 71000.0, "high": 72000.0, "low": 70500.0, "close": 71500.0, "adj": 1430.0,
              "volume": 1000, "simple_return": None, "log_return": None,
              "price_basis": "raw_close;adj_asof=2026-08-02", "data_version": "dataguide-20260802"}
    try:
        # dry_run 은 같은 분류를 내지만 한 행도 바꾸지 않는다.
        assert step.run(storage, "e2e-1148-dry", db=db, dry_run=True, **window) == 0
        assert _rows(pg) == before
        dry = log("e2e-1148-dry")
        assert (dry["created"], dry["replaced_5min"], dry["kept_existing"]) == (1, 1, 1)

        assert step.run(storage, "e2e-1148-a", db=db, **window) == 0
        after = _rows(pg)
        # 거래일 15:30 KST = 06:30 UTC.
        assert after[NEW_DAY] == {**loaded, "available_at": "2026-07-01 06:30:00"}
        assert after[FIVE_MIN_DAY] == {**loaded, "available_at": "2026-07-02 06:30:00"}
        assert after[KIS_DAY] == before[KIS_DAY]
        first = log("e2e-1148-a")
        assert (first["created"], first["replaced_5min"], first["rewritten"],
                first["kept_existing"]) == (1, 1, 0, 1)
        # 덮어쓴 5분봉 집산 행은 덮기 전 값 그대로 보존본에 남는다 — 되돌릴 유일한 근거다.
        [snapshot] = storage.list_keys("operations_archive/replaced_rows/")
        payload = gzip.decompress(storage.get_bytes(snapshot))
        # 키가 내용 해시라 다른 내용이 같은 자리를 덮을 수 없다.
        assert snapshot.endswith(f"run_id=e2e-1148-a/sha256={hashlib.sha256(payload).hexdigest()}"
                                 ".ndjson.gz")
        [kept_row] = [json.loads(line) for line in payload.decode("utf-8").splitlines()]
        assert (kept_row["trade_date"], float(kept_row["close_price"]), kept_row["simple_return"],
                kept_row["volume"], kept_row["data_version"]) == (
            FIVE_MIN_DAY, 71400.0, 0.01, 900, "fmp_5min_20260720")

        # 재실행은 같은 결과다 — 이 스텝이 넣은 두 행만 다시 쓰고 KIS 행은 여전히 그대로다.
        assert step.run(storage, "e2e-1148-b", db=db, **window) == 0
        assert _rows(pg) == after
        second = log("e2e-1148-b")
        assert (second["created"], second["replaced_5min"], second["rewritten"],
                second["kept_existing"]) == (0, 0, 2, 1)
        assert storage.list_keys("operations_archive/replaced_rows/") == [snapshot]   # 새 보존본 없음
    finally:
        with psycopg.connect(**pg) as conn, conn.cursor() as cur:
            _cleanup(cur)
