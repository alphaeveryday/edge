"""KR 거래일 판정 (ALPHA-530, 스펙 §5·§3.3).

비거래일 가격 수집은 Planner 에서 SKIPPED(NON_TRADING_DAY)로 처리한다. 스케줄 cron 이 이미
MON-FRI 라(statemachine.tf) 주말은 대개 안 들어오지만, **평일 공휴일**은 여기서 잡아야 한다.

⚠️ ponytail: 공휴일 원천 데이터가 코드에 없다(조사 결과). 주말 판정은 확실하고 테스트 가능한
핵심이라 그걸 정본으로 두고, 공휴일은 **오버라이드 가능한 정적 집합**으로 둔다(env
`OPS_KR_HOLIDAYS`=쉼표구분 YYYY-MM-DD, 또는 인자 주입). 완전한 KR 거래소 캘린더 연동은 후속
(pandas-market-calendars 등) — 지금 없는 걸 지어내지 않는다(스펙 §19).
"""

from __future__ import annotations

import os
from datetime import date


def _env_holidays() -> frozenset[str]:
    raw = os.environ.get("OPS_KR_HOLIDAYS", "")
    return frozenset(d.strip() for d in raw.split(",") if d.strip())


def is_trading_day(day: date, holidays: frozenset[str] | None = None) -> bool:
    """KR 거래일인가 — 평일이고 공휴일 집합에 없으면 True.

    holidays 미지정이면 env(OPS_KR_HOLIDAYS)에서 읽는다(테스트는 명시 주입).
    """
    if day.weekday() >= 5:  # 5=토, 6=일
        return False
    hset = _env_holidays() if holidays is None else holidays
    return day.isoformat() not in hset
