"""거래일 판정 테스트 (ALPHA-530)."""

from __future__ import annotations

from datetime import date

from data_pipeline.ops.trading_calendar import is_trading_day, latest_kr_trading_day


def test_weekday_is_trading_day():
    assert is_trading_day(date(2026, 7, 24), holidays=frozenset())  # 금


def test_weekend_is_not_trading_day():
    assert not is_trading_day(date(2026, 7, 25), holidays=frozenset())  # 토
    assert not is_trading_day(date(2026, 7, 26), holidays=frozenset())  # 일


def test_weekday_holiday_is_not_trading_day():
    assert not is_trading_day(date(2026, 7, 24), holidays=frozenset({"2026-07-24"}))


def test_latest_trading_day_uses_the_same_holiday_set_as_planner():
    """WHY: Planner 기대일과 KRX 요청일의 달력이 갈리면 같은 실행이 서로 다른 날짜를 뜻한다."""
    assert latest_kr_trading_day(
        date(2026, 7, 26), holidays=frozenset({"2026-07-24"})
    ) == date(2026, 7, 23)
