"""P5 판별 — **교란 정의를 닫는 자리. 기준은 선언이 아니라 실행 가능성이다.**

고정하는 불변식:
  · U 는 전부 처분된다 - 소거 검정이든 `cannot` 이든, 침묵은 거부된다
  · 두 세계가 같은 것을 예측하면 무용이고, 그 신고는 코드가 직접 센다
  · `executable` 은 **코드가 질의를 돌려서** 정한다. 모델의 자기 신고가 아니다
  · 조회 표면이 없으면 아무것도 소거되지 않는다 - 못 본 것은 못 가른 것이다

이 검사들이 없으면 "쉬운 U 두 개만 처리하고 done" 이 소거와 구별되지 않는다.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from edge_analysis.causal.contracts import (
    Axis,
    Fingerprint,
    Hypothesis,
    Identification,
    Latent,
    Question,
    WorldGraph,
)
from edge_analysis.causal.p5_discriminate import MAX_TURNS, NO_SQL, design
from edge_analysis.config import PipelineError

EVT, FLOW, AR = "EVT@t-1", "FLOW@t-1", "AR@t+0"
U_INFO, U_FLOW = "U_사적정보", "U_수급"
RUNS = "select count(*) from v_event where event_type_code = 'BUYBACK'"
FAILS = "select * from price_daily"

Q = Question(etf_instrument_id="091160", etf_name="테스트 ETF", trade_date=date(2026, 7, 16),
             as_of="2026-07-16T15:40:00+09:00", observed=0.0421, residual=0.0300,
             route_code="EVENT", explanandum="r⊥[091160, 2026-07-16] = +3.00%",
             intervention="자사주 취득 공시가 없던 세계", answer_form="구간")
FP = Fingerprint(axes=[Axis("사전표류", True, 0.001, says="공시 전 3일 누적 +0.10%"),
                       Axis("장중경로", False, missing_input="분봉 자료가 원장에 없다")])
LATENTS = [
    Latent(uid=U_INFO, between=(EVT, AR), says="기업이 이 사건을 고르게 만든 미관측 상태",
           source="compiled"),
    Latent(uid=U_FLOW, between=(FLOW, AR), says="같은 날 유입된 미관측 수급", source="declared"),
]
GRAPH = WorldGraph(
    nodes={n: {"says": n, "observed": f"{n} 일간 관측"} for n in (EVT, FLOW, AR)},
    edges=[{"from": EVT, "to": AR}, {"from": FLOW, "to": AR}],
    latents=LATENTS,
    # 역할·영역은 **신고값이다.** 둘 다 촉발원을 자처하는 경쟁 가설이라서 쌍 판별이
    # 필요한 것이고, 영역이 갈려야 P8 커버리지 원장에서 두 칸이 열린다.
    hypotheses=[Hypothesis(hid="H1", says="공시가 밀었다", treatment=EVT, outcome=AR,
                           assignment="chosen", role="trigger", domain="information"),
                Hypothesis(hid="H2", says="수급이 밀었다", treatment=FLOW, outcome=AR,
                           assignment="natural", role="trigger", domain="flow")],
    completeness="세 변수쌍을 훑었다")
IDENTS = [Identification(src=EVT, dst=AR, status="not_identified", blocked_by=[U_INFO])]


class _Client:
    """대본대로 답하는 판별 세션 스텁. 매 턴의 user 프롬프트를 모은다 - 남은 목록을 다시
    들려줬는지는 그 문자열로만 확인된다. 대본이 떨어지면 마지막 응답을 반복한다."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self._turns = list(turns)
        self.users: list[str] = []

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        self.users.append(user)
        return self._turns[min(len(self.users), len(self._turns)) - 1]


class _Sql:
    """가짜 조회 표면. **어떤 질의가 실제로 표면에 닿았는지 기록한다.**"""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def schema(self) -> str:
        return "v_event · v_daily · v_hold"

    def query(self, q: str, *, limit: int = 20) -> list[dict[str, Any]]:
        self.seen.append(q)
        if "price_daily" in q:
            raise PipelineError("기반 테이블 'price_daily' 직접 접근 금지")
        return [{"n": 3}]

    def ask(self, q: str) -> str:
        self.seen.append(q)
        return "n\n3"


