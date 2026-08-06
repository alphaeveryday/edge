"""라우팅 단일 진입점 — 층 분해 → route_code. **clock 은 필수다.**

clock 을 넘기지 않으면 호출이 실패한다 — "어떤 구간의 움직임을 설명하려는지"를
명시하지 않은 채 라우팅하는 것은 구조적 버그다.

사용:
    from .routing import compute_route

    # 하루 전체
    result = compute_route(lake, ticker, day, clock=(SESSION_OPEN, SESSION_CLOSE))

    # 분봉 모드 (09:00 ~ 요청 window 종료)
    result = compute_route(lake, ticker, day, clock=(SESSION_OPEN, window_end))
"""
from __future__ import annotations

from dataclasses import dataclass

from ..core.layers import decompose as layer_decompose
from .record import route_code_of
from .route import Route, route_etf


@dataclass(frozen=True, slots=True)
class RouteResult:
    """라우팅 계산 결과. pipeline 과 etfcell 이 동일하게 소비한다."""

    route_code: str
    event_search: bool
    roll: object  # layers.Rollup | None — 타입 순환 방지
    rt: Route | None


def compute_route(
    lake,
    ticker: str,
    day: str,
    clock: tuple[str, str],
    *,
    premium=None,
) -> RouteResult:
    """층 분해 → 라우팅. 단일 진입점.

    Parameters
    ----------
    lake : CausalLake
        DuckDB 표면.
    ticker : str
        ETF 티커.
    day : str
        거래일 ISO 문자열.
    clock : tuple[str, str]
        설명 대상 구간. 하루 전체면 ("09:00:00", "15:30:00").
        **필수** — 생략하면 TypeError.
    premium : optional
        괴리 판정. 없으면 괴리단독 분기를 안 탄다.
    """
    roll = layer_decompose(lake, ticker, day, clock=clock)
    if roll is None:
        code, search = route_code_of("")
        return RouteResult(route_code=code, event_search=search, roll=None, rt=None)
    rt = route_etf(roll, premium)
    code, search = route_code_of(rt.kind)
    return RouteResult(route_code=code, event_search=search, roll=roll, rt=rt)
