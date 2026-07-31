"""제안 계약 — **기계가 검사하는 것은 프롬프트에서 빠졌다. 그 검사를 여기서 고정한다.**

프롬프트를 압축한 근거가 이 파일이다. 유형별 필수 항목·출처 없는 수치·결론 노드 추론을
코드가 잡아내므로, 시스템 프롬프트가 그 목록을 다시 나열하지 않아도 된다. 검사가 사라지면
프롬프트만 짧아지고 규율은 없어지므로, 이 테스트가 그 교환의 유효성을 지킨다.
"""
from __future__ import annotations

import pytest

from edge_analysis.causal import agents as A
from edge_analysis.config import PipelineError


def _base(edges):
    return {"target": "AR@t0",
            "nodes": {"EVT@t-1": {"says": "공시", "observed": "공시 원장",
                                  "events": ["e1"], "value": [0.3, 0.3]},
                      "M@t0": {"says": "기대 변화", "observed": None},
                      "AR@t0": {"says": "당일 초과수익", "observed": "일간 수익률"}},
            "edges": edges, "missing": []}


def test_a_number_without_a_source_is_refused_because_it_cannot_be_checked():
    """수치를 금지하는 대신 **출처 대조로 죽인다.**

    연역 사슬에서는 수치가 본질이라 금지하면 사슬 자체가 불가능하다. 그래서 금지를 풀고,
    검정 세션이 조회할 수 없는 값만 막는다.
    """
    edge = {"from": "EVT@t-1", "to": "M@t0", "kind": "elasticity",
            "says": "증액이 기대를 올린다", "effect": [0.8, 1.0]}
    with pytest.raises(PipelineError, match="source"):
        A.parse(_base([edge]))

    ok = A.parse(_base([{**edge, "source": "직전 사업연도 배당총액(공시)"}]))
    assert ok.chain[0].effect.hi == 1.0


def test_each_kind_must_carry_what_its_proof_needs():
    """유형이 증명 양식을 정하므로 필수 항목도 유형마다 다르다."""
    with pytest.raises(PipelineError, match="formula"):
        A.parse(_base([{"from": "EVT@t-1", "to": "M@t0", "kind": "identity",
                        "says": "정의상 같다"}]))

    with pytest.raises(PipelineError, match="exposure"):
        A.parse(_base([{"from": "M@t0", "to": "AR@t0", "kind": "statistical",
                        "says": "기대가 가격을 움직였다"}]))

    # 못 재는 자리는 `needs` 로 남기면 통과한다 - 데이터 부재는 기각 사유가 아니다.
    got = A.parse(_base([{"from": "M@t0", "to": "AR@t0", "kind": "statistical",
                          "says": "기대가 가격을 움직였다",
                          "needs": "투자자별 순매수 일별 원장"}]))
    assert got.needs == ["투자자별 순매수 일별 원장"]


def test_only_statistical_edges_become_verification_designs():
    """항등식·탄력성에 코호트를 짜면 계산을 검정하는 것이다."""
    prop = A.parse(_base([
        {"from": "EVT@t-1", "to": "M@t0", "kind": "elasticity", "says": "전파",
         "effect": [0.5, 0.5], "source": "재무제표"},
        {"from": "M@t0", "to": "AR@t0", "kind": "statistical", "says": "귀속",
         "exposure": "event_type_code = 'X'", "reference": "industry_name = 'Y'"}]))

    assert [(d.src, d.dst) for d in prop.designs] == [("M@t0", "AR@t0")]
    assert prop.designs[0].treated and prop.designs[0].control
    # 고객이 읽을 원인 이름은 **사슬의 뿌리**다 - 중간 매개가 아니라 사건이다.
    assert prop.designs[0].cause_label == "공시"


def test_the_conclusion_node_must_be_unique_or_the_budget_is_undefined():
    """결론이 둘이면 무엇의 예산인지 정해지지 않는다."""
    two = {"nodes": {"A@t-1": {"says": "a"}, "B@t0": {"says": "b"},
                     "C@t0": {"says": "c"}},
           "edges": [{"from": "A@t-1", "to": "B@t0", "kind": "elasticity",
                      "says": "x", "effect": [1, 1], "source": "s"},
                     {"from": "A@t-1", "to": "C@t0", "kind": "elasticity",
                      "says": "y", "effect": [1, 1], "source": "s"}]}
    with pytest.raises(PipelineError, match="종점이"):
        A.parse(two)

    # 하나면 `target` 을 안 적어도 추론한다 - 받을 수 있는 것을 되묻지 않는다.
    one = dict(two, edges=two["edges"][:1])
    assert A.parse(one).target == "B@t0"


def test_reverse_causality_risk_is_carried_because_code_cannot_detect_it():
    """가격을 보고 쓰인 기사는 코드가 판별할 수 없다 - 적힌 것만 통계 주장에서 뺀다."""
    prop = A.parse(_base([
        {"from": "M@t0", "to": "AR@t0", "kind": "statistical", "says": "귀속",
         "exposure": "x", "reverse_risk": "장중 급락을 언급한 사후 해설"}]))

    assert prop.edges[0]["timing"] == "price_responsive"
    assert prop.designs[0].timing == "price_responsive"


def test_the_brief_carries_facts_and_no_instruction_about_where_to_look():
    """결론과 참조집합을 미리 정해주는 줄이 브리프에 남으면 자유도가 사라진다."""
    text = A.brief(etf_name="X", trade_date="2026-07-30", observed=0.05,
                   residual=0.042, route_code="CONCENTRATED",
                   contributors=[("삼성전자", 0.02)],
                   candidates=[{"event_type_code": "T", "label": "L",
                                "measures": [{"role_code": "AMOUNT",
                                              "surface": "1,883억원",
                                              "basis": "TOTAL",
                                              "value_source": "DART"}]}])

    assert "설명 예산" in text and "1,883억원" in text
    for banned in ("방향을 못 쓴다", "되도록", "판단해라", "빈 간선"):
        assert banned not in text