def _disc(target: str, sql: str, predicts: dict[str, str], **kw: Any) -> dict[str, Any]:
    """판별 제출 하나. **기본값은 실제로 가르는 검정이다** - WOE 10 dB 는 JND(3 dB)의 세 배다.

    무용한 검정·구조적 배제·용량 검정은 `woe_db`·`kind` 를 덮어써서 만든다. 기본을
    0 dB 로 두면 모든 픽스처가 "무용" 으로 굳어 소거 경로가 한 번도 안 밟힌다.
    """
    return {"discriminator": {"kind": "latent", "target": target,
                              "observation": "공시 직전 내부자 매수 여부", "sql": sql,
                              "predicts": predicts, "woe_db": 10,
                              "woe_because": "두 세계에 각각 살아 보고 적은 우도비다", **kw}}


def _run(turns: list[dict[str, Any]], sql: _Sql | None = None):
    client = _Client(turns)
    plan = design(client, sql, question=Q, fingerprint=FP, graph=GRAPH, idents=IDENTS)
    return client, plan


def _uncleared_ids(plan) -> list[str]:
    return [u.uid for u in plan.uncleared(GRAPH.latents)]


# --------------------------------------------------------------------------- #
# 처분 폐쇄 — 침묵은 거부된다
# --------------------------------------------------------------------------- #
def test_a_done_with_a_silent_latent_is_refused_and_the_remaining_list_comes_back():
    """한 번 요구하고 마는 설계에서 모델은 쉬운 U 하나만 처리하고 done 을 낸다. 그 침묵이
    소거와 구별되지 않으면 P8 은 배제하지 못한 교란을 배제한 것으로 적는다.

    `cannot` 은 처분이지 소거가 아니다 - 미소거로 남아 다음 수집 의제가 된다.
    """
    client, plan = _run([
        _disc(U_INFO, RUNS, {"H1": "직전 매수가 는다", U_INFO: "직전 매수가 없다"}),
        {"done": True},                                   # U_수급 이 아직 침묵 -> 거부
        {"cannot": U_FLOW, "why": "투자자 유형별 수급 원장이 없다"},
        {"done": True},
    ], _Sql())

    assert len(client.users) == 4, "침묵한 채 낸 done 이 그대로 받아들여졌다"
    assert "거부" in client.users[2] and U_FLOW in client.users[2]
    assert {d.target for d in plan.discriminators} == {U_INFO, U_FLOW}
    assert _uncleared_ids(plan) == [U_FLOW]

    cannot = plan.for_latent(U_FLOW)
    assert not cannot.executable and "수급 원장" in cannot.why_not


def test_a_latent_that_stays_silent_to_the_turn_cap_is_still_written_down():
    """상한까지 못 낸 U 는 값으로 굳힌다. 행이 없으면 P9 대장에서 "안 물어본 것"과
    "물어봤는데 못 냈다"가 같은 모양(부재)이 되고, 다음 수집이 그 자리를 못 찾는다.
    """
    client, plan = _run([{"done": True}], _Sql())

    assert len(client.users) == MAX_TURNS
    assert {d.target for d in plan.discriminators} == {U_INFO, U_FLOW}
    assert all(not d.executable and "턴" in d.why_not for d in plan.discriminators)
    assert sorted(_uncleared_ids(plan)) == sorted([U_INFO, U_FLOW])


