"""렌더 — 한 표. 합계 검산이 표 안에 있고, 미설명이 1급 행이다.

조건 문장 규율(설계 §14): 사전 고정 목록의 조건만 · 교호항 유의할 때만 ·
반사실 쌍으로 · positivity(반대 사례 有) 없으면 침묵.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .gates import EdgeVerdict
from .tree import Share


@dataclass(frozen=True, slots=True)
class Row:
    share: Share
    treatment: str = ""                 # 배정된 처치 (게이트 통과 시)
    verdict: EdgeVerdict | str = ""     # 성립·불성립·판정불가 (사건 창만)
    est: float | None = None            # 구조방정식 기여 (로그수익률)
    lo: float | None = None
    hi: float | None = None

    @property
    def unexplained(self) -> float:
        """창의 몫 − 추정 기여. 게이트 탈락 창은 몫 전부가 미설명이다."""
        return self.share.log_ret - (self.est or 0.0)


def _pct(x: float | None) -> str:
    return "     —" if x is None else f"{(math.exp(x) - 1) * 100:+6.2f}"


def render(rows: list[Row], *, conditional: str = "") -> str:
    """셀 하나의 최종 표. 몫 합 = 총수익률 검산이 마지막 행에 있다."""
    head = (f"{'창':<14}{'시각':<14}{'몫%p':>8}{'처치':<22}"
            f"{'판정':<10}{'기여%p':>8}{'구간%p':>18}{'미설명%p':>10}")
    lines = [head, "─" * len(head)]
    for r in rows:
        w = r.share.window
        span = f"{w.start:%H:%M}–{w.end:%H:%M}" if w.kind != "gap" else "전일→개장"
        iv = (f"[{_pct(r.lo).strip()},{_pct(r.hi).strip()}]"
              if r.lo is not None and r.hi is not None else "—")
        lines.append(f"{w.name:<14}{span:<14}{_pct(r.share.log_ret):>8}"
                     f"{(r.treatment or '—'):<22}{(r.verdict or '—'):<10}"
                     f"{_pct(r.est):>8}{iv:>18}{_pct(r.unexplained):>10}")
    total = sum(r.share.log_ret for r in rows)
    est = sum(r.est or 0.0 for r in rows)
    unexp = sum(r.unexplained for r in rows)
    lines.append("─" * len(head))
    lines.append(f"{'합계':<14}{'':<14}{_pct(total):>8}{'':<22}{'':<10}"
                 f"{_pct(est):>8}{'':>18}{_pct(unexp):>10}")
    if conditional:
        lines.append(f"조건: {conditional}")
    out = "\n".join(lines)
    assert abs(total - (est + unexp)) < 1e-9    # 표 자체가 검산이다
    return out


__all__ = ["Row", "render"]
