"""주 DB 쓰기가 아닌 S3 산출물: 런 아카이브.

런 아카이브는 매 런의 중간 산출물(분해·소비 트리거·LLM 원문)을 남겨 트리거 없는 잔잔한
날도 감사 가능하게 한다(ALPHA-415). 분석 완료의 필수 감사 산출물이므로 크기 초과나
쓰기 실패를 삼키지 않는다.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..config import PipelineError, Settings
from ..domain.models import Decomposition, EventContext
from ..observability import log, utcnow_iso

_DEFAULT_PREFIX = "operations_archive/etf_explanations/"

# Two model attempts contribute at most 512 KiB after the model-boundary cap.
# 2 MiB leaves room for deterministic evidence, events, and explanation metadata.
MAX_RUN_ARCHIVE_BYTES = 2 * 1024 * 1024


class RunArchiveError(PipelineError):
    """A required analysis run archive could not be stored intact."""


# 런 아카이브는 이벤트 제목과 novelty 를 남긴다. 나머지는 의도적으로 버린다.
_ARCHIVE_EVENT_FIELDS = (
    "source_event_id", "thread_id", "event_type_code", "ticker", "novelty_status", "title",
)


def _result_prefix(settings: Settings) -> str:
    return settings.result_s3_prefix or f"s3://{settings.lake_bucket}/{_DEFAULT_PREFIX}"


def _split_s3_uri(uri: str) -> tuple[str, str]:
    bucket, _, key_prefix = uri[len("s3://"):].partition("/")
    return bucket, key_prefix.rstrip("/")


def _event_dict(event: EventContext, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(event, field) for field in fields}


def archived_events(events: list[EventContext]) -> list[dict[str, Any]]:
    """런 아카이브용 구성종목 이벤트 직렬화(novelty 포함)."""
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
    """``{result_prefix}/runs/`` 아래 크기 제한된 필수 런 아카이브 1건을 쓴다.

    Returns:
        저장된 s3 URI. 비 s3:// prefix 설정이면 ``None``.

    Raises:
        RunArchiveError: 아카이브가 제한을 넘거나 S3 쓰기에 실패한 경우.
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
    if len(body) > MAX_RUN_ARCHIVE_BYTES:
        raise RunArchiveError(
            f"RUN_ARCHIVE_TOO_LARGE: {len(body)} > {MAX_RUN_ARCHIVE_BYTES} bytes")
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    except Exception as exc:  # noqa: BLE001 — 필수 감사 산출물은 실패를 숨기지 않는다
        log("run_archive.failed", error=str(exc))
        raise RunArchiveError(f"run archive S3 write failed: {exc}") from exc
    location = f"s3://{bucket}/{key}"
    log("run_archive.stored", s3=location)
    return location
