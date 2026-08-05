"""인과 하네스의 **중간 과정** trace 를 S3 에 남긴다.

최종 설명은 ``write_run_archive`` 가 이미 ``runs/`` 에 남긴다 — 여기 끌어넣으면 아카이브가
디버그 로그로 부푼다. 그래서 같은 결과 prefix 아래 별도 ``traces/`` 키로 분리한다.
``_result_prefix``·``_split_s3_uri`` 는 archive 에서 그대로 import 한다(복사하면 두 곳의
키 규약이 조용히 갈라진다).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..config import Settings
from ..observability import log, utcnow_iso
from .archive import _result_prefix, _split_s3_uri


def write_agent_trace(
    s3, settings: Settings, trace: list[dict[str, Any]],
) -> dict[str, str | int] | None:
    """중간 과정 S3 객체와 RDB ``stage_results``용 검증 manifest를 함께 만든다."""
    if not trace:
        return None
    prefix = _result_prefix(settings)
    if not prefix.startswith("s3://"):
        return None
    bucket, key_prefix = _split_s3_uri(prefix)
    key = (
        f"{key_prefix}/traces/etf={settings.etf_ticker}/"
        f"trade_date={settings.trade_date.isoformat()}/{settings.request_id}.json"
    )
    body = json.dumps(
        {
            "etf_ticker": settings.etf_ticker,
            "trade_date": settings.trade_date.isoformat(),
            "request_id": settings.request_id,
            "generated_at": utcnow_iso(),
            "events": trace,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    except Exception as exc:  # noqa: BLE001 — 관측이 분석을 무너뜨리면 안 된다
        log("agent_trace.failed", error=str(exc))
        return None
    location = f"s3://{bucket}/{key}"
    log("agent_trace.stored", s3=location, events=len(trace))
    return {
        "s3_uri": location,
        "sha256": hashlib.sha256(body).hexdigest(),
        "event_count": len(trace),
        "schema_version": 1,
    }
