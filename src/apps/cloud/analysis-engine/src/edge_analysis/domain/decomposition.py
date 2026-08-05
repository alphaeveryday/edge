"""ETF 등락 분해와 라우팅 — 도메인 모델 위의 순수 함수.

proxy 등락은 가격 보유 부분집합에 한정한 ``Σ(비중·등락) / Σ(비중)``(커버리지 정규화)로,
파이프라인 트리거 writer 와 같은 산식이다 — 발화한 트리거와 그 설명이 일관되게 분해되도록.
"""
from __future__ import annotations

from collections.abc import Mapping

from .models import Decomposition, Holding, Member

# top-1 집중도가 이 비율 이상이면 (단일 종목 주도) 집중 설명으로, 아니면 공통요인으로 라우팅.
CONCENTRATION_THRESHOLD = 0.5


def compute_decomposition(
    holdings: list[Holding], returns: Mapping[str, float | None]
) -> Decomposition:
    """ETF 등락을 구성종목별 기여로 분해한다."""
    total_weight = sum(h.weight for h in holdings)
    members: list[Member] = []
    weighted_return_sum = covered_weight = 0.0
    for h in holdings:
        ret = returns.get(h.ticker)
        if ret is None:  # 가격 없는 종목은 proxy·커버리지에서 제외한다.
            continue
        contribution = h.weight * ret
        members.append(
            Member(ticker=h.ticker, name=h.name, weight=h.weight, ret=ret,
                   contribution=contribution, rank=0)
        )
        weighted_return_sum += contribution
        covered_weight += h.weight

    # |기여| 내림차순으로 순위 부여. frozen dataclass 라 rank 를 넣어 다시 만든다.
    members.sort(key=lambda m: abs(m.contribution), reverse=True)
    members = [
        Member(m.ticker, m.name, m.weight, m.ret, m.contribution, rank)
        for rank, m in enumerate(members, 1)
    ]

    total_abs = sum(abs(m.contribution) for m in members)
    return Decomposition(
        members=members,
        proxy_ret=(weighted_return_sum / covered_weight) if covered_weight > 0 else None,
        covered_weight=covered_weight,
        total_weight=total_weight,
        coverage=(covered_weight / total_weight) if total_weight > 0 else 0.0,
        top1=(abs(members[0].contribution) / total_abs) if members and total_abs > 0 else None,
        top3=(sum(abs(m.contribution) for m in members[:3]) / total_abs)
        if members and total_abs > 0
        else None,
        advancing=sum(1 for m in members if m.ret > 0),
        total_priced=len(members),
        n_constituents=len(holdings),
    )

