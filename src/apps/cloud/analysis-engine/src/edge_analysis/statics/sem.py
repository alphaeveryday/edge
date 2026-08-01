"""구조방정식 — 창별 회귀 + 합 제약. 값은 여기서, 크기 상한은 트리에서.

u_{i,k} = Σ_c τ_c(v_i)·x_i^c·D_{e,k} + α_k + ε   s.t.   Σ_k 몫_k = r_today

- 사건 고정효과 α_k: 그날 그 창의 모든 공통 교란을 흡수한다. 대가로 충격
  절대크기 g 도 흡수된다 — 추정되는 것은 **노출 기울기**뿐 (설계 §10, §18).
- 식별집합 = CI(τ̂·x) ∩ (0, 창의 몫]. 구조 추정이 항등식 상한을 넘으면
  모형이 틀린 것이다 — 공짜 검산.
- 순위는 여기(구조 추정)의 산물이고 부트스트랩 구간이 겹치면 동순위다.
  트리 몫에서 읽은 순위는 서술이지 인과가 아니다.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class EdgeEstimate:
    """엣지 하나의 계수와 오늘 기여. 채널은 노드가 아니라 노출의 인덱스다."""
    channel: str
    tau: float                  # 노출 기울기 (사건 고정효과 하)
    se: float
    today_exposure: float

    @property
    def contribution(self) -> float:
        return self.tau * self.today_exposure

    def interval(self, z: float = 1.96) -> tuple[float, float]:
        lo = (self.tau - z * self.se) * self.today_exposure
        hi = (self.tau + z * self.se) * self.today_exposure
        return (min(lo, hi), max(lo, hi))


def exposure_slope(u: np.ndarray, exposure: np.ndarray,
                   event_id: np.ndarray) -> tuple[float, float]:
    """사건 고정효과 하의 노출 기울기 — 사건 내부 편차 회귀.

    사건별 평균을 빼면(within transform) 그날의 모든 공통 교란이 소거된다.
    반환: (τ̂, se). 표본이 얇으면 se 가 커져서 구간이 스스로 넓어진다.
    """
    if not (len(u) == len(exposure) == len(event_id)):
        raise ValueError("길이 불일치")
    uu = np.asarray(u, dtype=float).copy()
    xx = np.asarray(exposure, dtype=float).copy()
    for e in np.unique(event_id):
        m = event_id == e
        uu[m] -= uu[m].mean()
        xx[m] -= xx[m].mean()
    sxx = float(xx @ xx)
    if sxx <= 0.0:
        raise ValueError("노출 분산 0 — 게이트 A 가 먼저 걸렀어야 한다")
    tau = float(xx @ uu) / sxx
    resid = uu - tau * xx
    dof = max(len(uu) - len(np.unique(event_id)) - 1, 1)
    se = float(np.sqrt((resid @ resid) / dof / sxx))
    return tau, se


def clip_to_share(est: EdgeEstimate,
                  share_logret: float) -> tuple[float, float] | None:
    """식별집합 = 구조 추정 구간 ∩ (0, 창의 몫]. 부호는 몫이 정한다.

    **공집합이면 None** — 구조 추정이 항등식 상한과 모순이라는 뜻이고, 이것이
    과대식별 검산의 실패 신호다: 시각은 맞는데 크기가 안 맞으면 채널이 틀렸다.
    None 을 삼키지 말고 그 창을 판정불가로 되돌려라.
    """
    lo, hi = est.interval()
    if share_logret >= 0:
        lo, hi = max(lo, 0.0), min(hi, share_logret)
    else:
        lo, hi = max(lo, share_logret), min(hi, 0.0)
    return (lo, hi) if lo <= hi else None


def rank_with_ties(contribs: dict[str, np.ndarray]) -> list[tuple[str, int]]:
    """부트스트랩 기여 표본 → 순위. 구간이 겹치면 **동순위** — 아니면 날조다.

    contribs: 이름 → 부트스트랩 |기여| 표본 (동일 길이).
    """
    names = list(contribs)
    qs = {n: (float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975)))
          for n, v in contribs.items()}
    order = sorted(names, key=lambda n: -float(np.median(contribs[n])))
    ranks: dict[str, int] = {}
    rank = 1
    for i, n in enumerate(order):
        if i > 0:
            prev = order[i - 1]
            # 겹치면 앞 항목과 같은 순위
            if qs[n][1] >= qs[prev][0] and qs[prev][1] >= qs[n][0]:
                ranks[n] = ranks[prev]
                continue
            rank = i + 1
        ranks[n] = rank
    return [(n, ranks[n]) for n in order]


def _selfcheck() -> None:
    rng = np.random.default_rng(0)
    ev = np.repeat(np.arange(40), 8)
    x = rng.normal(size=ev.size)
    alpha = rng.normal(size=40)[ev] * 3.0          # 큰 사건 고정효과
    u = 0.7 * x + alpha + rng.normal(scale=0.5, size=ev.size)
    tau, se = exposure_slope(u, x, ev)
    assert abs(tau - 0.7) < 0.1 and se < 0.05      # FE 가 α 를 소거했다
    est = EdgeEstimate("FX환", tau, se, today_exposure=1.0)
    lo, hi = clip_to_share(est, share_logret=1.0)
    assert 0.0 <= lo <= hi <= 1.0                  # 상한이 문다
    # 추정(≈0.7)이 몫(0.2)을 넘으면 식별집합이 비고 — 모형 모순 신호다.
    assert clip_to_share(est, share_logret=0.2) is None
    r = rank_with_ties({"a": rng.normal(1.0, 0.01, 500),
                        "b": rng.normal(0.99, 0.01, 500),   # a 와 겹침 → 동순위
                        "c": rng.normal(0.2, 0.01, 500)})
    d = dict(r)
    assert d["a"] == d["b"] == 1 and d["c"] == 3


_selfcheck()

__all__ = ["EdgeEstimate", "clip_to_share", "exposure_slope", "rank_with_ties"]
