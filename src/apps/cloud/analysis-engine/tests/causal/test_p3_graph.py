"""P3 세계 그래프 — **그림에 대한 진술이 세계에 대한 진술이 되는 자리를 지킨다.**

고정하는 불변식:
  · `chosen` 배정은 U 를 심고 모델이 그 자리를 덮을 수 없다 (교란 폐쇄)
  · 동시간 간선은 방향을 주장하려면 근거를 적어야 한다
  · 컴파일러를 우회한 그래프는 `validate` 가 되돌린다
  · 거부는 침묵이 아니라 되먹임이다 - 위반 문장이 다음 턴에 돌아간다
  · 3회 안에 못 세우면 **억지로 밀지 않고** 위반을 단 채 넘긴다

되먹임 검사는 삭제된 `test_run_feedback.py` 의 의도를 여기서 다시 세운 것이다. 구 구조는
`agents.propose` 가 되먹임을 받았고, 그 자리는 이제 `p3_graph.build` 다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from edge_analysis.causal.contracts import Hypothesis, Question, WorldGraph
from edge_analysis.causal.p3_graph import MAX_TRIES, build, compile_latents, validate

TREAT, OUT = "BUYBACK@t-1", "AR@t+0"
NODES = {
    TREAT: {"says": "자사주 취득 결정", "observed": None, "events": ["e1"]},
    OUT: {"says": "당일 초과수익", "observed": "종가 기준 초과수익", "events": []},
}
# `kind` 는 필수다 - 없으면 그 간선은 검정을 조용히 빠져나간다(run._designs).
EDGES = [{"from": TREAT, "to": OUT, "kind": "statistical"}]
H = Hypothesis(hid="H1", says="자사주 취득 결정이 당일 초과수익을 만들었다",
               treatment=TREAT, outcome=OUT, assignment="chosen")
Q = Question(etf_instrument_id="091160", etf_name="테스트 ETF", trade_date=date(2026, 7, 16),
             as_of="2026-07-16T15:40:00+09:00", observed=0.0421, residual=0.0300,
             route_code="EVENT", explanandum="r⊥[091160, 2026-07-16] = +3.00%",
             intervention="자사주 취득 공시가 없던 세계", answer_form="구간")


class _Client:
    """대본대로 답하는 그래프 세션 스텁. **매 턴의 user 프롬프트를 모은다** - 되먹임이
    실제로 모델에게 돌아갔는지는 그 문자열로만 확인할 수 있다. 대본이 떨어지면 마지막
    응답을 반복한다(고집 부리는 모델)."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self._turns = list(turns)
        self.users: list[str] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.users.append(user)
        return self._turns[min(len(self.users), len(self._turns)) - 1]


def _payload(*, completeness: str = "처치·결과 두 변수쌍을 훑었고 그 밖의 공통원인은 없다",
             nodes: dict[str, Any] | None = None,
             edges: list[dict[str, Any]] | None = None,
             latents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"graph": {"nodes": NODES if nodes is None else nodes,
                      "edges": EDGES if edges is None else edges,
                      "completeness": completeness, "latents": latents or []}}


# --------------------------------------------------------------------------- #
# 교란 폐쇄 — 배정 기제가 U 를 심고, 모델은 그 자리를 못 건드린다
# --------------------------------------------------------------------------- #
def test_a_mechanical_assignment_plants_no_latent_but_a_chosen_one_does():
    """`chosen` 은 "기업이 미관측 상태를 보고 골랐다"는 선언이고, 처치·결과의 공통원인이
    그 선언 안에 이미 들어 있다. 모델의 성실성에 맡기면 기본값이 무교란이 된다.

    반대로 `mechanical` 까지 심으면 배당락에 사적 정보를 씌우게 된다 - 규칙이 시점과
    내용을 정하는 사건에는 고를 여지가 없으므로 없는 뒷문을 그리는 것이다.
    """
    rule = Hypothesis(hid="H2", says="배당락은 규칙이 값을 정한다", treatment="EXDIV@t-1",
                      outcome=OUT, assignment="mechanical")

    assert compile_latents([rule], []) == []
    assert [(u.source, u.between) for u in compile_latents([H], [])] \
        == [("compiled", (TREAT, OUT))]


def test_a_weak_declared_latent_cannot_overwrite_the_compiled_one_at_the_same_slot():
    """P5 의 소거 설계와 P8 의 상한 문장은 **U 의 문구를 읽는다.** 모델이 같은 자리에
    "경미한 정보 비대칭" 을 써 넣으면 두 단계가 함께 헐거워지므로, 컴파일러가 심은 문구가
    살아남아야 한다. 자리는 방향이 없으므로 (결과, 처치) 로 뒤집어 적어도 같은 자리다.
    """
    weak = [{"uid": "U_약", "between": [OUT, TREAT], "says": "경미한 정보 비대칭"}]
    client = _Client([_payload(latents=weak)])

    g = build(client, None, question=Q, hypotheses=[H], grounded={"e1"})

    assert [u.source for u in g.latents] == ["compiled"], g.latents
    assert "경미" not in g.latents[0].says and "사적 정보" in g.latents[0].says


