"""사슬 — **간선 유형이 증명 양식을 정하고, 예산이 그래프를 기각한다.**

여기서 지키는 계약은 넷이다.

    1. 항등식과 추정을 섞지 않는다      (오차 없는 자리에 구간이 오면 fail-loud)
    2. 부호가 사슬을 타고 정확히 전파된다 (음의 탄력성에서 조용히 뒤집히지 않는다)
    3. 못 잰 칸은 침묵이 아니라 요청이다  (예측 없음 + needs 가 남는다)
    4. 넓은 구간은 통과가 아니라 무력이다 (가정을 벌려 반증을 피하는 것을 이름으로 구분)
"""
from __future__ import annotations

import pytest

from edge_analysis.causal import chain as C


def _edge(src, dst, kind="elasticity", eff=None, **kw):
    return C.Edge(src=src, dst=dst, kind=kind, says="주장",
                  effect=(C.Interval(*eff) if eff else None),
                  source="공시" if eff and kind != "statistical" else "",
                  formula="a = b * c" if kind == "identity" else "", **kw)


def test_identity_edge_refuses_an_interval_because_it_has_no_error():
    """오차 없는 자리에 폭이 오면 추정을 계산으로 위장한 것이다."""
    with pytest.raises(ValueError, match="항등식"):
        _edge("A@t0", "B@t0", kind="identity", eff=(0.9, 1.1))

    # 점값은 받는다 - 항등식은 계수가 아니라 정의다.
    assert _edge("A@t0", "B@t0", kind="identity", eff=(1.0, 1.0)).measured


def test_negative_elasticity_does_not_silently_flip_the_sign():
    """구간 곱을 `[lo*lo, hi*hi]` 로 줄여 쓰면 음수 계수 하나에서 틀린다.

    사슬에는 음의 탄력성(원가 상승 → 이익 감소)이 흔하므로, 이 실수는 조용히 부호를
    뒤집어 "호재"를 "악재"로 바꾼다.
    """
    got = C.multiply(C.Interval(-0.5, 1.0), C.Interval(2.0, 3.0))
    assert (got.lo, got.hi) == (-1.5, 3.0)

    both = C.multiply(C.Interval(-2.0, -1.0), C.Interval(-3.0, 2.0))
    assert (both.lo, both.hi) == (-4.0, 6.0)


def test_a_chain_multiplies_from_the_event_size_not_from_one():
    """절대 크기는 **사건 노드 한 곳**에서만 들어온다 - 나머지는 배수다."""
    edges = [_edge("EVT@t-1", "M@t0", eff=(0.5, 0.5)),
             _edge("M@t0", "AR@t0", kind="statistical", eff=(0.2, 0.2))]
    ps = C.paths(edges, "AR@t0", {"EVT@t-1": C.Interval(0.30, 0.30)})

    assert len(ps) == 1
    assert ps[0].predict() == C.Interval(0.03, 0.03)     # 0.30 × 0.5 × 0.2
    assert ps[0].kinds == "e→s"


def test_an_unmeasured_step_yields_no_prediction_and_names_what_it_needs():
    """데이터 부재는 기각이 아니다. 예측을 내지 않고 **무엇이 없는지 남긴다.**"""
    edges = [_edge("EVT@t-1", "M@t0", eff=(0.5, 0.5)),
             _edge("M@t0", "AR@t0", kind="statistical", exposure="x",
                   needs="투자자별 순매수 일별 원장")]
    ps = C.paths(edges, "AR@t0")

    assert ps[0].predict() is None
    b = C.budget(ps, residual=-0.04)
    assert b["n_blocked"] == 1 and b["n_measured"] == 0
    assert b["blocked"][0]["needs"] == ["투자자별 순매수 일별 원장"]
    assert b["over_budget"] is False        # 못 잰 것이 그래프를 기각하지는 않는다


def test_attribution_over_the_residual_rejects_the_graph():
    """귀속은 같은 예산을 나눠 쓴다 - 합이 잔차를 넘으면 그래프가 틀렸다.

    타입 수준 모형에서는 여러 원인이 각자 유의미해도 모순이 아니다. 귀속에서는 모순이다.
    이 비대칭이 바텀업 그래프의 가장 값싼 기각 경로다.
    """
    edges = [_edge("A@t-1", "AR@t0", kind="statistical", eff=(-0.03, -0.03)),
             _edge("B@t-1", "AR@t0", kind="statistical", eff=(-0.03, -0.03))]
    ps = C.paths(edges, "AR@t0")

    ok = C.budget(ps, residual=-0.08)
    assert ok["over_budget"] is False and 0.7 < ok["share"] < 0.8

    over = C.budget(ps, residual=-0.02)
    assert over["over_budget"] is True and "넘는다" in over["reason"]
    assert over["unexplained"] == pytest.approx(0.04)   # 남은 폭은 부호까지 그대로 남는다


def test_a_path_that_does_not_reach_the_target_is_not_attribution():
    """결론 노드로 닿지 않는 간선은 구조상 흥미로워도 설명 몫을 못 가져간다."""
    edges = [_edge("A@t-1", "AR@t0", kind="statistical", eff=(-0.01, -0.01)),
             _edge("C@t-1", "D@t0", eff=(1.0, 1.0))]

    ps = C.paths(edges, "AR@t0")
    assert [p.cause for p in ps] == ["A@t-1"]


def test_a_prediction_wider_than_the_volatility_is_powerless_not_passing():
    """가정을 벌려 구간을 넓히면 어떤 관측도 포함된다 - 그 통과는 증거가 아니다."""
    wide = C.Interval(-0.05, 0.05)
    assert C.verdict(wide, observed=0.01, daily_vol=0.02) == "무력"

    tight = C.Interval(0.005, 0.02)
    assert C.verdict(tight, observed=0.01, daily_vol=0.02) == "정합"
    assert C.verdict(tight, observed=0.04, daily_vol=0.02) == "기각"
    assert C.verdict(None, observed=0.01, daily_vol=0.02) == "미측정"


def test_the_widest_step_is_named_so_the_next_collection_knows_where_to_go():
    """실물 효과를 가격까지 전파할 때 최대 불확실성은 크기가 아니라 지속 기간이다."""
    edges = [_edge("EVT@t-1", "MARGIN@t0", eff=(0.95, 1.05)),
             C.Edge(src="MARGIN@t0", dst="VALUE@t0", kind="elasticity",
                    says="개선이 몇 년 지속되는가", source="DCF 가정",
                    effect=C.Interval(0.5, 3.0))]
    p = C.paths(edges, "VALUE@t0", {"EVT@t-1": C.Interval(0.1, 0.1)})[0]

    assert p.widest().says.startswith("개선이 몇 년")
