"""P4 식별 또는 경계 — **3값이다. 빈 조정집합은 성공이 아니다.**

고정하는 불변식:
  · P3 가 심은 U 는 P4 에서 점식별을 막는다 (두 단계가 이어져야 교란 폐쇄가 성립한다)
  · 완비 선언 없는 `adjust=[]` 는 `identified` 가 아니라 `identified_under` 다
  · `blocked_by` 는 **코드가 검증한다** - 모델의 자기 신고로 승격되지 않는다
  · 유계 가정이 없으면 경계는 무한이고, 그 사실이 문장으로 남는다
  · 같은 쌍을 두 가설이 주장해도 판정은 한 번이다 (회계가 두 번 세지 않는다)

옛 2값 `engine.identify` 를 검사하던 테스트는 `test_engine.py` 에서 삭제됐다 - 그 함수는
사라졌고 이 파일이 그 자리를 대신한다.
"""
from __future__ import annotations

import pytest

from edge_analysis.causal.contracts import Hypothesis, Latent, WorldGraph
from edge_analysis.causal.p3_graph import compile_latents
from edge_analysis.causal.p4_identify import identify, identify_all
from edge_analysis.config import PipelineError

EVT, AR, MOM, SCHED = "EVT@t-1", "AR@t+0", "MOM@t-3", "SCHED@t-5"
DECL = "처치·결과·모멘텀 세 변수쌍을 훑었고 그 밖의 공통원인은 없다"
CONFOUNDED = [(EVT, AR), (MOM, EVT), (MOM, AR)]


def _graph(names: list[str], edges: list[tuple[str, str]], *,
           latents: list[Latent] = (), pairs: tuple[tuple[str, str], ...] = ((EVT, AR),),
           completeness: str = DECL) -> WorldGraph:
    """관측 노드로만 이뤄진 그래프. 미관측을 섞고 싶으면 `nodes` 를 직접 손봐라."""
    return WorldGraph(
        nodes={n: {"says": n, "observed": f"{n} 일간 관측"} for n in names},
        edges=[{"from": a, "to": b} for a, b in edges],
        latents=list(latents), completeness=completeness,
        hypotheses=[Hypothesis(hid=f"H{i}", says="", treatment=t, outcome=o,
                               assignment="chosen") for i, (t, o) in enumerate(pairs, 1)])


CHOSEN = Hypothesis(hid="H1", says="자사주 취득 결정이 초과수익을 만들었다",
                    treatment=EVT, outcome=AR, assignment="chosen")


# --------------------------------------------------------------------------- #
# P3 → P4 — 심은 U 가 여기서 실제로 막아야 폐쇄가 닫힌다
# --------------------------------------------------------------------------- #
def test_a_latent_planted_by_p3_blocks_point_identification_and_is_named():
    """P3 가 U 를 심어도 P4 가 그것을 못 보면 폐쇄는 두 단계 사이에서 새어 나간다.

    범인을 uid 로 지목하는 것까지가 계약이다 - P5 는 그 uid 로 소거 검정을 설계하고 P8 은
    같은 uid 로 미소거를 적는다. 이름 없이 "막혔다"만 남기면 다음 두 단계가 대상을 잃는다.
    """
    g = _graph([EVT, AR], [(EVT, AR)], latents=compile_latents([CHOSEN], []))

    i = identify(g, EVT, AR)

    assert i.status == "not_identified" and not i.point_identified
    assert i.blocked_by == [f"U_{EVT}"], i.blocked_by
    assert not i.adjust, "막힌 간선에 조정집합을 적으면 P8 이 식별된 것으로 읽는다"


def test_the_same_structure_is_point_identified_once_the_confounder_is_measured():
    """대조 - U 가 아니라 **관측된** 교란이면 조정으로 끝난다. 이게 안 되면 위 검사는
    "P4 가 아무것도 식별 못 한다"를 증명한 것이지 U 에 반응한다는 증명이 아니다.
    """
    i = identify(_graph([EVT, AR, MOM], CONFOUNDED), EVT, AR)

    assert i.status == "identified" and i.adjust == [MOM] and i.point_identified


# --------------------------------------------------------------------------- #
# 빈 조정집합 — 무엇에 대한 진술인지가 선언에 달려 있다
# --------------------------------------------------------------------------- #
def test_an_empty_adjustment_set_is_demoted_without_a_completeness_declaration():
    """`adjust=[]` 는 "뒷문이 없다"가 아니라 "조정으로 막을 것이 없다"다. 완비 선언이
    있어야 그 빈 집합이 세계에 대한 진술이 되고, 없으면 아무도 교란을 안 그린 그래프와
    구별되지 않는다 - 그 상태를 성공으로 읽던 것이 옛 체제다.
    """
    edges = [(EVT, AR)]

    declared = identify(_graph([EVT, AR], edges), EVT, AR)
    silent = identify(_graph([EVT, AR], edges, completeness="  "), EVT, AR)

    assert declared.status == "identified" and declared.adjust == []
    assert silent.status == "identified_under" and silent.adjust == []
    assert any("완비" in a for a in silent.assumptions), silent.assumptions


