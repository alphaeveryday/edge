from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from edge_analysis.domain.window import (
    AggregatedBar,
    AnalysisReason,
    CommittedMinuteWindow,
    MinuteBar,
    WindowAggregationError,
    WindowSpec,
    aggregate_window,
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


def minute_bar(minute: int, unit_id: str = "A", *, session: str = "session-1",
               checksum: str = "a" * 64) -> MinuteBar:
    start = at(9, minute)
    source = CommittedMinuteWindow(
        session, start, start + timedelta(minutes=1), 1, checksum)
    price = Decimal(100 + minute)
    return MinuteBar(
        source, unit_id, price, price + 2, price - 1, price + 1, Decimal(minute + 1))


def window(end_minute: int) -> WindowSpec:
    return WindowSpec("KR", "session-1", DAY, at(9, 0), at(9, end_minute))


def test_aggregate_window_builds_complete_five_minute_ohlcv():
    [bar] = aggregate_window(window(5), tuple(minute_bar(m) for m in range(5)))

    assert (bar.start, bar.end, bar.observed_minutes) == (at(9, 0), at(9, 5), 5)
    assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        Decimal("100"), Decimal("106"), Decimal("99"), Decimal("105"), Decimal("15"))
    assert [source.start.minute for source in bar.sources] == [0, 1, 2, 3, 4]


def test_aggregate_window_keeps_the_last_partial_bar():
    bars = aggregate_window(window(7), tuple(minute_bar(m) for m in range(7)))

    assert [(bar.start.minute, bar.end.minute, bar.observed_minutes) for bar in bars] == [
        (0, 5, 5), (5, 7, 2)]
    assert bars[-1].open == Decimal("105")
    assert bars[-1].close == Decimal("107")
    assert bars[-1].volume == Decimal("13")


def test_aggregate_window_uses_one_common_axis_for_every_unit():
    inputs = tuple(
        minute_bar(minute, unit)
        for minute in range(5)
        for unit in ("B", "A")
    )

    bars = aggregate_window(window(5), inputs)

    assert [bar.unit_id for bar in bars] == ["A", "B"]
    assert all(bar.observed_minutes == 5 for bar in bars)


def test_aggregate_window_rejects_missing_or_drifting_units():
    missing = tuple(minute_bar(m) for m in (0, 1, 3, 4))
    with pytest.raises(WindowAggregationError) as caught:
        aggregate_window(window(5), missing)
    assert caught.value.reason.code == "MISSING_MINUTE_BAR"
    assert "A@09:02" in caught.value.reason.message

    drift = tuple(minute_bar(m, "A") for m in range(5)) + tuple(
        minute_bar(m, "B") for m in range(1, 5))
    with pytest.raises(WindowAggregationError) as caught:
        aggregate_window(window(5), drift)
    assert caught.value.reason.code == "MISSING_MINUTE_BAR"
    assert "B@09:00" in caught.value.reason.message


def test_aggregate_window_rejects_duplicate_and_source_mismatch():
    duplicate = (minute_bar(0), minute_bar(0))
    with pytest.raises(WindowAggregationError) as caught:
        aggregate_window(window(1), duplicate)
    assert caught.value.reason.code == "DUPLICATE_MINUTE_BAR"

    different_source = (
        minute_bar(0, "A", checksum="a" * 64),
        minute_bar(0, "B", checksum="b" * 64),
    )
    with pytest.raises(WindowAggregationError) as caught:
        aggregate_window(window(1), different_source)
    assert caught.value.reason.code == "MINUTE_SOURCE_MISMATCH"


def test_aggregate_window_rejects_empty_foreign_session_or_out_of_range():
    with pytest.raises(WindowAggregationError) as caught:
        aggregate_window(window(1), ())
    assert caught.value.reason.code == "EMPTY_MINUTE_BARS"

    with pytest.raises(WindowAggregationError) as caught:
        aggregate_window(window(1), (minute_bar(0, session="foreign"),))
    assert caught.value.reason.code == "MINUTE_SESSION_MISMATCH"

    with pytest.raises(WindowAggregationError) as caught:
        aggregate_window(window(1), (minute_bar(1),))
    assert caught.value.reason.code == "MINUTE_OUT_OF_WINDOW"
