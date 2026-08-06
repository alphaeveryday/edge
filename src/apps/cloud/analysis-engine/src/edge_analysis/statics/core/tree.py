"""시간 분해 트리 — 크기는 여기서 확정된다. 추정이 아니라 산술이다.

r_today = 갭 + Σ_k 창_k.  로그수익률의 망원 합이라 **정확히** 성립한다(합=1).
이 몫은 서술("언제 얼마나 움직였나")이지 인과("없었다면")가 아니다 — 인과는
게이트(gates.py)가 통과시킨 창에만 붙고, 탈락한 창의 몫은 미설명으로 남는다.

분해가 종속변수를 만들고 상한을 준다: 식별집합 = CI(구조 추정) ∩ (0, 창의 몫].
시간 분할 자체가 식별 장치다 — 15분/390분 → 표준오차 ≈ 1/5, 같은 날
타 사건 배제, 동일일 다중 사건 분리. (설계 §9–§10)

경계 가격 규약: B(t) = t 직전 마지막 봉의 종가, 개장 전이면 첫 봉의 종가.
갭 = ln(첫 봉 종가 / 전일 종가), 창 = ln(B(end)/B(start)) — 이 규약이면
갭과 첫 장중 창이 이중계상 없이 정확히 이어진다.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from datetime import datetime

from .windows import Window


@dataclass(frozen=True, slots=True)
class Share:
    """창 하나의 몫 (로그수익률, %p 로 렌더). 무편향 — 인과 주장 없음."""
    window: Window
    log_ret: float

    @property
    def pct(self) -> float:
        return (math.exp(self.log_ret) - 1.0) * 100.0


def decompose(bars: list[tuple[datetime, float]], prev_close: float,
              windows: list[Window]) -> list[Share]:
    """5분봉 (시각, 종가) + 전일 종가 → 창별 로그수익률.

    합 = ln(마지막 종가 / 전일 종가) 이 부동소수 오차 안에서 정확히 성립한다.
    빈 창(그 구간에 봉 없음)은 몫 0 — 침묵이 아니라 0 이라는 값이다.
    """
    if prev_close <= 0:
        raise ValueError("전일 종가가 0 이하")
    if not bars:
        raise ValueError("봉이 없다")
    bars = sorted(bars)
    ts = [b[0] for b in bars]
    px = [b[1] for b in bars]

    def boundary(t: datetime) -> float:
        """B(t): t 직전 마지막 봉 종가. 개장 전 경계는 첫 봉 종가로 고정된다 —
        전일 종가 → 첫 봉 종가 구간은 갭의 몫이기 때문이다."""
        i = bisect.bisect_left(ts, t)
        return px[i - 1] if i > 0 else px[0]

    out: list[Share] = []
    for w in windows:
        if w.kind == "gap":
            out.append(Share(w, math.log(px[0] / prev_close)))
        else:
            p0, p1 = boundary(w.start), boundary(w.end)
            out.append(Share(w, math.log(p1 / p0)))

    total = math.log(px[-1] / prev_close)
    s = sum(x.log_ret for x in out)
    assert abs(s - total) < 1e-9, (s, total)   # 항등식 — 깨지면 창이 서로소가 아니다
    return out


def _selfcheck() -> None:
    from .windows import build_windows
    o, c = datetime(2026, 7, 15, 9, 0), datetime(2026, 7, 15, 15, 30)
    ws = build_windows(o, c, [(datetime(2026, 7, 15, 10, 0), "e1")])
    bars = [(datetime(2026, 7, 15, 9, 0), 101.0),
            (datetime(2026, 7, 15, 9, 55), 100.5),
            (datetime(2026, 7, 15, 10, 5), 99.0),
            (datetime(2026, 7, 15, 15, 25), 98.0)]
    shares = decompose(bars, prev_close=100.0, windows=ws)
    assert abs(sum(s.log_ret for s in shares) - math.log(0.98)) < 1e-12
    gap = shares[0]
    assert gap.window.kind == "gap" and abs(gap.log_ret - math.log(1.01)) < 1e-12
    ev = next(s for s in shares if s.window.kind == "event")
    assert abs(ev.log_ret - math.log(99.0 / 100.5)) < 1e-12   # 창 [10:00,10:15)
    # 사건이 하나도 없어도 분해는 성립한다 (갭 + 잔여 하나).
    ws0 = build_windows(o, c, [])
    s0 = decompose(bars, 100.0, ws0)
    assert abs(sum(x.log_ret for x in s0) - math.log(0.98)) < 1e-12


_selfcheck()

__all__ = ["Share", "decompose"]
