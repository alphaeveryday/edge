"""5분 단위 괴리율과 수익률 분해 — NAV 가 있으면 선제적으로, 없으면 즉시 넘어간다.

ETF 하루 수익률은 **두 몫의 합**이다(로그, 항등식):

    ln(P_t / P_0) = ln(iNAV_t / NAV_0)  +  ln((1+괴리_t) / (1+괴리_0))
                    └ 바스켓이 움직인 몫    └ ETF 값만 따로 움직인 몫(수급)

`etf_nav_daily` 는 **일 단위**라 장중 NAV 가 없다. 그래서 iNAV 를 만든다:
전일 NAV 를 앵커로 두고 구성종목의 전일종가→분봉종가 누적수익을 가중합한다. 밤사이
갭이 이 안에 들어온다(구성종목 전일종가를 기준으로 하므로).

**데이터가 없으면 사유 한 줄만 남기고 넘어간다** — 괴리는 선제적 부가정보이고, 이것이
없다고 셀 설명이 멈추면 안 된다. 넘어가는 조건 넷:

    1. 전일 NAV 앵커 없음        (etf_nav_daily 커버리지 밖)
    2. 그 날 ETF 5분봉 없음      (창을 만들 수 없다)
    3. 구성종목 스냅샷 없음      (바스켓을 만들 수 없다)
    4. 5분봉 있는 구성종목 가중합 < MIN_COVER  (반쪽 바스켓은 괴리가 아니라 결측이다)

실측(2026-08): NAV 33종목 × 15일. 그중 5분봉이 함께 있는 것은 **091160 하나**다.
나머지 32종목은 조건 2 로 즉시 넘어간다 - 그것이 정상 동작이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .duck import CausalLake

# 바스켓 가중 커버리지 바닥. 이 밑이면 괴리가 아니라 결측을 괴리로 읽는 것이다.
MIN_COVER = 0.60

_SQL = """
WITH w AS (
  SELECT h.constituent_instrument_id AS iid, h.weight_ratio AS wt
  FROM rdb.public.etf_holding_snapshot h
  WHERE h.etf_instrument_id = '{eid}'
    AND h.trade_date = (SELECT max(trade_date) FROM rdb.public.etf_holding_snapshot
                        WHERE etf_instrument_id = '{eid}' AND trade_date <= DATE '{day}')
    AND h.weight_ratio > 0
),
sym AS (SELECT instrument_id AS iid, ticker || '.KS' AS symbol FROM rdb.public.instrument),
prev AS (  -- 구성종목 전일종가: 밤사이 갭이 여기로 들어온다
  SELECT p.instrument_id AS iid, p.close_price AS c0
  FROM rdb.public.price_daily p
  WHERE p.trade_date = (SELECT max(trade_date) FROM rdb.public.price_daily
                        WHERE trade_date < DATE '{day}')
    AND p.close_price > 0
),
bars AS (
  SELECT b.ts, s.iid, w.wt, ln(b.close / prev.c0) AS cum
  FROM bars_5m b
  JOIN sym  s    ON s.symbol = b.symbol
  JOIN w         ON w.iid = s.iid
  JOIN prev      ON prev.iid = s.iid
  WHERE CAST(b.ts AS DATE) = DATE '{day}' AND b.close > 0
)
SELECT e.ts, e.close,
       sum(bars.wt * bars.cum) / sum(bars.wt) AS bk_cum,
       sum(bars.wt) / (SELECT sum(wt) FROM w)  AS cover
FROM (SELECT ts, close FROM bars_5m
      WHERE symbol = '{tk}' AND CAST(ts AS DATE) = DATE '{day}' AND close > 0) e
