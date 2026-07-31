"""주입 가능한 clock (계획 §4·§6).

요청 함수 안에서 현재 시각으로 window 를 다시 계산하지 않는다 — clock 은 scheduler 와
테스트가 주입한다. 벽시계 타이밍 단언은 가상 시계로 축을 교체한다는 기존 원칙과 같은 결.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} 은 timezone-aware 여야 한다")
    return value


class VirtualClock:
    """테스트·시뮬레이션용 가상 시계. 뒤로는 가지 않는다."""

    def __init__(self, start: datetime) -> None:
        self._now = _require_aware(start, "start")

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("시계는 뒤로 가지 않는다")
        self._now += delta
        return self._now


class SystemClock:
    """운영용 실제 시계 — VirtualClock 과 같은 now() 계약(UTC aware)."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)
