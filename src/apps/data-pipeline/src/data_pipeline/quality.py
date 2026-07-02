"""품질 게이트 (S003 AC2 — 실패 항목은 사유와 함께 로깅).

canonical 1차 저장 필수 필드는 스토리 정의 그대로: 제목·발행시각·언론사·URL.
게이트를 통과하지 못한 항목은 canonical 에 넣지 않고(raw 에는 남아 있다)
실패 사유를 data_quality_logs 에 남긴다.
"""

from __future__ import annotations

from .parse import normalize_url, parse_datetime


def check_record(record: dict) -> list[str]:
    """raw 뉴스 항목의 품질 실패 사유 목록. 비어 있으면 통과."""
    reasons: list[str] = []
    if not (record.get("title") or "").strip():
        reasons.append("missing_title")
    if normalize_url(record.get("url")) is None:
        reasons.append("invalid_url")
    if parse_datetime(record.get("publishedDate")) is None:
        reasons.append("unparseable_published_at")
    if not (record.get("site") or "").strip():
        reasons.append("missing_publisher")
    return reasons