# --------------------------------------------------------------------------- #
# 소거의 조건 — 실행됐고, 갈랐을 때만
# --------------------------------------------------------------------------- #
def test_a_query_that_runs_but_predicts_the_same_thing_in_both_worlds_clears_nothing():
    """두 세계가 모두 "거래량이 는다"고 말하면 그 관측은 아무것도 가르지 못한다. 모델은
    스스로 무용하다고 적지 않으므로(`common` 신고를 안 한다) 코드가 예측을 직접 센다.

    ★ 제출은 10 dB 를 **주장한다**(`_disc` 기본값). 적어 낸 두 예측이 같은 문장이면 그
    주장은 근거가 없으므로 코드가 0 dB 로 되돌린다 - 신고된 무게를 그대로 받으면 무용한
    관측이 자기 신고만으로 U 를 소거한다.
    """
    client, plan = _run([
        _disc(U_INFO, RUNS, {"H1": "거래량이 는다", U_INFO: "거래량이 는다"}),
        {"cannot": U_FLOW, "why": "수급 원장이 없다"},
        {"done": True},
    ], _Sql())

    d = plan.for_latent(U_INFO)
    assert d.executable is True, "질의는 돌았다 - 무용 판정이 실행 실패에서 온 것이 아니다"
    assert d.woe_db == 0, "같은 예측 두 개인데 신고된 무게가 살아남았다"
    assert d.common_prediction is True
    assert U_INFO in _uncleared_ids(plan), "무용한 검정이 U 를 소거했다"


def test_executability_is_decided_by_running_the_query_not_by_the_model_saying_so():
    """자기 신고를 받으면 실행 못 하는 질의가 U 를 소거한다 - 이 모듈이 막으려는 실패다.
    거부 사유는 문장으로 남아야 다음 수집 의제가 된다.
    """
    sql = _Sql()
    client, plan = _run([
        _disc(U_INFO, FAILS, {"H1": "직전 매수가 는다", U_INFO: "직전 매수가 없다"}),
        _disc(U_FLOW, RUNS, {"H2": "장중 유입이 는다", U_FLOW: "장중 유입이 없다"}),
        {"done": True},
    ], sql)

    blocked, ran = plan.for_latent(U_INFO), plan.for_latent(U_FLOW)
    assert blocked.executable is False and "price_daily" in blocked.why_not
    assert ran.executable is True and not ran.why_not
    assert sql.seen == [FAILS, RUNS], "코드가 두 질의를 실제로 던지지 않았다"
    assert _uncleared_ids(plan) == [U_INFO]


def test_without_a_sql_surface_nothing_can_be_cleared():
    """조회 표면이 없으면 아무 질의도 돌지 않는다. 그 상태에서 소거를 인정하면 "못 봤다"가
    "봤는데 없더라"로 바뀌어 원장에 남는다 - 부재가 성공으로 읽히는 옛 구멍이다.
    """
    client, plan = _run([
        _disc(U_INFO, RUNS, {"H1": "직전 매수가 는다", U_INFO: "직전 매수가 없다"}),
        _disc(U_FLOW, RUNS, {"H2": "장중 유입이 는다", U_FLOW: "장중 유입이 없다"}),
        {"done": True},
    ], None)

    assert all(not d.executable and d.why_not == NO_SQL for d in plan.discriminators)
    assert sorted(_uncleared_ids(plan)) == sorted([U_INFO, U_FLOW])
    assert plan.queries == [], "표면에 안 닿은 질의가 조회 원장에 실렸다"


def test_a_discriminator_under_the_jnd_is_recorded_as_useless_even_though_it_ran():
    """3 dB 는 Good(1985) 의 JND 다 - 그보다 작은 우도비는 좋은 청력의 성인도 지각하지
    못하는 차이이고, 그런 관측은 **질의는 잘 돌면서** 아무것도 가르지 못한다. `executable`
    만 보는 소거 규칙이 정확히 이 자리에서 뚫린다.

    경계를 같이 잠근다: 2 dB 는 무용, 3 dB 는 소거다. 부등호가 뒤집히면 둘 중 하나가 깨진다.
    """
    client, plan = _run([
        _disc(U_INFO, RUNS, {"H1": "직전 매수가 는다", U_INFO: "직전 매수가 없다"},
              woe_db=2, woe_because="사전확률을 거의 못 움직인다"),
        _disc(U_FLOW, RUNS, {"H2": "장중 유입이 는다", U_FLOW: "장중 유입이 없다"},
              woe_db=3, woe_because="JND 를 겨우 넘는다 - 그래도 갈린다"),
        {"done": True},
    ], _Sql())

    weak, ok = plan.for_latent(U_INFO), plan.for_latent(U_FLOW)
    assert weak.executable is True and weak.common_prediction is True
    assert ok.executable is True and ok.common_prediction is False
    assert _uncleared_ids(plan) == [U_INFO], "JND 아래 검정이 U 를 소거했다"
    assert "무용" in client.users[1], "무용 판정이 모델에게 되먹여지지 않았다"


