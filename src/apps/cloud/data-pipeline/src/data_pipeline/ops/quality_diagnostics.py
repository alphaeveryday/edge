"""실행 시도에 보존할 bounded 뉴스 품질 진단 계약 (ALPHA-1067)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

SCHEMA = "news_resolution_v1"
ASSERTION_SCOPE = "assertion_arguments"
EVENT_SCOPE = "anchorless_events"

INSTRUMENT_NOT_FOUND = "instrument_not_found"
INSTRUMENT_AMBIGUOUS = "instrument_ambiguous"
REGISTRY_MISS = "registry_miss"
ARGUMENTS_MISSING = "arguments_missing"
CONCEPT_REJECTED = "concept_rejected"

_SCOPES = {
    ASSERTION_SCOPE: frozenset({"total", "resolved", "unresolved"}),
    EVENT_SCOPE: frozenset({"events", "anchorless", "unresolvedArguments"}),
}
_REASONS = frozenset({
    INSTRUMENT_NOT_FOUND,
    INSTRUMENT_AMBIGUOUS,
    REGISTRY_MISS,
    ARGUMENTS_MISSING,
    CONCEPT_REJECTED,
})
_ISSUE_LIMIT = 10
_MAX_BYTES = 8192  # DB CHECK(16KiB) 아래에서 JSONB 공백 직렬화 여유를 둔다.
# 10건 모두 quote/backslash라 JSON escape가 2배가 되어도 전체 8KiB 아래인 합계다.
_MAX_ROLE_BYTES = 32
_MAX_EXPRESSION_BYTES = 96
_MAX_ARTICLE_ID_BYTES = 64
_MAX_TITLE_BYTES = 96


def _count(value: object, *, positive: bool = False) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= (1 if positive else 0)
        and value <= 2**63 - 1
    )


def _text_fits(value: object, limit: int) -> bool:
    """DB JSONB에 안전한 비어 있지 않은 printable UTF-8 문자열인가."""
    if not isinstance(value, str) or not value or not value.isprintable():
        return False
    try:
        return len(value.encode("utf-8")) <= limit
    except UnicodeError:
        return False


def _truncate(value: str, limit: int) -> str:
    """제어문자를 없애고 UTF-8 경계에서 잘라 JSON escape까지 포함한 상한을 지킨다."""
    printable = "".join(char if char.isprintable() else " " for char in value)
    return printable.encode("utf-8", errors="replace")[:limit].decode("utf-8", errors="ignore")


def build(
    scope: str,
    metrics: Mapping[str, int],
    issues: Iterable[Mapping[str, object]],
) -> dict:
    """producer의 전량 집계에서 상위 10건만 결정적으로 잘라 계약 객체를 만든다."""
    rows = sorted(
        issues,
        key=lambda row: (
            -int(row["count"]), str(row["reason"]), str(row["role"]),
            str(row["expression"]),
        ),
    )[:_ISSUE_LIMIT]
    value = {
        "schema": SCHEMA,
        "scope": scope,
        "metrics": dict(metrics),
        "issues": [dict(row) for row in rows],
    }
    if validated(value) is None:
        raise ValueError("품질 진단이 bounded 계약을 위반했다")
    return value


def validated(value: object) -> dict | None:
    """로그의 임의 JSON을 원장에 복사하기 전에 형태·어휘·크기를 닫아 검증한다."""
    if not isinstance(value, dict) or set(value) != {"schema", "scope", "metrics", "issues"}:
        return None
    scope = value.get("scope")
    if value.get("schema") != SCHEMA or not isinstance(scope, str) or scope not in _SCOPES:
        return None
    metrics = value.get("metrics")
    if (not isinstance(metrics, dict) or set(metrics) != _SCOPES[scope]
            or any(not _count(v) for v in metrics.values())):
        return None
    if scope == ASSERTION_SCOPE and metrics["resolved"] + metrics["unresolved"] != metrics["total"]:
        return None
    if scope == EVENT_SCOPE and metrics["anchorless"] > metrics["events"]:
        return None
    issues = value.get("issues")
    if not isinstance(issues, list) or len(issues) > _ISSUE_LIMIT:
        return None
    issue_count = 0
    arguments_missing_count = 0
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {
            "reason", "role", "expression", "count", "sample",
        }:
            return None
        reason, role, expression = issue.get("reason"), issue.get("role"), issue.get("expression")
        if (not isinstance(reason, str) or reason not in _REASONS
                or not _text_fits(role, _MAX_ROLE_BYTES)
                or not _text_fits(expression, _MAX_EXPRESSION_BYTES)
                or not _count(issue.get("count"), positive=True)):
            return None
        issue_count += issue["count"]
        if reason == ARGUMENTS_MISSING:
            arguments_missing_count += issue["count"]
        sample = issue.get("sample")
        if not isinstance(sample, dict) or set(sample) != {"articleId", "title"}:
            return None
        article_id, title = sample.get("articleId"), sample.get("title")
        if (not _text_fits(article_id, _MAX_ARTICLE_ID_BYTES)
                or not _text_fits(title, _MAX_TITLE_BYTES)):
            return None
    if scope == ASSERTION_SCOPE and issue_count > metrics["unresolved"]:
        return None
    if scope == EVENT_SCOPE:
        if bool(issues) != (metrics["anchorless"] > 0):
            return None
        if (arguments_missing_count > metrics["anchorless"]
                or issue_count - arguments_missing_count > metrics["unresolvedArguments"]):
            return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return None
    return value if len(encoded) <= _MAX_BYTES else None


def issue(
    *, reason: str, role: str, expression: str, count: int,
    article_id: str, title: str,
) -> dict:
    """원문 본문 없이 표시명·제목 표본만 상한 안으로 만든다."""
    return {
        "reason": reason,
        "role": _truncate(role, _MAX_ROLE_BYTES),
        "expression": _truncate(expression, _MAX_EXPRESSION_BYTES),
        "count": count,
        "sample": {
            "articleId": _truncate(article_id, _MAX_ARTICLE_ID_BYTES),
            "title": _truncate(title, _MAX_TITLE_BYTES),
        },
    }