def test_a_hand_built_graph_cannot_skip_the_compiler_and_drop_the_latent():
    """컴파일러를 우회해 `WorldGraph` 를 직접 만들면 `chosen` 이 U 없이 통과한다. 그
    그래프는 P4 에서 뒷문이 없는 것처럼 보이고 P8 의 상한이 `confirmed` 까지 열린다 -
    검사가 컴파일 경로에만 있으면 우회가 곧 승격이 된다.
    """
    bare = WorldGraph(nodes=NODES, edges=EDGES, latents=[], hypotheses=[H],
                      completeness="두 변수쌍을 훑었다")

    bad = validate(bare, grounded={"e1"})

    assert any("공통원인이 latents 에 없다" in b for b in bad), bad
    assert validate(replace(bare, latents=compile_latents([H], [])), grounded={"e1"}) == []


# --------------------------------------------------------------------------- #
# 시간 — 방향을 주장하려면 시차가 있거나 근거가 있어야 한다
# --------------------------------------------------------------------------- #
def test_an_all_simultaneous_chain_must_declare_why_it_can_point_an_arrow():
    """전부 `@t+0` 인 사슬은 시간 정보를 0비트 담고도 인과 방향을 주장한다 - 실측으로
    무사통과했다. 금지가 아니라 근거를 요구하는 이유는 하루 그레인에서 같은 날 안에
    전달되는 경로가 실제로 있기 때문이다. 선언을 지우면 다시 통과해야 실패한다.
    """
    now = {"BUYBACK@t+0": {"says": "취득 결정", "observed": None, "events": ["e1"]},
           OUT: NODES[OUT]}
    h = replace(H, treatment="BUYBACK@t+0")
    g = WorldGraph(nodes=now, edges=[{"from": "BUYBACK@t+0", "to": OUT, "kind": "statistical"}],
                   latents=compile_latents([h], []), hypotheses=[h],
                   completeness="두 변수쌍을 훑었다")

    assert any("simultaneous" in b for b in validate(g, grounded={"e1"}))

    declared = replace(g, edges=[{"from": "BUYBACK@t+0", "to": OUT, "kind": "statistical", "simultaneous": True,
                                  "simultaneous_why": "결정 공시와 체결이 같은 장중에 있다"}])
    assert validate(declared, grounded={"e1"}) == []


# --------------------------------------------------------------------------- #
# 되먹임 — 거부는 교정이고, 못 고치면 위반을 달고 나간다
# --------------------------------------------------------------------------- #
def test_an_empty_completeness_declaration_is_refused_and_the_model_is_asked_again():
    """완비 선언 없는 그래프는 인과 그래프가 아니다(Hernán-Robins). 선언이 비면 P4 의
    빈 조정집합이 "뒷문이 없다"인지 "아무도 안 그렸다"인지 구별되지 않는다 - 빈 문자열을
    받아 주면 그 구별이 파이프라인 전체에서 사라진다.
    """
    client = _Client([_payload(completeness="   "), _payload()])

    g = build(client, None, question=Q, hypotheses=[H], grounded={"e1"})

    assert len(client.users) == 2, "빈 완비 선언을 그대로 받았다"
    assert "completeness" in client.users[1], "거부 사유가 모델에게 안 돌아갔다"
    assert not g.violations and g.completeness.strip()


def test_a_structure_violation_comes_back_as_feedback_and_the_next_try_is_accepted():
    """거부는 침묵이 아니라 교정이다. 위반 문장을 안 돌려주면 모델은 같은 그래프를 다시
    내거나 구조를 통째로 갈아탄다 - 클라우드 실행에서 실제로 그랬고 회차가 소진됐다.
    """
    broken = _payload(edges=[{"from": OUT, "to": TREAT, "kind": "statistical"}])
    client = _Client([broken, _payload()])

    g = build(client, None, question=Q, hypotheses=[H], grounded={"e1"})

    assert "역행" in client.users[1], "위반이 되먹임으로 안 돌아갔다"
    assert len(client.users) == 2 and not g.violations


def test_three_failed_tries_hand_over_the_violations_instead_of_forcing_a_graph():
    """위반을 지우고 통과시킨 그래프가 내는 `adjust=[]` 보다, 위반을 단 그래프가 정직하다.
    위반이 붙어 있어야 P4 가 식별을 포기하고 P8 이 `undetermined` 로 처분한다 - 억지로
    밀면 그 셀은 근거 없이 확정된 것처럼 원장에 남는다.
    """
    late = {**NODES, "BUYBACK@t+1": NODES[TREAT]}
    client = _Client([_payload(nodes=late, edges=[{"from": "BUYBACK@t+1", "to": OUT, "kind": "statistical"}])])

    g = build(client, None, question=Q, hypotheses=[H], grounded={"e1"})

    assert len(client.users) == MAX_TRIES, "시도 상한을 안 지켰다"
    assert any("역행" in b for b in g.violations), g.violations
    assert g.hypotheses == [H], "처분할 가설이 사라지면 침묵이 된다"