# --------------------------------------------------------------------------- #
# 통계 밖의 기각 — 제도와 용량은 질의로 반박하지 않는다
# --------------------------------------------------------------------------- #
def test_a_structural_exclusion_stands_without_touching_the_query_surface():
    """제도·규칙이 가설을 불가능하게 만든 것은 **자료로 반박할 대상이 아니다.** Flash Crash
    보고서의 가장 깨끗한 기각 셋이 통계가 아니었다 - fat finger 는 CME 가격밴드 ±12pt·
    최대주문 2,000계약으로 죽었다. `executable` 을 질의 성공으로만 정의하면 이 기각을
    표현할 자리가 아예 없어지고, 제도적 확실성이 "자료 없음" 과 같은 칸에 들어간다.

    표면은 멀쩡히 붙어 있다 - 그런데도 한 번도 안 닿는다는 것이 이 검사의 요점이다.
    """
    sql = _Sql()
    client, plan = _run([
        _disc("H2", "", {"H2": "장중 순매수가 관측된다", "H2 불가": "제도가 그 매수를 막는다"},
              kind="structural", observation="당일 공매도 과열종목 지정으로 차입공매도가 금지됐다",
              woe_db=20, woe_because="금지된 경로는 관측될 수 없다 - 우도가 0 에 붙는다"),
        {"cannot": U_INFO, "why": "내부자 거래 신고 원장이 없다"},
        {"cannot": U_FLOW, "why": "투자자 유형별 수급 원장이 없다"},
        {"done": True},
    ], sql)

    d = plan.by_kind("structural")[0]
    assert d.target == "H2" and d.executable is True, "제도적 배제가 질의 실패로 죽었다"
    assert not d.why_not and not d.sql
    assert sql.seen == [], "질의가 필요 없다는 기각이 표면을 두드렸다"
    assert plan.queries == []


def test_a_dose_discriminator_aimed_at_a_pair_is_refused_and_told_what_to_aim_at():
    """`dose` 는 **주 가설 자신의** 처치 강도가 결과와 단조인가를 본다 - 겨눌 수 있는 것은
    가설 하나다. 쌍을 받아 주면 "H1 보다 H2 가 낫다" 라는 상대 비교가 자기 처치의 단조성
    자리에 들어앉고, Menkveld-Yueshen 의 결정적 한 방(매도자가 붕괴 구간에서 공격강도를
    66% 줄였다)이 쌍 판별로 흐려진다.

    거부는 사유와 함께 되먹여야 다음 턴이 고쳐 낸다 - 조용히 버리면 같은 제출이 반복된다.
    """
    client, plan = _run([
        _disc("H1|H2", RUNS, {"H1": "H1 이 더 그럴듯하다", "H2": "H2 가 더 그럴듯하다"},
              kind="dose", observation="어느 쪽이 더 센가"),
        _disc("H1", RUNS, {"H1": "처치 상위구간에서 초과수익이 크다",
                           "H1 반증": "상위구간에서 오히려 작다"},
              kind="dose", observation="공시 강도 상위·하위 구간의 초과수익 차",
              woe_db=-12, woe_because="센 자리에서 결과가 더 작았다 - 가설이 자기 증거로 죽는다"),
        {"cannot": U_INFO, "why": "내부자 거래 신고 원장이 없다"},
        {"cannot": U_FLOW, "why": "투자자 유형별 수급 원장이 없다"},
        {"done": True},
    ], _Sql())

    assert "거부" in client.users[1] and "가설 하나를 겨눈다" in client.users[1]
    assert [d.target for d in plan.by_kind("dose")] == ["H1"], "쌍을 겨눈 dose 가 원장에 실렸다"
    assert [d.target for d in plan.dose_failures()] == ["H1"]
