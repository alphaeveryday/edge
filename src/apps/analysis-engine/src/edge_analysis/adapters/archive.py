"""S3 outputs that are not the primary DB write: the run archive and the
FK-missing explanation fallback.

The run archive records every run's intermediate outputs (decomposition,
consumed trigger, raw LLM response) so calm no-trigger days are still auditable
(ALPHA-415). A write failure is logged and swallowed — observation must never
take down analysis persistence.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..config import PipelineError, Settings
from ..domain.models import Decomposition, KodexEvent
from ..observability import log, utcnow_iso

_DEFAULT_PREFIX = "operations_archive/etf_explanations/"

# The FK-missing fallback keeps the event title; the run archive also keeps the
# novelty status. Both intentionally drop the rest of the event.
_FALLBACK_EVENT_FIELDS = ("source_event_id", "thread_id", "event_type_code", "ticker", "title")
_ARCHIVE_EVENT_FIELDS = (*_FALLBACK_EVENT_FIELDS[:4], "novelty_status", "title")


def _result_prefix(settings: Settings) -> str:
    return settings.result_s3_prefix or f"s3://{settings.lake_bucket}/{_DEFAULT_PREFIX}"


def _split_s3_uri(uri: str) -> tuple[str, str]:
    bucket, _, key_prefix = uri[len("s3://"):].partition("/")
    return bucket, key_prefix.rstrip("/")


def _event_dict(event: KodexEvent, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(event, field) for field in fields}


def archived_events(events: list[KodexEvent]) -> list[dict[str, Any]]:
    """Serialize KODEX events for the run archive (incl. novelty status)."""
    return [_event_dict(e, _ARCHIVE_EVENT_FIELDS) for e in events]


def decomp_summary(decomp: Decomposition) -> dict[str, Any]:
    """Archive-friendly summary — scalars in full, only the top-10 members."""
    return {
        "proxy_ret": decomp.proxy_ret,
        "coverage": decomp.coverage,
        "covered_weight": decomp.covered_weight,
        "total_priced": decomp.total_priced,
        "n_constituents": decomp.n_constituents,
        "advancing": decomp.advancing,
        "top1": decomp.top1,
        "top3": decomp.top3,
        "top_members": [asdict(m) for m in decomp.members[:10]],
    }


def write_run_archive(s3, settings: Settings, archive: dict[str, Any]) -> str | None:
    """Write one run-archive record under ``{result_prefix}/runs/``.

    Returns the s3 URI, or ``None`` if the write fails or the prefix is not an
    s3:// URI (the run continues either way).
    """
    prefix = _result_prefix(settings)
    if not prefix.startswith("s3://"):
        return None
    bucket, key_prefix = _split_s3_uri(prefix)
    key = (
        f"{key_prefix}/runs/etf={settings.etf_ticker}/"
        f"trade_date={settings.trade_date.isoformat()}/{settings.request_id}.json"
    )
    body = json.dumps(
        {
            "etf_ticker": settings.etf_ticker,
            "trade_date": settings.trade_date.isoformat(),
            "request_id": settings.request_id,
            "generated_at": utcnow_iso(),
            **archive,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    except Exception as exc:  # noqa: BLE001 — observation must not kill the run
        log("run_archive.failed", error=str(exc))
        return None
    location = f"s3://{bucket}/{key}"
    log("run_archive.stored", s3=location)
    return location


def write_explanation_to_s3(
    s3, settings: Settings, explanation: dict[str, Any], events: list[KodexEvent]
) -> str:
    """Persist the explanation to S3 when the DB FK prerequisites are missing."""
    prefix = _result_prefix(settings)
    if not prefix.startswith("s3://"):
        raise PipelineError(f"result prefix must be an s3:// URI, got {prefix!r}")
    bucket, key_prefix = _split_s3_uri(prefix)
    key = (
        f"{key_prefix}/etf={settings.etf_ticker}/"
        f"trade_date={settings.trade_date.isoformat()}/{settings.request_id}.json"
    )
    body = json.dumps(
        {
            "etf_ticker": settings.etf_ticker,
            "trade_date": settings.trade_date.isoformat(),
            "request_id": settings.request_id,
            "generated_at": utcnow_iso(),
            "explanation": explanation,
            "events": [_event_dict(e, _FALLBACK_EVENT_FIELDS) for e in events],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    return f"s3://{bucket}/{key}"
