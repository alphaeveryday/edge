"""검정 에이전트 — 튜플의 결정론 전개와 타입 수준 패널 게이트. LLM 없음.

설계 §17: 검정자는 고르지 않는다. 창·컷·표본·유의수준은 전역 상수(vocab)이고,
대상군·위약군은 튜플의 노출원에서 **유도**된다. 여기 함수들이 그 유도의 전부다.

패널의 논리(§4 용량-반응): 같은 타입의 과거 사건들에서, 노출 상위가 부호
방향으로 더 크게 반응했는가. 귀무는 사건일 **안**에서 노출 라벨을 섞는 순열
(층=날짜 — 날짜를 가로지르면 귀무 분산이 공통충격으로 부푼다. verify G7 계보).

감사 5라운드의 교훈이 그대로 계약이다:
  선언=배선   여기 적힌 클램프·상수는 전부 아래 SQL·코드에 있다
  PIT        event_date < day AND available_at <= day — 오늘이 패널에 못 들어간다
  부재 선언   못 재는 노출·방아쇠는 판정불가 + 사유. 침묵·기각 위장 금지
  결정론     SEED 고정 — 같은 셀 재실행 = 같은 판정 (레지스트리 재현 원칙)

지금 잴 수 있는 노출은 price_daily(3.7년)에서 나오는 3족뿐이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .gates import EdgeVerdict, edge_gate
from .vocab import EXPOSURE_CUT, HypothesisTuple, MIN_N

PERMS = 1000        # 전역 상수 - 가설별 지정 금지 (§13)
SEED = 0

# (계열족, 변환) → 사건 전 20거래일(lag 1) 노출 식. **여기 없는 조합은 아직 못 잰다.**
_EXPOSURE = {
    ("가격잔차", "누적"): "sum(p.ar)",
    ("거래량", "수준"): "avg(p.turnover_value)",
    ("가격잔차", "변동성"): "stddev_samp(p.log_return)",
}

_PANEL_SQL = """
WITH r AS (
    SELECT instrument_id, trade_date, log_return, turnover_value,
           log_return - avg(log_return) OVER (PARTITION BY trade_date) AS ar,
           row_number() OVER (PARTITION BY instrument_id ORDER BY trade_date) AS rn
    FROM rdb.public.price_daily
    WHERE log_return IS NOT NULL
),
ev AS (
    SELECT DISTINCT ea.entity_id AS iid, se.event_date AS d
    FROM rdb.public.source_event se
    JOIN rdb.public.event_argument ea ON ea.source_event_id = se.source_event_id
    JOIN rdb.public.instrument i ON i.instrument_id = ea.entity_id
    WHERE se.event_type_code = '{etype}' AND se.event_status = 'ACTIVE'
      AND se.event_date < DATE '{day}'
      AND se.available_at <= TIMESTAMP '{day} 00:00:00'
),
resp AS (
    SELECT ev.iid, ev.d, r.ar, r.rn
    FROM ev JOIN r ON r.instrument_id = ev.iid AND r.trade_date = ev.d
)
SELECT resp.iid, resp.d, resp.ar,
       (SELECT {expr} FROM r p
        WHERE p.instrument_id = resp.iid AND p.rn BETWEEN resp.rn - 20 AND resp.rn - 1) AS x
