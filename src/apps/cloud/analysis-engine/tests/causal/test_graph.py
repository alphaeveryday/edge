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


def test_latent_mediator_must_declare_which_effect_it_claims():
    """"접어라"가 아니라 "무엇을 주장하나"다 - CDE 는 do-계산으로 식별된다."""
    nodes = {"M@t-1": {"kind": "MECHANISM"}, "X@t-2": {"kind": "TARGET"},
             "Y@t0": {"kind": "TARGET"}}
    dag = {"nodes": nodes, "structures": [{"id": "A", "edges": [
        {"from": "X@t-2", "to": "M@t-1", "timing": "n/a"},
        {"from": "M@t-1", "to": "Y@t0", "timing": "n/a"}]}]}

    assert any("effect=CDE" in v for v in G.validate(dag))

    nodes["M@t-1"]["effect"] = "NDE"
    assert any("seq_ignorability" in v for v in G.validate(dag))

    nodes["M@t-1"]["effect"] = "CDE"
    assert not any("M@t-1" in v for v in G.validate(dag))


@pytest.mark.parametrize("timing", ["scheduled", "unscheduled", "price_responsive", "n/a"])
def test_timing_replaces_manipulability_gate(timing):
    """조작가능성이 아니라 역인과가 관심사다 - 속성도 원인일 수 있다(Bollen-Pearl Myth 3)."""
    dag = {"nodes": {"A@t-2": {"kind": "TARGET"}, "B@t0": {"kind": "TARGET"}},
           "structures": [{"id": "A", "edges": [
               {"from": "A@t-2", "to": "B@t0", "timing": timing}]}]}

    assert not any("timing" in v for v in G.validate(dag))


def _price_pair(residualized=None, with_market=False):
    """가격→가격 간선 하나. 교란 통제 규칙의 최소 재현."""
    nodes = {"SK@t0": {"kind": "OBSERVABLE"}, "ETF@t0": {"kind": "TARGET"}}
    if residualized is not None:
        nodes["SK@t0"]["residualized"] = residualized
    edges = [{"from": "SK@t0", "to": "ETF@t0"}]
    if with_market:
        nodes["MARKET@t0"] = {"kind": "OBSERVABLE"}
        edges.append({"from": "MARKET@t0", "to": "SK@t0"})
    return {"nodes": nodes, "structures": [{"id": "A", "edges": edges}]}


def _violations(dag):
    return [v for v in G.validate(dag, require_competing=False) if "가격 노드끼리" in v]


def test_bare_price_to_price_edge_is_rejected():
    """두 가격은 시장 요인에 함께 흔들려 그 간선이 인과가 아니다."""
    assert _violations(_price_pair())


def test_market_parent_satisfies_the_rule():
    """시장을 명시적으로 모형에 넣으면 교란이 통제된다."""
    assert not _violations(_price_pair(with_market=True))


def test_residualized_true_satisfies_the_rule():
    assert not _violations(_price_pair(residualized=True))


def test_residualized_string_does_not_satisfy_the_rule():
    """문자열 "false" 는 truthy 다 - 통제 규칙이 그걸로 우회되면 안 된다.

    프롬프트가 불리언을 요구하지만 모델은 문자열을 낼 수 있다. 통제 규칙은 닫힌 쪽으로
    실패해야 한다 - 우회를 허용하면 교란된 간선이 인과로 게시된다.
    """
    assert _violations(_price_pair(residualized="false"))
    assert _violations(_price_pair(residualized="true"))


def test_malformed_node_id_is_a_violation_not_a_crash():
    """LLM 오타가 런을 죽이면 안 된다.

    모델이 `KODEX_반도체@t`(오프셋 없음)를 냈고, 규칙 검사 뒤쪽의 `parse` 재호출에서
    ValueError 가 밖으로 튀어 파이프라인이 exit 1 로 죽었다. 형식 오류는 되먹임 대상이다.
    """
    dag = {"nodes": {"BAD@t": {"kind": "OBSERVABLE"}, "ETF@t0": {"kind": "TARGET"}},
           "structures": [{"id": "A", "edges": [{"from": "BAD@t", "to": "ETF@t0"}]}]}

    violations = G.validate(dag, require_competing=False)

    assert any("시간 색인" in v for v in violations)
