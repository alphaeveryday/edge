"""Reasoning effort of a movement explanation, raised for a chosen set of ETFs.

Outlooks always run at high. A movement explanation runs at low unless its ETF is listed here: on 2026-11-11 a
first explanation at high took about 1.8 times as long (318-385 s of the 480 s limit) and wrote about 1.7 times
as many output tokens as at low. To run every movement at low again, set HIGH_EFFORT_ENABLED to False.

The list is the 30 ETFs whose analysis or movement screen the most app users opened in the 14 days to
2026-11-11 (PostHog, emulators and the Android tester farm excluded), most-viewed first.
"""

HIGH_EFFORT_ENABLED = True
HIGH_EFFORT_ETFS = frozenset({
    '091160',
    '438900',
    '494670',
    '422420',
    '401170',
    '469170',
    '400970',
    '441540',
    '140710',
    '0210A0',
    '494220',
    '069500',
    '0216Z0',
    '091170',
    '305720',
    '139220',
    '157490',
    '0209Z0',
    '401470',
    '469790',
    '315270',
    '0103T0',
    '229200',
    '471990',
    '449450',
    '461950',
    '0117V0',
    '396500',
    '466920',
    '278530',
})


def movement_effort(etf_code: str) -> str:
    """Effort for a movement explanation of one ETF."""
    return 'high' if HIGH_EFFORT_ENABLED and etf_code in HIGH_EFFORT_ETFS else 'low'
