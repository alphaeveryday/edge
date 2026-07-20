"""Structured logging and deterministic id minting.

``PIPELINE_ID`` is deterministic-id material and **must** equal the
data-pipeline's ``PIPELINE_ID`` (``assemble_events.PIPELINE_ID``): the same
event must resolve to the same source_event/thread ids across both codebases,
so idempotent upserts converge (ADR-0028 transition).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

PIPELINE_ID = "alphamale-etf-daily-v1"


def utcnow_iso() -> str:
    """Second-resolution UTC ISO timestamp (stable for sorting/comparison)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    """Deterministic id from ``PIPELINE_ID`` + ``parts`` (idempotent upserts)."""
    material = "\u0001".join([PIPELINE_ID, *(str(p) for p in parts)])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:26]
    return f"{prefix}_{digest}"


def log(event: str, **fields: object) -> None:
    """Structured stdout log. Never emit titles, prompts, or secrets here."""
    payload = {"ts": utcnow_iso(), "pipeline": PIPELINE_ID, "event": event}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
