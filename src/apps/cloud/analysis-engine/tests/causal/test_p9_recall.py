"""P9 회상 — 쓰기만 하는 소환 기록은 track record 가 아니다.

레지스트리 헤더의 약속: "같은 기제를 다른 날 다시 소환했을 때 같은 부호·같은
크기가 나오는지가 유일한 검정." 종전에는 record 만 배선돼 그 검정이 시작될 수
없었다(에이전트 층 감사 4라운드). 계약: 회상은 record 이전 상태를 보고,
첫 소환은 이력 없음(빈 dict)이 정직한 답이며, 승격 임계(PROMOTE_AT)를 넘은
미해결은 회상이 수면 위로 올린다.
"""
from datetime import date
from pathlib import Path

from edge_analysis.causal.contracts import (Discriminator, DiscriminationPlan,
                                            Disposition, Findings, Hypothesis,
                                            Latent, Question, WorldGraph)
from edge_analysis.causal.p9_registry import recall, record

Q = Question(
    etf_instrument_id="091160", etf_name="반도체", trade_date=date(2026, 7, 16),
    as_of="2026-07-16T15:30:00", observed=0.031, residual=0.0421,
    route_code="R1", explanandum="r⊥ = +4.21%",
    intervention="공시가 없던 세계", answer_form="구간", missing=["분봉"])
H = Hypothesis(hid="H1", says="자사주 매입이 수급을 당겼다", treatment="BUYBACK",
               outcome="PX", assignment="chosen", nodes={"BUYBACK": {}, "PX": {}},
               denies=["공시 전 선행 상승"])
U = Latent(uid="U1", between=("BUYBACK", "PX"), says="사적 정보", source="compiled")
G = WorldGraph(nodes={"BUYBACK": {}, "PX": {}}, latents=[U], hypotheses=[H])
PLAN = DiscriminationPlan(discriminators=[
    Discriminator(kind="latent", target="U1", observation="피어 반응",
                  executable=False, why_not="분봉 없음")])
F = Findings(question=Q, uncleared_latents=[U], ceiling="undetermined",
             contributing=[Disposition(candidate="H1", verdict="contributing",
                                       why="수급", evidence={"effect": 0.02, "p": 0.03},
                                       ceiling="mechanism_compatible")])


F = Findings(question=Q, uncleared_latents=[U], ceiling="undetermined",
             contributing=[Disposition(candidate="H1", verdict="contributing",
                                       why="수급", evidence={"effect": 0.02, "p": 0.03},
                                       ceiling="mechanism_compatible")])
# 다른 날 = 다른 셀. 같은 셀 재실행은 latest() 가 한 행으로 접는다(결정론 재실행이
# track record 를 부풀리면 안 된다) - 검증 대상은 셀 축의 누적이다.
Q2 = Question(
    etf_instrument_id="091160", etf_name="반도체", trade_date=date(2026, 7, 17),
    as_of="2026-07-17T15:30:00", observed=0.02, residual=0.021,
    route_code="R1", explanandum="r⊥ = +2.10%",
    intervention="공시가 없던 세계", answer_form="구간", missing=["분봉"])
F2 = Findings(question=Q2, uncleared_latents=[U], ceiling="undetermined",
              contributing=[Disposition(candidate="H1", verdict="contributing",
                                        why="수급", evidence={"effect": 0.015, "p": 0.04},
                                        ceiling="mechanism_compatible")])


def test_first_summon_has_no_history(tmp_path: Path):
    assert recall(tmp_path / "reg", [H]) == {}      # 이력 날조 금지 - 빈손이 정직하다


def test_recall_sees_prior_summons_not_todays(tmp_path: Path):
    root = tmp_path / "reg"
    record(F, G, PLAN, root=root)
    record(F2, G, PLAN, root=root)

    hist = recall(root, [H])                        # 3번째 소환 직전
    m = next(iter(hist["mechanisms"].values()))
    assert m["n_invocations"] == 2                  # 오늘 것이 미리 들어가지 않는다
    assert m["verdicts"] == {"contributing": 2}     # 셀 축 누적 (7/16 · 7/17)
    assert m["sign_consistent"] is True             # 효과 부호 일관 - 재소환 검정의 최소형


def test_promote_due_surfaces_after_threshold(tmp_path: Path):
    root = tmp_path / "reg"
    for _ in range(3):                              # PROMOTE_AT = 3
        record(F, G, PLAN, root=root)
    hist = recall(root, [H])
    claims = {d["claim"] for d in hist["promote_due"]}
    # 3회 반복된 미해결(실행 불가 판별자·미소거 U·결측)이 수면 위로 - 침묵으로 안 사라진다.
    assert "latent:U1" in claims and any(c.startswith("discriminator:") for c in claims)
