from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from edge_analysis.domain.window import (
    AggregatedBar,
    AnalysisReason,
    CommittedMinuteWindow,
    MinuteBar,
    WindowSpec,
)

KST = timezone(timedelta(hours=9))
DAY = date(2026, 8, 7)


def at(hour: int, minute: int, *, tz=KST, second: int = 0) -> datetime:
    return datetime(2026, 8, 7, hour, minute, second, tzinfo=tz)


def committed(minute: int, *, generation: int = 1) -> CommittedMinuteWindow:
    start = at(10, minute)
    return CommittedMinuteWindow(
        "session-1", start, start + timedelta(minutes=1), generation, "a" * 64)


def test_window_spec_is_open_to_requested_kst_minute():
    spec = WindowSpec(" KR ", " session-1 ", DAY, at(9, 0), at(10, 17))

    assert (spec.market, spec.session_id) == ("KR", "session-1")
    assert spec.end == at(10, 17)


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (at(9, 1), at(10, 0), "09:00"),
        (at(9, 0), at(9, 0), "15:30"),
        (at(9, 0), at(15, 31), "15:30"),
        (at(9, 0, tz=None), at(10, 0), "KST"),
        (at(9, 0), at(10, 0, second=1), "분 경계"),
    ],
)
def test_window_spec_rejects_wrong_axis(start, end, message):
    with pytest.raises(ValueError, match=message):
        WindowSpec("KR", "session-1", DAY, start, end)


def test_window_spec_rejects_a_different_session_date():
    with pytest.raises(ValueError, match="session_date"):
        WindowSpec("KR", "session-1", DAY - timedelta(days=1), at(9, 0), at(10, 0))


def test_committed_window_requires_one_minute_generation_and_sha256():
    row = committed(15, generation=2)
    assert row.end - row.start == timedelta(minutes=1)
    assert row.generation == 2

    with pytest.raises(ValueError, match="1분"):
        CommittedMinuteWindow("s", at(10, 15), at(10, 17), 1, "a" * 64)
    with pytest.raises(ValueError, match="1 이상"):
        CommittedMinuteWindow("s", at(10, 15), at(10, 16), 0, "a" * 64)
    with pytest.raises(ValueError, match="sha256"):
        CommittedMinuteWindow("s", at(10, 15), at(10, 16), 1, "A" * 64)


def test_minute_bar_preserves_decimal_ohlcv_and_lineage():
    source = committed(15)
    bar = MinuteBar(source, "005930", Decimal("100"), Decimal("103"),
                    Decimal("99"), Decimal("102"), Decimal("1234"))

    assert bar.source is source and bar.close == Decimal("102")


@pytest.mark.parametrize(
    ("ohlcv", "message"),
    [
        ((Decimal("100"), Decimal("99"), Decimal("98"), Decimal("100"), Decimal("1")),
         "모순"),
        ((Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("-1")),
         "비음수"),
        ((Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("0")),
         "무거래"),
        ((Decimal("NaN"), Decimal("101"), Decimal("99"), Decimal("100"), Decimal("1")),
         "유한"),
    ],
)
def test_minute_bar_rejects_invalid_ohlcv(ohlcv, message):
    with pytest.raises(ValueError, match=message):
        MinuteBar(committed(15), "005930", *ohlcv)


def test_partial_aggregate_requires_contiguous_source_windows():
    sources = tuple(committed(m) for m in (15, 16, 17))
    bar = AggregatedBar(
        "005930", at(10, 15), at(10, 18), 3,
        Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), Decimal("30"),
        sources,
    )

    assert bar.observed_minutes == 3 and bar.sources == sources

    with pytest.raises(ValueError, match="연속"):
        AggregatedBar(
            "005930", at(10, 15), at(10, 18), 3,
            Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), Decimal("30"),
            (committed(15), committed(17), committed(18)),
        )


def test_aggregate_rejects_duration_lineage_or_session_mismatch():
    with pytest.raises(ValueError, match="봉 길이"):
        AggregatedBar(
            "005930", at(10, 15), at(10, 19), 3,
            Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), Decimal("30"),
            tuple(committed(m) for m in (15, 16, 17)),
        )
    with pytest.raises(ValueError, match="source window 수"):
        AggregatedBar(
            "005930", at(10, 15), at(10, 18), 3,
            Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), Decimal("30"),
            (committed(15), committed(16)),
        )

    foreign = CommittedMinuteWindow(
        "session-2", at(10, 17), at(10, 18), 1, "b" * 64)
    with pytest.raises(ValueError, match="session_id"):
        AggregatedBar(
            "005930", at(10, 15), at(10, 18), 3,
            Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), Decimal("30"),
            (committed(15), committed(16), foreign),
        )


def test_analysis_reason_is_machine_readable_and_human_readable():
    reason = AnalysisReason("WINDOW_CORRECTING", "정정 세대 커밋 대기", retryable=True)
    assert reason.retryable is True

    with pytest.raises(ValueError, match="UPPER_SNAKE_CASE"):
        AnalysisReason("window-correcting", "대기")
    with pytest.raises(ValueError, match="message"):
        AnalysisReason("WINDOW_CORRECTING", " ")
