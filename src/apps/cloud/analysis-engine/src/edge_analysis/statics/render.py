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


def _pp(x: float | None) -> str:
    """로그수익률을 %p 로. **가법 단위** - 표의 합계 검산이 성립하는 유일한 표기다.

    단순수익(exp−1)으로 칸을 채우면 부분의 합이 합계와 안 맞는다: 실측
    (042700 07-31) 갭 +22.91 · 잔여 +4.13 = 27.04 인데 합계 칸은 +27.98 이었다.
    assert 는 로그에서 검산하고 표시는 단순수익이던 것 - 표가 자기가 보여주지
    않는 것을 검산했다.
    """
    return "     —" if x is None else f"{x * 100:+6.2f}"


def simple_pct(logret: float) -> str:
    """로그 → 단순수익. 하루 총수익처럼 **가법이 필요 없는 한 개 값**에만 쓴다."""
    return f"{(math.exp(logret) - 1) * 100:+.2f}%"


def render(rows: list[Row], *, conditional: str = "", top: int = 12) -> str:
    """셀 하나의 최종 표. 몫 합 = 총수익률 검산이 마지막 행에 있다.

    `top` 개만 |몫| 순으로 펼치고 나머지는 한 행으로 접는다. 실측(000660
    07-29): 사건이 78건이라 창이 137개가 됐고, 137행 표는 설명이 아니라 로그다.
    접어도 합계 검산은 그대로 성립한다 (접은 행도 몫·기여·미설명을 합산한다).
    """
    head = (f"{'창':<14}{'시각':<14}{'몫%p':>8}{'처치':<22}"
            f"{'판정':<10}{'기여%p':>8}{'구간%p':>18}{'미설명%p':>10}")
    lines = [head, "─" * len(head)]
    # 판정·기여가 붙은 행은 **접지 않는다** (설명의 본체다). 나머지를 |몫| 순으로.
    keep = [r for r in rows if r.est is not None or r.verdict]
    plain = [r for r in rows if r not in keep]
    show = keep + sorted(plain, key=lambda r: -abs(r.share.log_ret))[:max(top - len(keep), 0)]
    folded = [r for r in rows if r not in show]
    for r in sorted(show, key=lambda r: r.share.window.start):
        w = r.share.window
        span = f"{w.start:%H:%M}–{w.end:%H:%M}" if w.kind != "gap" else "전일→개장"
        iv = (f"[{_pp(r.lo).strip()},{_pp(r.hi).strip()}]"
              if r.lo is not None and r.hi is not None else "—")
        lines.append(f"{w.name:<14}{span:<14}{_pp(r.share.log_ret):>8}"
                     f"{(r.treatment or '—'):<22}{(r.verdict or '—'):<10}"
                     f"{_pp(r.est):>8}{iv:>18}{_pp(r.unexplained):>10}")
    if folded:
        lines.append(f"{f'…나머지 {len(folded)}창':<14}{'접음':<14}"
                     f"{_pp(sum(r.share.log_ret for r in folded)):>8}{'—':<22}{'—':<10}"
                     f"{_pp(sum(r.est or 0.0 for r in folded)):>8}{'—':>18}"
                     f"{_pp(sum(r.unexplained for r in folded)):>10}")
    total = sum(r.share.log_ret for r in rows)
    est = sum(r.est or 0.0 for r in rows)
    unexp = sum(r.unexplained for r in rows)
    lines.append("─" * len(head))
    lines.append(f"{'합계':<14}{'':<14}{_pp(total):>8}{'':<22}{'':<10}"
                 f"{_pp(est):>8}{'':>18}{_pp(unexp):>10}")
    lines.append(f"단위: 로그수익률 %p (가법) · 하루 단순수익 {simple_pct(total)}")
    if conditional:
        lines.append(f"조건: {conditional}")
    out = "\n".join(lines)
    assert abs(total - (est + unexp)) < 1e-9    # 표 자체가 검산이다
    return out


__all__ = ["Row", "render"]
