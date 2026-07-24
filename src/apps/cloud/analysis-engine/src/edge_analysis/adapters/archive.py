"""주 DB 쓰기가 아닌 S3 산출물: 런 아카이브와 FK-결여 설명 폴백.

런 아카이브는 매 런의 중간 산출물(분해·소비 트리거·LLM 원문)을 남겨 트리거 없는 잔잔한
날도 감사 가능하게 한다(ALPHA-415). 쓰기 실패는 로그만 남기고 삼킨다 — 관측이 분석
영속을 무너뜨리면 안 된다.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..config import PipelineError, Settings
from ..domain.models import Decomposition, KodexEvent
from ..observability import log, utcnow_iso

_DEFAULT_PREFIX = "operations_archive/etf_explanations/"

# FK-결여 폴백은 이벤트 제목을 남기고, 런 아카이브는 novelty 도 남긴다. 둘 다 나머지는
# 의도적으로 버린다.
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
    """런 아카이브용 KODEX 이벤트 직렬화(novelty 포함)."""
    return [_event_dict(e, _ARCHIVE_EVENT_FIELDS) for e in events]


def decomp_summary(decomp: Decomposition) -> dict[str, Any]:
    """아카이브용 요약 — 스칼라는 전부, 멤버는 상위 10개만."""
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
    """``{result_prefix}/runs/`` 아래 런 아카이브 1건을 쓴다.

    Returns:
        s3 URI, 또는 쓰기 실패·비 s3:// prefix 면 ``None``(어느 쪽이든 런은 계속).
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
    except Exception as exc:  # noqa: BLE001 — 관측이 런을 죽이면 안 된다
        log("run_archive.failed", error=str(exc))
        return None
    location = f"s3://{bucket}/{key}"
    log("run_archive.stored", s3=location)
    return location


def write_explanation_to_s3(
    s3, settings: Settings, explanation: dict[str, Any], events: list[KodexEvent]
) -> str:
    """DB FK 전제가 없을 때 설명을 S3 에 영속한다."""
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
