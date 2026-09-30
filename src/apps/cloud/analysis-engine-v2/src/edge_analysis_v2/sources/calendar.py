"""KRX 2026 sessions matching infra/terraform/envs/dev/main.tf kr_holidays."""
from datetime import date, timedelta

HOLIDAYS = frozenset('''2026-01-01 2026-02-16 2026-02-17 2026-02-18 2026-03-02
2026-05-01 2026-05-05 2026-05-25 2026-06-03 2026-08-17 2026-09-24 2026-09-25
2026-10-05 2026-10-09 2026-12-25 2026-12-31'''.split())


def trading_dates(start, end):
    """Return inclusive expected sessions, failing outside the registered year.

    Args:
        start: Inclusive ISO calendar date.
        end: Inclusive ISO calendar date.

    Returns:
        Ordered ISO trading dates; no inference from price or flow observations.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.year != 2026 or last.year != 2026 or first > last:
        raise ValueError('Only the registered 2026 KRX calendar is supported')
    days = [first+timedelta(days=i) for i in range((last-first).days+1)]
    return [d.isoformat() for d in days if d.weekday()<5 and d.isoformat() not in HOLIDAYS]
