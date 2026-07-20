"""구조화 로그와 결정적 id 생성.

``PIPELINE_ID`` 는 결정적 id 의 재료라 data-pipeline 의 ``PIPELINE_ID``
(``assemble_events.PIPELINE_ID``)와 **반드시 같아야** 한다 — 같은 이벤트가 두 코드베이스에서
같은 source_event/thread id 로 수렴해야 멱등 upsert 가 성립한다(ADR-0028 이행기).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

PIPELINE_ID = "alphamale-etf-daily-v1"


def utcnow_iso() -> str:
    """초 단위 UTC ISO 타임스탬프(정렬·비교가 안정적이도록 마이크로초 제거)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    """``PIPELINE_ID`` + ``parts`` 로부터 결정적 id 생성(멱등 upsert 의 재료)."""
    material = "\u0001".join([PIPELINE_ID, *(str(p) for p in parts)])
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:26]
    return f"{prefix}_{digest}"


def log(event: str, **fields: object) -> None:
    """구조화 stdout 로그. 제목·프롬프트·비밀값은 절대 넣지 않는다."""
    payload = {"ts": utcnow_iso(), "pipeline": PIPELINE_ID, "event": event}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
