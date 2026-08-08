"""게이트 — 거절이 기능이다. 항상 답하는 시스템은 반드시 지어낸다.

두 겹의 게이트:
1) 엣지 게이트(3값): 이 (타입×채널×노출) 엣지가 패널 역사에서 서는가.
   표본 부족은 "불성립"이 아니라 **판정불가** — 둘을 접으면 측정 실패가
   자동으로 기각 판정을 만들어낸다(P1 의 부재≠침묵과 같은 규율).
2) 귀속 게이트(A–E): 오늘 이 셀에서 점귀속이 성립하는 조건. 실패 조합이
   산출 형태를 정한다 — 점 / 구간 / 배제 / 거절. (설계 §11–§12)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .vocab import ALPHA, MIN_N

EdgeVerdict = Literal["성립", "불성립", "판정불가"]


def edge_gate(n: int, p_value: float | None, *,
              min_n: int = MIN_N, alpha: float = ALPHA) -> EdgeVerdict:
    """타입 수준 패널 검정의 3값 판정. 크기를 만들지 않는다 — 통과/탈락만."""
    if n < min_n or p_value is None:
        return "판정불가"
    return "성립" if p_value < alpha else "불성립"


@dataclass(frozen=True, slots=True)
class GateInputs:
    """귀속 게이트의 재료. 전부 코드가 계산한다 — 에이전트 판단 없음."""
    exposure_n: int             # 같은 채널 노출 종목 수
    exposure_spread: float      # 노출 분산 (0이면 용량-반응 불가)
    window_separable: bool      # τ 전후 분리 가능, 창 안 경쟁 사건 없음
    pre_drift_sig: bool         # τ 이전 누적 잔차가 유의한가 (예고·유출)
    index_weight: float         # 지수 내 비중 v_i (요인 오염)
    exposure_fixed_before: bool  # 노출이 사건 이전에 고정됐나

Route = Literal["점추정", "구간만", "배제만", "거절"]


def route(g: GateInputs, *, min_peers: int = 5, weight_cap: float = 0.05) -> Route:
    """게이트 통과 조합 → 산출 형태.

    D(요인 오염) 실패는 대형주에서 구조적이다 — 자기 사건이 요인을 움직이므로
    요인을 빼면 효과 일부가 같이 빠진다. 설명 가치가 큰 종목일수록 식별이
    안 된다는 저주가 여기 게이트로 나타난다.
    """
    a = g.exposure_n >= min_peers and g.exposure_spread > 0.0
    b = g.window_separable
    c = not g.pre_drift_sig
    d = g.index_weight < weight_cap
    e = g.exposure_fixed_before

    if not d:
        return "거절"           # 요인 재구성(해당 종목 제외) 전에는 점귀속 금지
    if not (c and e):
        return "배제만"         # 예고됐거나 노출이 내생 — 반증만 정직하다
    if not (a and b):
        return "구간만"         # 용량-반응 또는 시각 분리 결손 — 부분식별
    return "점추정"


def _selfcheck() -> None:
    assert edge_gate(10, 0.001) == "판정불가"          # N 부족은 기각이 아니다
    assert edge_gate(100, None) == "판정불가"
    assert edge_gate(100, 0.001) == "성립"
    assert edge_gate(100, 0.20) == "불성립"
    ok = GateInputs(20, 0.4, True, False, 0.01, True)
    assert route(ok) == "점추정"
    assert route(GateInputs(20, 0.4, True, False, 0.30, True)) == "거절"      # 대형주
    assert route(GateInputs(20, 0.4, True, True, 0.01, True)) == "배제만"     # 예고
    assert route(GateInputs(2, 0.0, True, False, 0.01, True)) == "구간만"     # 노출 결손


_selfcheck()

__all__ = ["EdgeVerdict", "GateInputs", "Route", "edge_gate", "route"]
