"""Database connection settings."""
from __future__ import annotations
import os
from dataclasses import dataclass

class PipelineError(RuntimeError):
    """Invalid query configuration or operation."""

@dataclass(frozen=True, slots=True)
class PgConfig:
    """Cloud Event Store(Postgres) 접속 설정."""

    host: str
    port: int
    dbname: str
    user: str
    password: str | None
    schema: str

def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default

def _load_pg() -> PgConfig:
    schema = _env("PGSCHEMA", "public")
    # 스키마는 search_path 에 문자열로 박히므로 주입을 막는다(영숫자·밑줄만 허용).
    if schema != schema.strip() or not schema.replace("_", "").isalnum():
        raise PipelineError(f"invalid PGSCHEMA {schema!r}")
    return PgConfig(
        host=_env("PGHOST", "127.0.0.1"),
        port=int(_env("PGPORT", "5432")),
        dbname=_env("PGDATABASE", "postgres"),
        user=_env("PGUSER", "postgres"),
        password=_env("PGPASSWORD"),
        schema=schema,
    )
