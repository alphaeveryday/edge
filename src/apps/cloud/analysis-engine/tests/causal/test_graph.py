"""ADMG·식별 규칙 테스트.

여기서 지키는 불변식은 하나다: **양방향 간선은 공짜가 아니다.** 미지의 공통원인을
선언하면 조정으로는 식별이 불가능해지고, 검정 가능한 함의도 줄어든다. 둘 다 코드가
계산해야 한다 - 안 그러면 에이전트가 양방향을 뿌려 반증을 피한다.
"""

import pytest

from edge_analysis.causal import graph as G


def test_adjustment_closes_backdoor_when_confounder_is_measured():
    edges = [("S@t-3", "X@t-2"), ("S@t-3", "Y@t0"), ("X@t-2", "Y@t0")]

    assert G.admg_backdoor_ok(edges, [], "X@t-2", "Y@t0", {"S@t-3"})[0]
    assert not G.admg_backdoor_ok(edges, [], "X@t-2", "Y@t0", set())[0]


def test_conditioning_on_descendant_of_treatment_is_rejected():
    """사후변수 조정은 편향을 만든다. 매개를 통제하면 총효과가 사라진다."""
    edges = [("X@t-2", "M@t-1"), ("M@t-1", "Y@t0"), ("X@t-2", "Y@t0")]

    ok, why = G.admg_backdoor_ok(edges, [], "X@t-2", "Y@t0", {"M@t-1"})

    assert not ok
    assert "후손" in why


def test_bidirected_edge_makes_adjustment_impossible():
    """X <-> Y 는 관측변수로 막을 수 없다 - 조정 전략이 원리적으로 죽는다."""
    ok, why = G.admg_backdoor_ok([("X@t-2", "Y@t0")], [("X@t-2", "Y@t0")],
                                 "X@t-2", "Y@t0", set())

    assert not ok
    assert "IV" in why
    assert G.admg_minimal_backdoor([("X@t-2", "Y@t0")], [("X@t-2", "Y@t0")],
                                   "X@t-2", "Y@t0", {"S@t-3"}) == []


def test_instrument_is_found_only_when_exclusion_restriction_holds():
    """IV 의 전부는 배제제약이다 - Z 가 X 를 통하지 않고 Y 에 닿으면 도구가 아니다."""
    d = [("Z@t-9", "X@t-2"), ("X@t-2", "Y@t0")]
    b = [("X@t-2", "Y@t0")]

    assert G.iv_candidates(d, b, "X@t-2", "Y@t0", {"Z@t-9"}) == ["Z@t-9"]
    leaky = [*d, ("Z@t-9", "Y@t0")]
    assert G.iv_candidates(leaky, b, "X@t-2", "Y@t0", {"Z@t-9"}) == []


def test_implied_ci_basis_size_equals_missing_edge_count():
    """Pearl 의 완비성: 잠재 없는 DAG 의 필요 검정 수 = 빠진 간선 수."""
    nodes = {f"n{i}@t{i}": {"kind": "OBSERVABLE"} for i in range(5)}
    ns = sorted(nodes)
    edges = [(ns[0], ns[1]), (ns[1], ns[2]), (ns[0], ns[3])]
    missing = 5 * 4 // 2 - len(edges)

    assert len(G.implied_ci(nodes, edges, testable_only=False)) == missing


def test_time_reversal_is_rejected():
    """시간 선행 한 줄이 비순환을 보장한다 - 순환 검사를 따로 두지 않는다."""
    dag = {"nodes": {"A@t0": {"kind": "SHOCK", "tau": "2026-07-29T09:00:00",
                              "member_events": ["e1"]},
                     "B@t-4": {"kind": "TARGET"}},
           "structures": [{"id": "A", "edges": [
               {"from": "A@t0", "to": "B@t-4", "timing": "unscheduled"}]}]}

    bad = G.validate(dag, grounded={"e1"})

    assert any("시간 역행" in v for v in bad)


def test_node_kinds_are_gone_and_grounding_is_checked_by_content():
    """종별 열거를 없앤 자리에 **내용 기반 접지**가 들어왔다.

    이전 판은 노드마다 `kind` 를 6종 중에서 고르게 하고, `MECHANISM` 이면 CDE/NDE/NIE 를
    선언하게 했다. 사슬의 매개(회계 항목·기대·수급)는 그 6칸에 안 맞는 것이 대부분이라
    모델이 노드를 만드는 대신 칸을 채웠다. 지금은 자연어(`says`)와 관측 여부만 받고,
    검사는 **사건을 참조한 노드가 실재하는 event_id 를 가리키는지**로 옮겼다.
    """
    nodes = {"COST@t-1": {"says": "원가율", "observed": "재무제표"},
             "EVT@t-2": {"says": "공시", "observed": "공시 원장", "events": ["e1"]},
             "AR@t0": {"says": "당일 초과수익", "observed": "일간 수익률"}}
    dag = {"nodes": nodes, "structures": [{"id": "A", "edges": [
        {"from": "EVT@t-2", "to": "COST@t-1", "timing": "n/a"},
        {"from": "COST@t-1", "to": "AR@t0", "timing": "n/a"}]}]}

    # 종별을 하나도 안 적었지만 통과한다 - 자유도가 규칙에 걸리지 않는다.
    assert G.validate(dag, grounded={"e1"}, require_competing=False) == []

    # 접지는 여전히 강제된다. 실재하지 않는 event_id 는 날조다.
    nodes["EVT@t-2"]["events"] = ["없는id"]
    assert any("접지 실패" in v
               for v in G.validate(dag, grounded={"e1"}, require_competing=False))

    # 사건을 참조한 노드가 아예 없으면 이 셀의 설명이 저장소와 이어지지 않는다.
    nodes["EVT@t-2"].pop("events")
    assert any("접지된 노드가 없다" in v
               for v in G.validate(dag, grounded={"e1"}, require_competing=False))


@pytest.mark.parametrize("timing", ["scheduled", "unscheduled", "price_responsive", "n/a"])
def test_timing_replaces_manipulability_gate(timing):
    """조작가능성이 아니라 역인과가 관심사다 - 속성도 원인일 수 있다(Bollen-Pearl Myth 3)."""
    dag = {"nodes": {"A@t-2": {"kind": "TARGET"}, "B@t0": {"kind": "TARGET"}},
           "structures": [{"id": "A", "edges": [
               {"from": "A@t-2", "to": "B@t0", "timing": timing}]}]}

    assert not any("timing" in v for v in G.validate(dag))
