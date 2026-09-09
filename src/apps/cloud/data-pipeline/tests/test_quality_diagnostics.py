import json

from data_pipeline.ops.quality_diagnostics import (
    ASSERTION_SCOPE,
    EVENT_SCOPE,
    INSTRUMENT_NOT_FOUND,
    build,
    issue,
    validated,
)


def _issue(n: int) -> dict:
    return issue(
        reason=INSTRUMENT_NOT_FOUND, role="ISSUER", expression=f"회사{n}", count=n + 1,
        article_id=f"article-{n}", title=f"기사 {n}",
    )


def test_build_keeps_deterministic_bounded_top_reasons():
    """WHY: 진단이 무제한이면 attempt 원장이 원문 저장소로 변하고 DB CHECK도 넘는다."""
    value = build(
        ASSERTION_SCOPE, {"total": 100, "resolved": 22, "unresolved": 78},
        [_issue(n) for n in range(12)],
    )
    assert len(value["issues"]) == 10
    assert [row["count"] for row in value["issues"]] == list(range(12, 2, -1))
    assert validated(value) == value


def test_validation_rejects_raw_or_unbounded_payloads():
    """WHY: producer 로그의 임의 필드·원문을 검증 없이 JSONB에 복사하면 민감정보가 장기 보존된다."""
    valid = build(
        ASSERTION_SCOPE, {"total": 1, "resolved": 0, "unresolved": 1}, [_issue(0)],
    )
    assert validated({**valid, "raw": "원문"}) is None
    assert validated({**valid, "issues": [*valid["issues"], *[_issue(n) for n in range(10)]]}) is None
    assert validated({**valid, "metrics": {**valid["metrics"], "total": True}}) is None
    assert validated({**valid, "metrics": {"total": 1, "resolved": 2, "unresolved": 0}}) is None
    assert validated({**valid, "metrics": {"total": 1, "resolved": 1, "unresolved": 0}}) is None
    assert validated({**valid, "issues": []}) is None
    assert validated({**valid, "issues": [{**valid["issues"][0], "reason": "invented"}]}) is None
    assert validated({
        "schema": "news_resolution_v1", "scope": EVENT_SCOPE,
        "metrics": {"events": 1, "anchorless": 2, "unresolvedArguments": 2}, "issues": [],
    }) is None
    event_issue = {**valid["issues"][0], "role": "ISSUER"}
    assert validated({
        "schema": "news_resolution_v1", "scope": EVENT_SCOPE,
        "metrics": {"events": 1, "anchorless": 0, "unresolvedArguments": 1},
        "issues": [event_issue],
    }) is None
    assert validated({
        "schema": "news_resolution_v1", "scope": EVENT_SCOPE,
        "metrics": {"events": 1, "anchorless": 1, "unresolvedArguments": 1}, "issues": [],
    }) is None


def test_validation_rejects_malformed_membership_and_postgres_unsafe_text_without_raising():
    """WHY: 임의 로그 타입이나 NUL·고립 surrogate가 validator를 터뜨리거나 JSONB UPDATE를 막으면
    정상 카운터까지 사라지고 attempt가 RUNNING에 남는다."""
    valid = build(
        ASSERTION_SCOPE, {"total": 1, "resolved": 0, "unresolved": 1}, [_issue(0)],
    )
    assert validated({**valid, "scope": []}) is None
    assert validated({
        **valid, "issues": [{**valid["issues"][0], "reason": {"bad": True}}],
    }) is None
    for unsafe in ("회사\x00", "회사\n", "\ud800"):
        assert validated({
            **valid,
            "issues": [{**valid["issues"][0], "expression": unsafe}],
        }) is None
    for field in ("role", "expression"):
        assert validated({
            **valid,
            "issues": [{**valid["issues"][0], field: "   "}],
        }) is None
    for field in ("articleId", "title"):
        assert validated({
            **valid,
            "issues": [{
                **valid["issues"][0],
                "sample": {**valid["issues"][0]["sample"], field: "   "},
            }],
        }) is None


def test_builder_keeps_ten_multibyte_samples_inside_storage_limit():
    """WHY: 문자 수로 자르면 한글 표본 10건은 8KiB를 넘어 정상 producer를 로그 직전에 죽인다."""
    rows = [issue(
        reason=INSTRUMENT_NOT_FOUND, role="발행사" * 100, expression="회사" * 200,
        count=10 - n, article_id="기사식별자" * 100, title="긴 한글 기사 제목" * 100,
    ) for n in range(10)]

    value = build(
        ASSERTION_SCOPE, {"total": 55, "resolved": 0, "unresolved": 55}, rows,
    )

    assert validated(value) == value
    assert len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) <= 8192


def test_builder_keeps_escaped_and_control_heavy_samples_inside_storage_limit():
    """WHY: UTF-8 바이트가 작아도 quote·backslash·제어문자는 JSON 직렬화에서 팽창한다."""
    noisy = '\\"\x00\n' * 100
    rows = [issue(
        reason=INSTRUMENT_NOT_FOUND, role=noisy, expression=noisy,
        count=10 - n, article_id=noisy, title=noisy,
    ) for n in range(10)]

    value = build(
        ASSERTION_SCOPE, {"total": 55, "resolved": 0, "unresolved": 55}, rows,
    )

    assert validated(value) == value
    assert len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()) <= 8192


def test_builder_replaces_control_only_display_fields_with_safe_labels():
    """WHY: 원천 표본이 제어문자뿐이어도 진단 생성이 런을 죽이거나 빈 표시를 저장하면 안 된다."""
    value = build(
        ASSERTION_SCOPE, {"total": 1, "resolved": 0, "unresolved": 1},
        [issue(
            reason=INSTRUMENT_NOT_FOUND, role="\x00\n", expression="\x00\n", count=1,
            article_id="\x00\n", title="\x00\n",
        )],
    )

    [row] = value["issues"]
    assert row["role"] == "UNKNOWN"
    assert row["expression"] == "UNKNOWN"
    assert row["sample"] == {"articleId": "UNKNOWN", "title": "제목 없음"}
