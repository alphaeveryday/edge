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
    """가격을 보고 쓰인 기사는 코드가 판별할 수 없다 - 적힌 것은 통계 주장에서 빠진다.

    프롬프트가 "적힌 간선은 통계 주장에서 제외된다"고 약속한다. `timing` 만 바꿔 검정에
    보내면 그 약속을 지키는 게이트가 없어 사후 해설이 사건 원인으로 게시된다 - 간선은
    사슬에 남기고(예산의 blocked 로 세어진다) 설계에서만 뺀다.
    """
    prop = A.parse(_base([
        {"from": "M@t0", "to": "AR@t0", "kind": "statistical", "says": "귀속",
         "exposure": "x", "reverse_risk": "장중 급락을 언급한 사후 해설"}]))

    assert prop.edges[0]["timing"] == "price_responsive"   # 사슬에는 남는다
    assert prop.designs == [], "역인과 위험이 적힌 간선이 검정 설계로 넘어갔다"


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


# --------------------------------------------------------------------------- #
# 예산에 들어가는 수는 **원장을 지난 것만** — 되꽂기 경로의 방어
# --------------------------------------------------------------------------- #
class _Proof:
    """`verify.EdgeProof` 의 되꽂기 계약만 흉내낸 스텁."""

    def __init__(self, design, effect, *, status="통과", gate_fail=()):
        self.design = design
        self.effect = effect
        self.null_sd = 0.01
        self.status = status
        self.gate_fail = list(gate_fail)


def _stat_prop():
    return A.parse(_base([
        {"from": "M@t0", "to": "AR@t0", "kind": "statistical", "says": "귀속",
         "exposure": "event_type_code = 'X'", "reference": "industry_name = 'Bio'"}]))


def test_a_gate_failed_proof_is_not_written_back_into_the_chain():
    """게이트가 기각한 수가 예산 몫을 가져가면 **멀쩡한 간선이 초과로 죽는다.**

    WHY: `verify._pack` 은 게이트 실패에도 `R['effect']` 를 보존한다(p 만 지운다) - 감사에
    남겨야 하기 때문이다. 그 값을 되꽂으면 기각된 간선이 '측정됨'이 되어 잔차를 나눠 갖고,
    다중 간선 설명에서 정상 간선이 예산 초과로 기각된다.
    """
    prop = _stat_prop()
    proof = _Proof(prop.designs[0], 0.05, status="게이트실패", gate_fail=["G2 n 부족"])

    out = A.measured(prop, [proof])

    assert out.chain[0].effect is None, "게이트 실패 증명이 사슬에 되꽂혔다"


def test_an_impossible_proof_is_not_written_back_either():
    """`불가` 는 '못 쟀다'다 - 그 자리에 수가 들어가면 미측정이 측정으로 위장된다."""
    prop = _stat_prop()
    proof = _Proof(prop.designs[0], 0.05, status="불가")

    assert A.measured(prop, [proof]).chain[0].effect is None


def test_a_passed_proof_is_written_back_so_the_budget_can_be_computed():
    """반대 방향 - 통과한 증명까지 막으면 예산 정합이 아예 계산되지 않는다."""
    prop = _stat_prop()

    out = A.measured(prop, [_Proof(prop.designs[0], 0.05)])

    assert out.chain[0].effect is not None
    assert out.chain[0].effect.mid == pytest.approx(0.05)


def test_a_statistical_effect_typed_by_the_model_is_dropped():
    """통계 간선의 수치는 **모델이 쓸 수 없다.**

    WHY: 채워서 오면 `measured()` 가 '이미 측정됨'으로 보고 덮지 않는다 - 원장을 지나지
    않은 구간이 예산 계산에 들어가 검정된 경로를 과대·과소 평가한다. 스키마에 `effect`
    칸이 남아 있으므로 코드가 버려야 한다.
    """
    prop = A.parse(_base([
        {"from": "M@t0", "to": "AR@t0", "kind": "statistical", "says": "귀속",
         "exposure": "event_type_code = 'X'", "effect": [0.02, 0.09],
         "source": "내 추정"}]))

    assert prop.chain[0].effect is None, "모델이 타이핑한 통계 배수가 살아남았다"
    # 버렸으므로 검정 결과가 그 칸을 채울 수 있다.
    out = A.measured(prop, [_Proof(prop.designs[0], 0.05)])
    assert out.chain[0].effect.mid == pytest.approx(0.05)


def test_a_non_object_node_spec_is_a_contract_violation_not_a_crash():
    """비객체 노드 명세를 건너뛰면 **런이 죽는다.**

    WHY: 건너뛰면 그 제안이 Proposal 로 살아 나가고, `graph.validate` 의 접지 순회가
    `(m or {}).get(...)` 에서 AttributeError 로 샌다. 그건 `except PipelineError` 되먹임
    경로 밖이라 깨진 제안 하나가 유니버스 전체 런을 FAILED 시킨다(ALPHA-633 과 같은 비대칭).
    """
    for bad in ("설명 문자열", ["events"], 7):
        out = _base([{"from": "M@t0", "to": "AR@t0", "kind": "statistical",
                      "says": "귀속", "exposure": "x"}])
        out["nodes"]["M@t0"] = bad
        with pytest.raises(PipelineError, match="객체가 아니다"):
            A.parse(out)
