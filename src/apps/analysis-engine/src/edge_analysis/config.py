"""Configuration: environment variables to a validated ``Settings``.

Invalid or missing values fail loudly (``PipelineError``) instead of silently
defaulting, so a misconfigured task cannot "succeed" with no output (Rule 12).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
DEFAULT_ETF_TICKER = "091160"
DEFAULT_MODEL = "deepseek-chat"


class PipelineError(RuntimeError):
    """Fatal pipeline error -> non-zero exit -> Step Functions failure."""


@dataclass(frozen=True, slots=True)
class PgConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str | None
    schema: str


@dataclass(frozen=True, slots=True)
class Settings:
    trade_date: date
    request_id: str
    region: str
    lake_bucket: str
    etf_ticker: str
    pg: PgConfig
    deepseek_api_key: str
    deepseek_model: str
    release_bundle_version: str | None
    result_s3_prefix: str | None
    aws_profile: str | None


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def parse_trade_date(value: str | None) -> date:
    """Parse ``YYYY-MM-DD``; empty means today (KST) so trigger-less days run."""
    if not value:
        return datetime.now(KST).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PipelineError(f"invalid trade_date {value!r}; expected YYYY-MM-DD") from exc


def _load_pg() -> PgConfig:
    schema = _env("PGSCHEMA", "public")
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


def load_settings(*, trade_date: str | None = None, request_id: str | None = None) -> Settings:
    """Build a validated ``Settings`` from the environment and CLI arguments."""
    api_key = _env("DEEPSEEK_API_KEY")
    if not api_key:
        raise PipelineError("DEEPSEEK_API_KEY is not set")
    return Settings(
        trade_date=parse_trade_date(trade_date),
        request_id=request_id or f"local-{datetime.now(KST).strftime('%Y%m%dT%H%M%S')}",
        region=_env("AWS_REGION", "ap-northeast-2"),
        lake_bucket=_env("ALPHAMALE_LAKE_BUCKET", "edge-dev-pipeline-lake"),
        etf_ticker=_env("ALPHAMALE_ETF_TICKER", DEFAULT_ETF_TICKER),
        pg=_load_pg(),
        deepseek_api_key=api_key,
        deepseek_model=_env("DEEPSEEK_MODEL", DEFAULT_MODEL),
        release_bundle_version=_env("ALPHAMALE_RELEASE_BUNDLE_VERSION"),
        result_s3_prefix=_env("ALPHAMALE_RESULT_S3_PREFIX"),
        aws_profile=_env("AWS_PROFILE"),
    )
