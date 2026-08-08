"""ETF 기계 프레임 — 하루치 변동이 흐를 수 있는 경로의 전부를 코드로 고정한다.

가설 에이전트는 이 프레임 밖의 경로를 주장할 수 없고, 정적 분석기는 이 프레임의
어느 노드가 데이터로 채워져 있는지(coverage)를 침묵하지 않고 보고한다 —
P1 지문의 "부재는 선언한다" 규율과 같은 계보.

심장 분기: NAV 경로(바스켓이 움직였다 → 종목 이야기로 하강) vs
괴리 경로(ETF 만 움직였다 → 수급, 되돌아옴 — 유일하게 ETF 고유·거래 가능).

설계: docs/analysis-engine/causal-attribution-design.md §2.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── 노드 ────────────────────────────────────────────────────────────────
# (id, 데이터 소스, 현재 상태) — 상태는 실측(2026-08-01) 기준이며 duck.coverage() 가 갱신한다.
SOURCES: dict[str, str] = {
    "US_OVERNIGHT": "전일 미국장·야간선물",
    "MACRO": "금리·환율·유가",
    "POLICY": "정책·규제",
    "UPSTREAM": "전방수요·경쟁사·원자재",
    "FIRM_EVENT": "기업 고유사건",
    "ETF_FLOW": "ETF 설정·환매·개인수급",
    "FX_RATE": "환율",
}
MEDIATORS: dict[str, str] = {
    "COMMON": "공통요인",
    "THEME": "테마·섹터 충격",
    "EXPECTATION": "기대 갱신",
    "PREMIUM": "괴리 경로",
    "FX_PATH": "FX 경로",
}
TERMINALS: dict[str, str] = {
    "R_I": "구성종목 수익률",
    "NAV": "NAV 경로 Σw·r",
    "ETF_PRICE": "ETF 가격",
}

# ── 간선 — 이 밖의 경로는 프레임 위반이다 ───────────────────────────────
EDGES: frozenset[tuple[str, str]] = frozenset({
    ("US_OVERNIGHT", "COMMON"), ("MACRO", "COMMON"), ("MACRO", "THEME"),
    ("POLICY", "THEME"), ("POLICY", "EXPECTATION"), ("UPSTREAM", "THEME"),
    ("FIRM_EVENT", "EXPECTATION"),
    ("COMMON", "R_I"), ("THEME", "R_I"), ("EXPECTATION", "R_I"),
    ("R_I", "NAV"), ("NAV", "ETF_PRICE"),
    ("ETF_FLOW", "PREMIUM"), ("PREMIUM", "ETF_PRICE"),
    ("FX_RATE", "FX_PATH"), ("FX_PATH", "ETF_PRICE"),
})

# ── 정적 제거 규칙 ──────────────────────────────────────────────────────
# 기준은 하나: 종목별 노출의 **부호가 갈리는가.** 안 갈리면(시장 β — 거의 전부 +)
# 기계적이고 서사가 없으므로 뺀다. 갈리면(금리·환율·유가: 차입 −/예대 +,
# 수출 +/수입 −) 이야기가 있으므로 채널로 남긴다 — 빼면 "금리 때문"이라는
# 답이 원리적으로 불가능해진다(설명 가능성 소각).
STATIC_REMOVE: frozenset[str] = frozenset({"COMMON"})
KEEP_AS_CHANNEL: frozenset[str] = frozenset({"THEME", "EXPECTATION"})


@dataclass(frozen=True, slots=True)
class PathVerdict:
    """한 셀에서 세 경로의 몫. 괴리 판정은 뺄셈 하나다:
    premium = 시장가 수익률 − NAV 수익률."""
    nav_return: float
    price_return: float

    @property
    def premium_return(self) -> float:
        return self.price_return - self.nav_return

    @property
    def basket_moved(self) -> bool:
        """바스켓이 움직였나 — 참이면 종목 이야기로 하강, 거짓이면 수급."""
        return abs(self.nav_return) >= abs(self.premium_return)


def validate_edge(src: str, dst: str) -> None:
    """프레임 밖 경로 주장은 생성 시점에 죽는다."""
    if (src, dst) not in EDGES:
        known = SOURCES | MEDIATORS | TERMINALS
        for n in (src, dst):
            if n not in known:
                raise ValueError(f"프레임에 없는 노드: {n}")
        raise ValueError(f"프레임에 없는 경로: {src} → {dst}")


def _selfcheck() -> None:
    # 모든 간선의 끝점이 선언된 노드이고, ETF_PRICE 로 들어오는 경로는 정확히 셋.
    nodes = SOURCES.keys() | MEDIATORS.keys() | TERMINALS.keys()
    assert all(s in nodes and d in nodes for s, d in EDGES)
    into_price = {s for s, d in EDGES if d == "ETF_PRICE"}
    assert into_price == {"NAV", "PREMIUM", "FX_PATH"}, into_price
    assert STATIC_REMOVE < MEDIATORS.keys()
    v = PathVerdict(nav_return=-0.02, price_return=-0.021)
    assert v.basket_moved and abs(v.premium_return - (-0.001)) < 1e-12


_selfcheck()

__all__ = ["EDGES", "KEEP_AS_CHANNEL", "MEDIATORS", "PathVerdict", "SOURCES",
           "STATIC_REMOVE", "TERMINALS", "validate_edge"]
