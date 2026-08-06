"""괴리 판정 — 프레임의 심장 분기를 데이터로 채운다 (설계 §2 문제 1).

트리거가 울린 (ETF, 날짜) 셀마다 시장가 수익률과 NAV 수익률을 맞대 뺄셈 하나로
경로를 가른다:

    바스켓이 움직였다 (|nav| ≥ |premium|) → 종목 이야기. 귀속은 종목으로 하강
    ETF 만 움직였다   (|premium| > |nav|) → 수급. 되돌아옴 — 유일하게 ETF 고유·거래 가능

`etf_contribution_observation.premium_discount_contribution_return` 이 전부 NULL 인
상태를 이 화면이 대신한다 — 추정 없음, 항등식과 뺄셈뿐. LLM 0.

사용:  python -m edge_analysis.statics.window.premium [day0] [day1]
       env: EDGE_RDB_DSN
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass

from ..core.duck import CausalLake
from ..core.frame import PathVerdict

_SQL = """
WITH nav AS (
  SELECT etf_instrument_id AS id, trade_date AS d, nav,
         lag(nav) OVER (PARTITION BY etf_instrument_id ORDER BY trade_date) AS pnav
  FROM rdb.public.etf_nav_daily),
px AS (
  -- close_price (미수정 종가): NAV 도 그날 주당 값이므로 같은 좌표계다.
  -- adjusted 를 쓰면 배당락일에 괴리가 가짜로 튄다.
  SELECT instrument_id AS id, trade_date AS d, close_price AS close,
         lag(close_price) OVER (PARTITION BY instrument_id ORDER BY trade_date) AS pclose
  FROM rdb.public.price_daily),
trg AS (
  SELECT DISTINCT etf_instrument_id AS id, trade_date AS d
  FROM rdb.public.price_movement_trigger)
SELECT trg.id, trg.d,
       ln(px.close / px.pclose)  AS price_ret,
       ln(nav.nav  / nav.pnav)   AS nav_ret
FROM trg
JOIN nav ON nav.id = trg.id AND nav.d = trg.d
JOIN px  ON px.id  = trg.id AND px.d  = trg.d
WHERE nav.pnav IS NOT NULL AND px.pclose IS NOT NULL
  AND trg.d BETWEEN DATE '{d0}' AND DATE '{d1}'
ORDER BY trg.d, trg.id
"""


@dataclass(frozen=True, slots=True)
class Cell:
    etf_id: str
    day: str
    verdict: PathVerdict

    @property
    def line(self) -> str:
        v = self.verdict
        kind = "바스켓" if v.basket_moved else "**수급**"
        return (f"{self.day}  {self.etf_id[:26]:<26} 가격 {v.price_return*100:+6.2f}%p "
                f"NAV {v.nav_return*100:+6.2f}%p 괴리 {v.premium_return*100:+6.2f}%p  {kind}")


def screen(lake: CausalLake, d0: str, d1: str) -> list[Cell]:
    """트리거 울린 셀 전량의 경로 판정. RDB 없으면 즉사 — 침묵 금지."""
    if lake.exists.get("rdb") is not True:
        raise RuntimeError("RDB 부재 — coverage 참조")
    rows = lake.sql(_SQL.format(d0=d0, d1=d1))
    return [Cell(str(i), str(d), PathVerdict(nav_return=float(n), price_return=float(p)))
            for i, d, p, n in rows]


def summarize(cells: list[Cell]) -> str:
    """분포 한 줄 + 괴리 상위 — 거래 가능 후보는 여기서 나온다."""
    if not cells:
        return "셀 없음"
    basket = sum(1 for c in cells if c.verdict.basket_moved)
    prem = len(cells) - basket
    top = sorted(cells, key=lambda c: -abs(c.verdict.premium_return))[:5]
    lines = [f"셀 {len(cells)} = 바스켓 {basket} ({basket/len(cells):.0%}) "
             f"+ 수급 {prem} ({prem/len(cells):.0%})",
             "괴리 상위 5 (수급 후보):"]
    lines += ["  " + c.line for c in top]
    return "\n".join(lines)


def _selfcheck() -> None:
    a = Cell("e", "2026-07-15", PathVerdict(nav_return=-0.020, price_return=-0.021))
    b = Cell("e", "2026-07-16", PathVerdict(nav_return=-0.001, price_return=-0.015))
    assert a.verdict.basket_moved and not b.verdict.basket_moved
    s = summarize([a, b])
    assert "바스켓 1" in s and "수급 1" in s
    assert abs(b.verdict.premium_return - (-0.014)) < 1e-12


_selfcheck()

if __name__ == "__main__":
    d0 = sys.argv[1] if len(sys.argv) > 1 else "2026-07-13"
    d1 = sys.argv[2] if len(sys.argv) > 2 else "2026-07-31"
    print(summarize(screen(CausalLake(), d0, d1)))

__all__ = ["Cell", "screen", "summarize"]