# --------------------------------------------------------------------------- #
# 승격 — 모델의 자기 신고를 믿지 않는다
# --------------------------------------------------------------------------- #
def test_a_blocked_by_claim_is_promoted_only_when_the_code_confirms_the_separation():
    """`blocked_by` 는 "이걸로 조건화하면 막힌다"는 **제안**이다. 제안을 그대로 받으면
    `identified_under` 가 `identified` 의 완곡어법이 되고 3값이 다시 2값으로 무너진다.
    """
    real = Latent(uid=f"U_{EVT}", between=(EVT, AR), says="사적 정보", source="compiled",
                  blocked_by=[MOM])

    i = identify(_graph([EVT, AR, MOM], CONFOUNDED, latents=[real]), EVT, AR)

    assert i.status == "identified_under" and i.adjust == [MOM]
    assert f"U_{EVT} ⊥ {EVT}" in i.assumptions[0], i.assumptions
    assert i.blocked_by == [f"U_{EVT}"], "승격돼도 무엇을 가정했는지는 남아야 한다"


def test_a_blocked_by_claim_that_does_no_work_is_not_promoted():
    """두 가지 헛소리를 막는다: 그래프에 없는 이름(검증 불가), 그리고 이 쌍의 뒷문과
    무관한 이름(아무 노드나 적으면 승격되는 구멍). 둘 다 통과시키면 지우지도 않은 교란을
    지웠다고 적게 되고, P8 의 상한이 그 문장을 근거로 올라간다.
    """
    ghost = Latent(uid=f"U_{EVT}", between=(EVT, AR), says="", source="compiled",
                   blocked_by=["없는노드@t-9"])
    idle = Latent(uid=f"U_{EVT}", between=(EVT, AR), says="", source="compiled",
                  blocked_by=[MOM])

    i_ghost = identify(_graph([EVT, AR, MOM], CONFOUNDED, latents=[ghost]), EVT, AR)
    # MOM 이 AR 에 안 닿으면 MOM 으로 조건화해도 이 쌍의 뒷문은 그대로다.
    i_idle = identify(_graph([EVT, AR, MOM], [(EVT, AR), (MOM, EVT)], latents=[idle]), EVT, AR)

    assert i_ghost.status == "not_identified" and not i_ghost.adjust
    assert not i_ghost.assumptions, i_ghost.assumptions
    assert not i_idle.adjust and not any("⊥" in a for a in i_idle.assumptions), i_idle


def test_an_instrument_is_only_offered_after_adjustment_and_promotion_both_fail():
    """순서가 곧 주장의 강도 순서다. 도구변수를 먼저 찾으면 조정으로 끝날 자리에 배제제약을
    얹게 된다 - 공짜로 약해진다. 그래서 IV 는 마지막이고, 올 때는 가정을 문장으로 들고 온다.
    """
    u = Latent(uid=f"U_{EVT}", between=(EVT, AR), says="사적 정보", source="compiled")

    i = identify(_graph([EVT, AR, SCHED], [(SCHED, EVT), (EVT, AR)], latents=[u]), EVT, AR)

    assert i.status == "identified_under" and i.iv == [SCHED] and not i.adjust
    assert "배제제약" in i.assumptions[0], i.assumptions


# --------------------------------------------------------------------------- #
# 경계 — 점식별 실패가 종료가 아니다
# --------------------------------------------------------------------------- #
def test_without_a_support_assumption_the_bounds_are_infinite_and_say_so():
    """유계 가정 없이 좁은 수를 내면 그건 계산이 아니라 날조다. 그렇다고 침묵하면 P6·P8 이
    "경계를 안 쟀다"와 "경계가 무한이다"를 구별할 수 없으므로, 무엇이 있으면 좁혀지는지를
    문장으로 남긴다.
    """
    g = _graph([EVT, AR], [(EVT, AR)], latents=compile_latents([CHOSEN], []))

    free = identify(g, EVT, AR)
    bounded = identify(g, EVT, AR, support=(-0.08, 0.08))

    assert free.bounds is None and "무한" in free.bounds_note
    assert bounded.bounds == (-0.16, 0.16) and "지지집합" in bounded.bounds_note


def test_a_support_that_is_not_an_interval_is_refused_rather_than_silently_flipped():
    """뒤집힌 구간을 정렬해서 받아 주면 호출자의 실수가 경계 폭으로 흘러든다."""
    g = _graph([EVT, AR], [(EVT, AR)], latents=compile_latents([CHOSEN], []))

    with pytest.raises(PipelineError, match="지지집합"):
        identify(g, EVT, AR, support=(0.1, -0.1))


def test_a_pair_outside_the_graph_is_not_identified_by_the_emptiness_of_the_graph():
    """빈 그래프에서 d-분리는 자동 성립한다 - 없는 노드가 `identified` 로 나오는 경로다.
    구조 없이 나오는 빈 조정집합은 식별이 아니라 빈 그래프의 성질이다.
    """
    i = identify(_graph([EVT, AR], [(EVT, AR)]), "없음@t-2", AR)

    assert i.status == "not_identified" and "노드가 아니다" in i.bounds_note


# --------------------------------------------------------------------------- #
# 전수 — 같은 쌍을 두 번 세면 회계가 어긋난다
# --------------------------------------------------------------------------- #
def test_a_pair_claimed_by_two_hypotheses_is_judged_once():
    """식별은 구조만 보므로 같은 쌍의 답은 같다. 두 번 적으면 P8 이 같은 간선을 두 후보로
    세고, 귀속의 합이 예산을 넘는 것처럼 보인다 - 회계 폐쇄가 구조 중복으로 깨진다.
    """
    g = _graph([EVT, AR, MOM], CONFOUNDED, pairs=((EVT, AR), (EVT, AR), (MOM, AR)))

    got = identify_all(g, support=(-0.05, 0.05))

    assert [(i.src, i.dst) for i in got] == [(EVT, AR), (MOM, AR)], got
