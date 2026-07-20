"""ETF-move decomposition and routing — pure functions over domain models.

The proxy return is ``Σ(weight·return) / Σ(weight)`` restricted to the priced
subset (coverage-normalized), matching the pipeline's trigger writer so a fired
trigger and its explanation decompose consistently.
"""
from __future__ import annotations

from collections.abc import Mapping

from .models import Decomposition, Holding, Member

# top-1 concentration at or above this fraction routes to a concentrated
# (single-name-driven) explanation rather than a common-factor one.
CONCENTRATION_THRESHOLD = 0.5


def compute_decomposition(
    holdings: list[Holding], returns: Mapping[str, float | None]
) -> Decomposition:
    """Decompose the ETF move into per-constituent contributions."""
    total_weight = sum(h.weight for h in holdings)
    members: list[Member] = []
    weighted_return_sum = covered_weight = 0.0
    for h in holdings:
        ret = returns.get(h.ticker)
        if ret is None:
            continue
        contribution = h.weight * ret
        members.append(
            Member(ticker=h.ticker, name=h.name, weight=h.weight, ret=ret,
                   contribution=contribution, rank=0)
        )
        weighted_return_sum += contribution
        covered_weight += h.weight

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


def decide_route(decomp: Decomposition) -> tuple[str, bool]:
    """Return the route code and whether an event (news) search is required."""
    if decomp.top1 is not None and decomp.top1 >= CONCENTRATION_THRESHOLD:
        return "CONCENTRATED", True
    return "COMMON_FACTOR", True
