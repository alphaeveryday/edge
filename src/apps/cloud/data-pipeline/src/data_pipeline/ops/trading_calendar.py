"""KR 거래일 판정 (ALPHA-530, 스펙 §5·§3.3).

비거래일 가격 수집은 Planner 에서 SKIPPED(NON_TRADING_DAY)로 처리한다. `kr_trading_calendar=True`
인 레인들(시장·장중 수급)의 cron 이 MON-FRI 라(statemachine.tf) 그쪽 주말은 대개 안 들어오고,
**평일 공휴일**이 여기서 잡을 몫이다.
⚠️ 뉴스 레인만은 주 7일 크론이라(ALPHA-874) 주말이 실제로 들어온다 — 다만 뉴스 작업은 전부
`kr_trading_calendar=False` 라 이 판정이 그 실행을 SKIPPED 로 접지 않는다(catalog.py 참조).

⚠️ ponytail: 공휴일 원천 데이터가 코드에 없다(조사 결과). 주말 판정은 확실하고 테스트 가능한
핵심이라 그걸 정본으로 두고, 공휴일은 **오버라이드 가능한 정적 집합**으로 둔다(env
`OPS_KR_HOLIDAYS`=쉼표구분 YYYY-MM-DD, 또는 인자 주입). 완전한 KR 거래소 캘린더 연동은 후속
(pandas-market-calendars 등) — 지금 없는 걸 지어내지 않는다(스펙 §19).
"""

from __future__ import annotations

import os
from datetime import date, timedelta

# 최장 연휴보다 넉넉하다. 이 안에 거래일이 없으면 달력 주입 오류로 보고 fail-loud한다.
MAX_LOOKBACK_DAYS = 10


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


def latest_kr_trading_day(
    day: date,
    holidays: frozenset[str] | None = None,
) -> date:
    """day 이하의 최근 KR 거래일. Planner와 KRX 요청일 계산의 단일 규칙이다."""
    for back in range(MAX_LOOKBACK_DAYS):
        candidate = day - timedelta(days=back)
        if is_trading_day(candidate, holidays):
            return candidate
    raise ValueError(
        f"{day} 부터 {MAX_LOOKBACK_DAYS}일 안에 거래일이 없다 — OPS_KR_HOLIDAYS 주입 확인"
    )
