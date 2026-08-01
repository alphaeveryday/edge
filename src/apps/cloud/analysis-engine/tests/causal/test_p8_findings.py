"""P8 처분 — **검토한 전건에 판정을 남기고, 상한이 동사를 정한다.**

고정하는 불변식:
  · 미소거 U 가 하나라도 있으면 `confirmed` 가 아니고 본문에 확인 문구가 못 나간다
  · 같은 입력에서 U 만 소거되면 열린다 - 규칙이 U 에 반응한다는 대조
  · 산술 게이트로 죽은 후보도 판정으로 원장에 남는다 (로그에만 남기지 않는다)
  · 못 잰 지문 축은 `undetermined` 다 - 침묵이 아니라 산출물이다
  · 예산을 넘기면 살아남는 원인이 없다
  · 서술은 `Explanation` 계약을 만족한다 - 못 읽는 dict 는 산출이 아니다
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import json

import pytest

from edge_analysis.causal.contracts import (
    Axis,
    ConfoundingScreen,
    Discriminator,
    DiscriminationPlan,
    Fingerprint,
    Hypothesis,
    Identification,
    Latent,
    NegativeControl,
    Question,
    Relation,
    RelationKind,
    Role,
    Sensitivity,
    WorldGraph,
)
from edge_analysis.causal.engine import EdgeDesign
from edge_analysis.causal.p8_findings import CONFIRMED_PHRASE, audit_block, dispose, narrate
from edge_analysis.causal.verify import EdgeProof
from edge_analysis.config import PipelineError
from edge_analysis.domain.models import Explanation

EVT, FLOW, AR = "GUIDE@t-1", "FLOW@t-1", "AR@t+0"
UID = "U_가이던스"
LABEL = "실적 가이던스 상향"
FLOW_LABEL = "기관 순매수 유입"

Q = Question(etf_instrument_id="305720", etf_name="테스트 2차전지 ETF",
             trade_date=date(2026, 7, 16), as_of="2026-07-16T15:40:00+09:00",
             observed=0.0380, residual=0.0250, route_code="EVENT",
             explanandum="r⊥[305720, 2026-07-16] = +2.50%",
             intervention="가이던스 상향이 없던 세계", answer_form="구간",
             contributors=[("A소재", 0.009)], missing=["분봉 체결 원장"])
FP = Fingerprint(axes=[
    Axis("사전표류", True, 0.0008, says="공시 전 3거래일 누적 +0.08%"),
    Axis("장중경로", False, missing_input="분봉 자료가 원장에 없다")])
U = Latent(uid=UID, between=(EVT, AR), says="가이던스를 올리게 만든 미관측 실적 전망",
           source="compiled")
# 역할·영역은 신고값이다. 가이던스 상향은 **정보 영역의 촉발원** - 인과연쇄를 시작한
# 자리다. 기본값에 기대면 이 픽스처가 무엇을 주장하는지 파일에서 안 읽힌다.
H = Hypothesis(hid="H1", says="가이던스 상향이 당일 초과수익을 만들었다", treatment=EVT,
               outcome=AR, assignment="chosen", cause_label=LABEL,
               role="trigger", domain="information")
GRAPH = WorldGraph(nodes={n: {"says": n, "observed": f"{n} 일간 관측"} for n in (EVT, AR)},
                   edges=[{"from": EVT, "to": AR}], latents=[U], hypotheses=[H],
                   completeness="두 변수쌍을 훑었다")
PROOF = EdgeProof(design=EdgeDesign(src=EVT, dst=AR, because="기대 현금흐름을 올린다",
                                    cause_label=LABEL),
                  status="통과", n=140, effect=0.019, p=0.006)
BUDGET = {"residual": 0.025, "explained": [0.017, 0.023], "unexplained": 0.006,
          "over_budget": False, "reason": "", "n_paths": 1, "n_measured": 1, "n_blocked": 0}
KILLED = "비중 0.4% 로는 잔차 2.50% 를 만들 수 없다 - 필요 초과수익이 타입 최대를 넘는다"

# 두 판별 계획의 차이는 **`executable` 하나뿐이다.** 둘 다 10 dB 로 갈리는 관측이라,
# BLOCKED 가 미소거로 남는 이유는 "무용해서" 가 아니라 "못 돌려서" 다 - 그 사유가
# 그대로 다음 수집 의제가 되므로 두 사유를 섞으면 안 된다.
WOE_WHY = "내부자 매수는 사적정보 세계에서 흔하고 공시효과 세계에서는 드물다"
BLOCKED = DiscriminationPlan(discriminators=[
    Discriminator(kind="latent", target=UID, observation="공시 직전 내부자 매수",
                  predicts={"H1": "직전 매수가 없다", UID: "직전 매수가 는다"},
                  executable=False, why_not="내부자 거래 신고 원장이 없다",
                  woe_db=10, woe_because=WOE_WHY)])
CLEARED = DiscriminationPlan(discriminators=[
    Discriminator(kind="latent", target=UID, observation="공시 직전 내부자 매수",
                  predicts={"H1": "직전 매수가 없다", UID: "직전 매수가 는다"},
                  sql="select count(*) from v_event", executable=True,
                  woe_db=10, woe_because=WOE_WHY)])


def _kw(**over):
    base = dict(
        question=Q, fingerprint=FP, graph=GRAPH,
        idents=[Identification(src=EVT, dst=AR, status="identified", adjust=["MOM@t-3"])],
        proofs=[PROOF], budget=BUDGET,
        sensitivities=[Sensitivity(edge=f"{EVT}->{AR}", effect=0.019, e_value=2.3)],
        controls=[NegativeControl(kind="outcome", name="사건 전 5거래일", n=90, effect=0.0,
                                  p=0.58, passed=True)],
        screen=ConfoundingScreen(n_before=51, n_dropped=6),
        screened_candidates=[{"label": "지수 리밸런스", "killed": KILLED, "share": 0.004}])
    return {**base, **over}


# 두 번째 살아있는 가설. **역할만 갈아 끼우려고** 따로 세운다 - 촉발원이 둘이면 PC 가
# 둘이고, 증폭이면 PC 가 아니라 기여다.
#
# ★ 이쪽을 **일부러 더 센 경로로** 잡는다 (p 0.001 < 0.006, 효과도 크다). 강도 1등을
# PC 로 올리는 구계약에서는 이 가설이 언제나 1등이므로, 역할로 갈리는지 강도로 갈리는지가
# 여기서만 구분된다. 그리고 그것이 Kirilenko 의 자리 그대로다 - HFT 의 몫은 컸다.
H_FLOW = Hypothesis(hid="H2", says="같은 날 기관 순매수가 가격을 밀었다", treatment=FLOW,
                    outcome=AR, assignment="natural", cause_label=FLOW_LABEL,
                    role="trigger", domain="flow")
PROOF_FLOW = EdgeProof(design=EdgeDesign(src=FLOW, dst=AR, because="매수 압력이 호가를 올린다",
                                         cause_label=FLOW_LABEL),
                       status="통과", n=140, effect=0.021, p=0.001)


def _rival(role: Role, kind: RelationKind = "coincident") -> dict:
    """H1 옆에 두 번째로 검정을 통과한 가설을 세운다. 관계는 기본이 `coincident` 다 -
    미판정으로 두면 몫 배분을 못 믿어 상한이 강등되고, 검사가 역할이 아니라 관계를 보게 된다.
    """
    graph = replace(
        GRAPH,
        nodes={**GRAPH.nodes, FLOW: {"says": FLOW, "observed": "기관 순매수 일간"}},
        edges=[*GRAPH.edges, {"from": FLOW, "to": AR}],
        hypotheses=[H, replace(H_FLOW, role=role)],
        relations=[Relation(a="H1", b="H2", kind=kind,
                            because="둘 다 동시에·독립으로 같은 잔차를 만들 수 있다")])
    return dict(
        graph=graph, proofs=[PROOF, PROOF_FLOW],
        idents=[Identification(src=EVT, dst=AR, status="identified", adjust=["MOM@t-3"]),
                Identification(src=FLOW, dst=AR, status="identified", adjust=["MOM@t-3"])],
        budget={**BUDGET, "n_paths": 2, "n_measured": 2})


# --------------------------------------------------------------------------- #
# 주장 상한 — 미소거 U 하나가 동사를 바꾼다
# --------------------------------------------------------------------------- #
def test_one_uncleared_latent_forbids_the_confirmation_phrase():
    """상한을 문장 뒤 경고로 두면 고객은 첫 문장만 읽고 "확인"으로 받는다. 그래서 상한이
    **어느 동사를 쓸지**를 정한다 - 배제하지 못한 대안이 있으면 확정형이 아예 못 만들어진다.
    """
    f = dispose(plan=BLOCKED, **_kw())
    raw = narrate(f)

    assert [u.uid for u in f.uncleared_latents] == [UID]
    assert f.ceiling == "mechanism_compatible" and f.ceiling != "confirmed"
    assert CONFIRMED_PHRASE not in raw["explain"], raw["explain"]
    assert "양립합니다" in raw["explain"] and "배제하지 못했습니다" in raw["explain"]
    assert "내부자 거래 신고 원장" in raw["explain"], "무엇이 있으면 갈리는지가 빠졌다"


def test_clearing_the_same_latent_is_what_opens_the_confirmation():
    """대조가 없으면 위 검사는 "이 파이프라인은 절대 확정 안 한다"를 증명한 것이지 규칙이
    U 에 반응한다는 증명이 아니다. **판별 관측 하나만** 실행 가능해지면 열려야 한다.
    """
    f = dispose(plan=CLEARED, **_kw())
    raw = narrate(f, audit_block(plan=CLEARED, **_kw()))

    assert not f.uncleared_latents and f.ceiling == "confirmed", f.ceiling_why
    assert CONFIRMED_PHRASE in raw["explain"]
    assert any(d.candidate.startswith(f"[{UID}]") and d.verdict == "not_contributing"
               for d in f.all_dispositions), "소거된 U 도 판정으로 남아야 한다"


def test_a_design_fault_keeps_the_ceiling_down_even_after_the_latent_is_cleared():
    """사건창 오염 검사를 건너뛴 "확인" 은 Kothari-Warner 절차를 뺀 주장이다. 필요조건을
    더 거는 것이므로 U 소거만으로 상한이 열리면 안 된다.
    """
    f = dispose(plan=CLEARED, **_kw(screen=ConfoundingScreen(
        n_before=0, n_dropped=0, checked=False, note="처치 표본이 없다")))

    assert not f.uncleared_latents and f.ceiling == "mechanism_compatible", f.ceiling_why
    assert CONFIRMED_PHRASE not in narrate(f)["explain"]


def test_the_confirmation_phrase_is_blocked_by_an_exception_not_by_convention():
    """규칙을 문서에만 적어 두면 다음 사람이 문장을 고치면서 조용히 깬다 - 그때 나가는 것은
    고객이 원인으로 읽는 한 문장이다. 상한과 본문이 어긋나면 서술 자체가 실패해야 한다.
    """
    confirmed = dispose(plan=CLEARED, **_kw())
    tampered = replace(confirmed, ceiling="mechanism_compatible")

    with pytest.raises(PipelineError, match="확인 문구가 본문에 있다"):
        narrate(tampered)


# --------------------------------------------------------------------------- #
# 처분 폐쇄 — 검토한 것에 침묵이 없다
# --------------------------------------------------------------------------- #
def test_a_candidate_killed_by_the_arithmetic_gate_stays_in_the_ledger_as_a_verdict():
    """가장 싼 게이트가 가장 세다 - 그런데 그 결과가 로그에만 남으면 고객도 다음 조사도
    무엇이 이미 배제됐는지 모른다. 죽은 후보는 사유와 함께 판정으로 남아야 한다.
    """
    f = dispose(plan=BLOCKED, **_kw())

    dead = next(d for d in f.not_contributing if d.candidate == "지수 리밸런스")
    assert dead.verdict == "not_contributing" and dead.why == KILLED
    assert dead.evidence["gate"] == "arithmetic" and dead.share == 0.004
    assert "지수 리밸런스" in narrate(f)["explain"]


def test_an_unmeasurable_fingerprint_axis_is_carried_as_an_open_verdict():
    """못 쟀다는 사실이 산출물이다. 빼면 "안 봤다"와 "봤는데 없다"가 같은 모양이 되고,
    무엇을 수집하면 이 셀이 풀리는지가 원장에서 사라진다.
    """
    f = dispose(plan=BLOCKED, **_kw())

    axis = next(d for d in f.undetermined if d.candidate == "지문 장중경로")
    assert axis.verdict == "undetermined" and "분봉" in axis.evidence["missing_input"]
    assert any(d.candidate == "미설명분" for d in f.undetermined), "남은 몫도 판정이다"


def test_going_over_budget_leaves_no_surviving_cause():
    """합이 잔차를 넘었다는 것은 어느 하나가 틀렸다는 뜻이지 **어느 것이** 틀렸는지가
    아니다. 그 상태에서 개별 간선을 살리면 상쇄 요인이 기각된 뒤 남은 경로가 잔차를 혼자
    넘긴 채 게시된다.
    """
    over = {**BUDGET, "over_budget": True, "reason": "귀속 합 3.90% > 잔차 2.50%"}

    f = dispose(plan=CLEARED, **_kw(budget=over))

    assert f.over_budget is True and f.probable_cause == [] and not f.contributing
    assert f.ceiling == "undetermined" and "예산 초과" in f.ceiling_why
    assert all(d.verdict == "undetermined" for d in f.all_dispositions
               if d.evidence.get("hid") == "H1")
    assert "신뢰할 수 없습니다" in narrate(f)["explain"]


# --------------------------------------------------------------------------- #
# 서술 — 아카이브가 읽을 수 있어야 산출이다
# --------------------------------------------------------------------------- #
def test_the_narration_satisfies_the_explanation_contract():
    """`Explanation` 이 못 읽는 dict 는 DB 에 UNCERTAIN 으로 떨어지거나 아예 버려진다.
    감사 블록이 없어도 고객 문장은 나가야 한다 - 아카이브 배선 하나가 산출을 막으면 안 된다.
    """
    f = dispose(plan=BLOCKED, **_kw())

    thin, thick = narrate(f), narrate(f, audit_block(plan=BLOCKED, **_kw()))

    assert Explanation(thin).is_valid and Explanation(thin).explanation_type != "UNCERTAIN"
    assert thin["headline"].startswith(Q.etf_name) and thin["confidence"]
    assert thick["explain"] == thin["explain"], "감사 재료가 고객 문장을 바꿨다"
    assert thick["causal"]["proofs"] and "proofs" not in thin["causal"]


# --------------------------------------------------------------------------- #
# 역할 — 원인은 복수일 수 있고, 증폭은 원인이 아니다
# --------------------------------------------------------------------------- #
def test_two_surviving_triggers_both_stand_as_probable_cause():
    """NTSB Writing Guide: "The probable cause can be a series of events or **a listing
    of separate causal factors**." 실제 Asiana 214 의 PC 는 4개 병렬이다. 단수로 두면
    과잉결정(둘 다 혼자서도 잔차를 만들 수 있는 자리)을 표현할 칸이 없어 강도 1등만
    남고 2등이 원장에서 조용히 강등된다.

    그리고 그 병렬이 **고객 문장까지** 가야 산출물이다 - 원장에만 둘이면 읽는 사람에게는
    여전히 범인이 하나다.
    """
    f = dispose(plan=CLEARED, **_kw(**_rival("trigger")))

    # 순서는 강도(p)를 따르고 **자격은 역할을 따른다** - 두 축이 다르다는 것이 요점이다.
    assert [d.candidate for d in f.probable_cause] == [FLOW_LABEL, LABEL]
    assert all(d.role == "trigger" and d.modality == "was" for d in f.probable_cause)
    assert not f.contributing, "두 촉발원 중 하나가 기여로 강등됐다"

    raw = narrate(f)
    assert "같은 자격으로 원인에 오른 것" in raw["explain"]
    assert FLOW_LABEL in raw["headline"] and LABEL in raw["headline"]


def test_a_surviving_amplifier_is_contributing_and_never_the_probable_cause():
    """**Kirilenko 의 HFT 판정이 정확히 이것이다** - 고빈도 거래자는 Flash Crash 의 원인이
    아니었고, 이미 시작된 하락을 증폭했다. 그 판정은 기여도(share) 축에서는 표현되지
    않는다: HFT 의 몫은 컸다. 역할 축이 없으면 "큰 몫 = 원인" 으로 접히고, 그 순간
    개입 설계가 뒤집힌다 (증폭을 막는 것과 촉발을 막는 것은 다른 규제다).

    같은 가설·같은 검정·같은 몫에서 `role` 하나만 바꾼다. 게다가 이 경로가 원장에서 **가장
    센 경로**다 - 강도로 PC 를 고르면 반드시 여기가 뽑힌다.
    """
    f = dispose(plan=CLEARED, **_kw(**_rival("amplifier")))

    assert [d.candidate for d in f.probable_cause] == [LABEL], "증폭요인이 PC 에 올랐다"
    assert [d.candidate for d in f.contributing] == [FLOW_LABEL]
    assert [d.candidate for d in f.by_role("amplifier")] == [FLOW_LABEL]
    assert "결과를 키운 것: " + FLOW_LABEL in narrate(f)["explain"]


def test_a_hypothesis_killed_by_its_own_dose_curve_is_not_contributing_though_it_passed():
    """Menkveld-Yueshen 의 결정적 한 방 - 공식 서사의 처치 변수(매도자 공격강도)가 붕괴
    구간에서 오히려 66% 줄었다. 처치가 센 자리에서 결과가 더 작으면 그 가설은 **자기
    증거로** 죽는다. 쌍 판별로는 절대 나오지 않는 기각이다.

    검정은 통과했고 유의했다 - 그런데도 기여가 아니다. 통계 게이트만 보는 처분은 이
    가설을 원인으로 게시한다.
    """
    dose_dead = DiscriminationPlan(discriminators=[
        *CLEARED.discriminators,
        Discriminator(kind="dose", target="H1", observation="공시 강도 상·하위 구간의 초과수익",
                      predicts={"H1": "상위구간에서 더 크다", "H1 반증": "상위구간에서 더 작다"},
                      sql="select * from v_event", executable=True, woe_db=-14,
                      woe_because="센 자리에서 결과가 작았다 - 단조성이 반대로 섰다")])

    assert PROOF.significant is True, "이 검사의 전제는 검정을 통과했다는 것이다"

    f = dispose(plan=dose_dead, **_kw())

    dead = next(d for d in f.all_dispositions if d.evidence.get("hid") == "H1")
    assert dead.verdict == "not_contributing" and dead.modality == "not_a_factor"
    assert "-14 dB" in dead.why and "역방향" in dead.why
    assert f.probable_cause == [], "자기 처치가 반대로 선 가설이 원인으로 올랐다"
    assert LABEL in narrate(f)["explain"]


# --------------------------------------------------------------------------- #
# 검정력 — 못 잡는 크기에서 "확인" 은 반증 불가능한 주장이다
# --------------------------------------------------------------------------- #
def test_an_underpowered_cell_cannot_reach_the_confirmation():
    """잔차가 이 셀의 검출 하한 아래면 "유의하지 않다"가 정보가 아니고 어떤 서사도 반증
    불가능하다. 그 자리에서 나온 확정형은 검정이 아니라 잡음을 읽은 것이다 - **E-value
    보다 먼저 오는 축**이라 교란 민감도를 논하기 전에 상한이 먼저 내려가야 한다.

    U 는 소거됐고 설계 결함도 없다. 갈리는 것은 `mde80` 하나뿐이다.
    """
    weak = replace(Q, resid_sd=0.0168, mde80=0.047,
                   null_note="같은 셀의 과거 250거래일 특이수익 분포")

    assert weak.underpowered is True and Q.underpowered is False

    f = dispose(plan=CLEARED, **_kw(question=weak))

    assert f.ceiling == "mechanism_compatible", f.ceiling_why
    assert "검정력 미달" in f.ceiling_why and "검출 하한" in f.ceiling_why
    assert CONFIRMED_PHRASE not in narrate(f)["explain"]


def test_non_finite_numbers_are_folded_to_null_in_the_audit_block():
    """NaN 은 값이 아니라 '못 쟀다'이고, JSON 리터럴로 쓰면 **원장이 통째로 사라진다.**

    실측(union-20260801-01): 검정 원장의 `obs` 가 NaN 이라 `json.dumps` 가 `NaN` 을
    찍었고, Postgres `json` 캐스팅이 그것을 거부해 설명을 다 만들어 놓고 영속 단계에서
    런이 죽었다 - 런 아카이브까지 함께 날아갔다.
    """
    proof = replace(PROOF, effect=float("nan"), p=float("inf"),
                    ledger=[{"obs": float("nan"), "perms_at": 2}])

    block = audit_block(plan=CLEARED, **_kw(proofs=[proof]))
    dumped = json.dumps(block, ensure_ascii=False, allow_nan=False)   # 여기서 터지면 실패다

    assert "NaN" not in dumped and "Infinity" not in dumped
    assert block["proofs"][0].get("effect") is None
    assert block["proofs"][0]["ledger"][0]["obs"] is None
