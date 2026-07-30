"""구조화 로그와 결정적 id 생성.

``PIPELINE_ID`` 는 결정적 id 의 재료라 data-pipeline 의 ``PIPELINE_ID``
(``assemble_events.PIPELINE_ID``)와 **반드시 같아야** 한다 — 같은 이벤트가 두 코드베이스에서
같은 source_event/thread id 로 수렴해야 멱등 upsert 가 성립한다(ADR-0028 이행기).
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone

PIPELINE_ID = "alphamale-etf-daily-v1"

# 인과 하네스 중간 과정 수집 버퍼. **기본은 None = 수집 안 함** — load-classification 같은
# 다른 진입점이 쓸데없이 메모리를 잡지 않게 한다.
# ponytail: 모듈 전역, 스레드 공유 없음(파이프라인 런은 단일 스레드). 병렬 런이 생기면
# ContextVar 로 올린다.
_buffer: list[dict[str, object]] | None = None
_limit = 0
_dropped = 0

TRACE_LIMIT = 2000


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
    global _dropped
    payload = {"ts": utcnow_iso(), "pipeline": PIPELINE_ID, "event": event}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    if _buffer is None:
        return
    # stdout 출력이 먼저다 — CloudWatch 가 읽는 계약은 수집 여부와 무관하게 유지된다.
    if len(_buffer) < _limit:
        _buffer.append(payload)
    else:
        _dropped += 1


@contextmanager
def collect_trace(limit: int = TRACE_LIMIT) -> Iterator[list[dict[str, object]]]:
    """블록 안의 ``log()`` 이벤트를 순서대로 모아 리스트로 넘긴다.

    상한을 두는 이유: 되먹임 루프가 폭주하면 버퍼가 런의 메모리를 다 먹는다. 넘친 건
    조용히 버리지 않고 블록이 끝날 때 ``trace.truncated`` 한 줄로 드러낸다 — 잘린 trace 를
    완전한 trace 로 오해하면 디버깅이 엉뚱한 곳을 판다.
    """
    global _buffer, _limit, _dropped
    prev = (_buffer, _limit, _dropped)
    collected: list[dict[str, object]] = []
    _buffer, _limit, _dropped = collected, limit, 0
    try:
        yield collected
    finally:
        dropped = _dropped
        _buffer, _limit, _dropped = prev
        if dropped:
            collected.append({"event": "trace.truncated", "dropped": dropped, "limit": limit})
