"""Local SQLite analysis store -- single ``daily_prediction`` table (see schema.py).

Idempotent upsert on (trade_date, market, asset_code). Failed/skipped tickers are
rows with ``status`` != 'ok' and an ``error_code`` -- no separate fail-log table.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..config import DB_PATH
from . import schema


def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(schema.sqlite_ddl())
    conn.commit()


def _coerce(name: str, value: Any) -> Any:
    if name in schema.JSON_COLUMNS:
        return None if value is None else json.dumps(value, ensure_ascii=False, default=str)
    if name in schema.BOOL_COLUMNS:
        return None if value is None else int(bool(value))
    if isinstance(value, bool):
        return int(value)
    return value


def upsert_daily(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """Insert/replace daily_prediction rows keyed by (trade_date, market, asset_code)."""
    materialized = list(rows)
    if not materialized:
        return 0
    now = schema.utc_now()
    payload = []
    for row in materialized:
        record = dict(row)
        record.setdefault("created_at", now)
        payload.append([_coerce(col, record.get(col)) for col in schema.COLUMN_NAMES])
    conn.executemany(schema.upsert_sql(schema.TABLE, "sqlite"), payload)
    conn.commit()
    return len(payload)