JOIN bars ON bars.ts = e.ts
GROUP BY 1, 2 ORDER BY 1
"""

_ANCHOR = """
SELECT (SELECT instrument_id FROM rdb.public.instrument WHERE ticker = '{base}' LIMIT 1),
       (SELECT nav FROM rdb.public.etf_nav_daily
        WHERE etf_instrument_id = (SELECT instrument_id FROM rdb.public.instrument
                                  WHERE ticker = '{base}' LIMIT 1)
          AND trade_date < DATE '{day}' ORDER BY trade_date DESC LIMIT 1),
       (SELECT close_price FROM rdb.public.price_daily
        WHERE instrument_id = (SELECT instrument_id FROM rdb.public.instrument
                               WHERE ticker = '{base}' LIMIT 1)
          AND trade_date < DATE '{day}' AND close_price > 0
        ORDER BY trade_date DESC LIMIT 1)
"""


@dataclass(frozen=True, slots=True)
class Win:
    """5분 창 하나. `r_etf == r_bk + d_prem` 이 항등식으로 성립한다."""

    ts: str
    premium: float   # 그 시각의 괴리율 (P/iNAV - 1)
    r_etf: float     # 직전 창 대비 ETF 로그수익
    r_bk: float      # 그중 바스켓 몫
    d_prem: float    # 그중 괴리 변화 몫
    cover: float     # 그 시각 바스켓 가중 커버리지


@dataclass(frozen=True, slots=True)
class Split:
    """선택 구간의 두 몫. `total == basket + premium_move` (로그 항등식)."""

    total: float
    basket: float
    premium_move: float
    prem_open: float
    prem_last: float
    wins: tuple[Win, ...]

    @property
    def line(self) -> str:
        d = "바스켓" if abs(self.basket) >= abs(self.premium_move) else "수급(ETF 고유)"
        return (f"5분 괴리 분해: 선택 구간 {self.total * 100:+.2f}%p = "
                f"바스켓 {self.basket * 100:+.2f}%p + 괴리변화 "
                f"{self.premium_move * 100:+.2f}%p · 주도 {d} · "
                f"괴리 {self.prem_open * 100:+.2f}%→{self.prem_last * 100:+.2f}% "
                f"· 창 {len(self.wins)}개")


def premium_5m(lake: CausalLake, ticker: str, day: str, *,
               window_start: str | None = None,
               window_end: str | None = None) -> tuple[Split | None, str]:
    """(분해, 사유). 선택 시각을 주면 `[window_start, window_end)`만 돌려준다.

    괴리는 **선제적 부가정보**다. 이것 때문에 셀 설명이 멈추면 안 되므로 부재는
    전부 사유 문자열로 흐른다(호출자가 그 줄을 산문에 남긴다).
    """
    if lake.exists.get("rdb") is not True:
        return None, "괴리 5분 분해 넘어감 — RDB 부재"
    if not lake.exists.get("bars_5m"):
        return None, "괴리 5분 분해 넘어감 — 5분봉 부재"
    base = ticker.split(".")[0]
    try:
        anc = lake.sql(_ANCHOR.format(base=base, day=day))
        eid, nav0, p0 = (anc[0] if anc else (None, None, None))
        if eid is None:
            return None, f"괴리 5분 분해 넘어감 — {base} 종목 미등록"
        if nav0 is None:
            return None, f"괴리 5분 분해 넘어감 — {day} 직전 NAV 없음 (커버리지 밖)"
        if p0 is None:
            return None, f"괴리 5분 분해 넘어감 — {day} 직전 ETF 종가 없음"
        rows = lake.sql(_SQL.format(eid=eid, day=day, tk=f"{base}.KS"))
    except Exception as exc:                       # noqa: BLE001 - 부재는 사유로 흐른다
        return None, f"괴리 5분 분해 넘어감 — 질의 실패: {type(exc).__name__}: {exc}"
    if len(rows) < 2:
        return None, (f"괴리 5분 분해 넘어감 — {ticker} {day} 5분봉·바스켓 교집합 "
                      f"{len(rows)}창 (2창 미만)")

    nav0, p0 = float(nav0), float(p0)
    cover = min(float(r[3]) for r in rows)
    if cover < MIN_COVER:
        return None, (f"괴리 5분 분해 넘어감 — 바스켓 가중 커버리지 {cover * 100:.0f}% "
                      f"< {MIN_COVER * 100:.0f}% (반쪽 바스켓은 결측이다)")

    prem0 = p0 / nav0 - 1.0
    wins: list[Win] = []
    prev_cum, prev_prem, prev_px = 0.0, prem0, p0
    for ts, px, bk, cov in rows:
        px, bk = float(px), float(bk)
        prem = px / (nav0 * math.exp(bk)) - 1.0
        wins.append(Win(str(ts), prem, math.log(px / prev_px), bk - prev_cum,
                        math.log((1 + prem) / (1 + prev_prem)), float(cov)))
        prev_cum, prev_prem, prev_px = bk, prem, px

    total = math.log(prev_px / p0)
    sp = Split(total, prev_cum, math.log((1 + prev_prem) / (1 + prem0)),
               prem0, prev_prem, tuple(wins))
    # 항등식은 **검산되어야** 한다 - 안 맞으면 두 몫이 하루를 설명하지 않는다
    if abs(sp.total - sp.basket - sp.premium_move) > 1e-9:
        return None, ("괴리 5분 분해 넘어감 — 항등식 검산 실패 "
                      f"({sp.total:.6f} ≠ {sp.basket:.6f} + {sp.premium_move:.6f})")
    if window_start is not None or window_end is not None:
        if window_start is None or window_end is None:
            return None, "괴리 5분 분해 넘어감 — 요청창 시작·종료가 함께 필요하다"
        picked = tuple(w for w in sp.wins
                       if window_start <= w.ts[11:19] < window_end)
        if not picked:
            return None, (f"괴리 5분 분해 넘어감 — 요청창 {window_start[:5]}~"
                          f"{window_end[:5]} 교집합 0창")
        first = sp.wins.index(picked[0])
        prem_open = sp.prem_open if first == 0 else sp.wins[first - 1].premium
        sp = Split(
            sum(w.r_etf for w in picked),
            sum(w.r_bk for w in picked),
            sum(w.d_prem for w in picked),
            prem_open,
            picked[-1].premium,
            picked,
        )
    return sp, sp.line


def _selfcheck() -> None:
    # 항등식: 창마다 r_etf == r_bk + d_prem, 하루도 같다
    nav0, p0 = 100.0, 101.0                      # 괴리 +1%
    prem0 = p0 / nav0 - 1
    rows = [("t1", 103.0, 0.01, 1.0), ("t2", 104.0, 0.03, 1.0)]
    prev_cum, prev_prem, prev_px, ws = 0.0, prem0, p0, []
    for ts, px, bk, cov in rows:
        prem = px / (nav0 * math.exp(bk)) - 1
        ws.append(Win(ts, prem, math.log(px / prev_px), bk - prev_cum,
                      math.log((1 + prem) / (1 + prev_prem)), cov))
        prev_cum, prev_prem, prev_px = bk, prem, px
    for w in ws:
        assert abs(w.r_etf - w.r_bk - w.d_prem) < 1e-12, w
    sp = Split(math.log(prev_px / p0), prev_cum,
               math.log((1 + prev_prem) / (1 + prem0)), prem0, prev_prem, tuple(ws))
    assert abs(sp.total - sp.basket - sp.premium_move) < 1e-12
    assert abs(sp.total - sum(w.r_etf for w in ws)) < 1e-12, "창의 합이 하루다"
    assert "주도 바스켓" in sp.line and "괴리 +1.00%→" in sp.line


if __name__ == "__main__":                        # pragma: no cover
    import sys

    _selfcheck()
    lk = CausalLake()
    tk, day = sys.argv[1], sys.argv[2]
    sp, note = premium_5m(lk, tk, day)
    print(note)
    for w in (sp.wins if sp else ())[:12]:
        print(f"  {w.ts}  괴리 {w.premium * 100:+6.3f}%  "
              f"ETF {w.r_etf * 100:+6.3f}%p = 바스켓 {w.r_bk * 100:+6.3f}%p "
              f"+ 괴리 {w.d_prem * 100:+6.3f}%p  (cover {w.cover * 100:.0f}%)")