FROM resp
"""

_TODAY_SQL = """
WITH r AS (
    SELECT instrument_id, trade_date, log_return, turnover_value,
           log_return - avg(log_return) OVER (PARTITION BY trade_date) AS ar,
           row_number() OVER (PARTITION BY instrument_id ORDER BY trade_date) AS rn
    FROM rdb.public.price_daily WHERE log_return IS NOT NULL
),
me AS (SELECT rn FROM r WHERE instrument_id = '{iid}' AND trade_date = DATE '{day}')
SELECT {expr} FROM r p, me
WHERE p.instrument_id = '{iid}' AND p.rn BETWEEN me.rn - 20 AND me.rn - 1
"""


@dataclass(frozen=True, slots=True)
class EdgeReport:
    """엣지 하나의 패널 판정. 수치는 전부 이 모듈이 계산했다 - 모델 손을 안 거친다."""
    verdict: EdgeVerdict
    n: int
    p: float | None
    effect_high: float | None       # 노출 상위(≥컷)의 평균 ar
    effect_low: float | None
    today_exposure_pct: float | None  # 오늘 셀 종목의 노출 백분위 (패널 분포 대비)
    reason: str = ""

    @property
    def line(self) -> str:
        if self.verdict == "판정불가":
            return f"판정불가 (n={self.n}) — {self.reason}"
        hi = f"{self.effect_high * 100:+.2f}%" if self.effect_high is not None else "?"
        lo = f"{self.effect_low * 100:+.2f}%" if self.effect_low is not None else "?"
        te = (f" · 오늘 노출 p{self.today_exposure_pct * 100:.0f}"
              if self.today_exposure_pct is not None else "")
        return f"{self.verdict} (n={self.n}, p={self.p:.3f}, 상위 {hi} vs 하위 {lo}{te})"


def _unmeasurable(reason: str) -> EdgeReport:
    return EdgeReport("판정불가", 0, None, None, None, None, reason)


def edge_test(lake, t: HypothesisTuple, day: str,
              cell_instrument_id: str = "") -> EdgeReport:
    """튜플 → 패널 검정. 표본이 얇으면 판정불가 — **다른 표본을 찾으러 가지 않는다.**"""
    if t.trigger.kind != "점":
        return _unmeasurable("계열 방아쇠 패널은 이 판에 없다 - 점 사건만 검정한다")
    if t.exposure.kind != "속성":
        return _unmeasurable("관계 노출 전개는 이 판에 없다 - 속성 노출만 검정한다")
    expr = _EXPOSURE.get((t.exposure.ident, t.exposure.transform))
    if expr is None:
        return _unmeasurable(
            f"노출 ({t.exposure.ident},{t.exposure.transform}) 는 아직 못 잰다 - "
            f"재는 것: {sorted(_EXPOSURE)}")

    rows = [(str(i), str(d), float(a), float(x))
            for i, d, a, x in lake.sql(_PANEL_SQL.format(
                etype=t.trigger.ident, day=day, expr=expr))
            if a is not None and x is not None]
    if len(rows) < MIN_N:
        return EdgeReport("판정불가", len(rows), None, None, None, None,
                          f"패널 n={len(rows)} < {MIN_N}")

    ar = np.array([r[2] for r in rows])
    x = np.array([r[3] for r in rows])
    dates = np.array([r[1] for r in rows])
    pct = np.argsort(np.argsort(x)) / max(len(x) - 1, 1)
    high = pct >= EXPOSURE_CUT
    if high.sum() < 3 or (~high).sum() < 3:
        return EdgeReport("판정불가", len(rows), None, None, None, None,
                          "노출 분산 부족 - 상·하위가 갈리지 않는다 (게이트 A)")

    sign = float(t.sign)
    obs = float(ar[high].mean() - ar[~high].mean()) * sign
    rng = np.random.default_rng(SEED)
    null = np.empty(PERMS)
    for k in range(PERMS):
        perm = high.copy()
        for d in np.unique(dates):                        # 층 = 사건일
            m = dates == d
            perm[m] = rng.permutation(perm[m])
        null[k] = (ar[perm].mean() - ar[~perm].mean()) * sign
    p = float((null >= obs).mean())

    today = None
    if cell_instrument_id:
        row = lake.sql(_TODAY_SQL.format(iid=cell_instrument_id, day=day, expr=expr))
        if row and row[0][0] is not None:
            today = float((x <= float(row[0][0])).mean())

    return EdgeReport(edge_gate(len(rows), p), len(rows), p,
                      float(ar[high].mean()), float(ar[~high].mean()), today)


__all__ = ["EdgeReport", "PERMS", "SEED", "edge_test"]
