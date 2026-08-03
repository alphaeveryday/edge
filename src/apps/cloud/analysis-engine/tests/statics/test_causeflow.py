"""인과 흐름 계약 - 간선엔 의도가 실리고, 검정은 결론을 지고, 대상은 유한하다."""
import pytest

from edge_analysis.statics.causeflow import LayerFact, pick_targets
from edge_analysis.statics.dag import TargetDAG
from edge_analysis.statics.fsm import GROUND, MENUS, SCREEN, Machine
from edge_analysis.statics.judge import JUDGE_MENUS, to_finding
from edge_analysis.statics.vocab import (ExposureSource, HypothesisTuple, Trigger,
                                         Condition)


def _tup(channel="Q수량", intent="거래량 급증이 매도 압력이면 고유 하락이 성립한다"):
    return HypothesisTuple(
        conditions=(Condition("수급", "누적", ">=", 0.9),),
        trigger=Trigger("계열", "거래량"), channel=channel,
        exposure=ExposureSource("속성", "거래량", "변화"),
        outcome="수익률", sign=-1, intent=intent)


# ── DAG: 간선 = 튜플 + 의도 ───────────────────────────────────────────────
def test_edge_without_intent_is_rejected():
    # 사용자 의도: 간선마다 "이 튜플로 검정하고 싶은 인과의 의도"를 적는다.
    # 의도 없는 간선은 검정 에이전트가 무엇이 사실이면 성립인지 모른다 - 반려.
    dag = TargetDAG("고유", "고유 -1.2%p", -0.012)
    rejected = dag.add([_tup(intent="  ")])
    assert len(rejected) == 1 and "의도" in rejected[0]
    assert not dag.edges


def test_competing_hypotheses_must_differ_by_channel():
    # 경쟁가설 = 같은 결과를 다른 채널로 설명하는 간선들. 같은 채널 둘은 변주다.
    dag = TargetDAG("고유", "고유 -1.2%p", -0.012)
    assert not dag.add([_tup("Q수량")])
    rejected = dag.add([_tup("Q수량")])
    assert len(rejected) == 1 and "중복" in rejected[0]
    assert len(dag.edges) == 1


def test_shared_trigger_is_surfaced_as_common_factor():
    # 두 간선이 같은 방아쇠를 딛으면 그 노드가 공통요인이다 - 조용히 두지 않고
    # 렌더에 드러나 검정 에이전트가 교란으로 고려한다.
    dag = TargetDAG("고유", "고유 -1.2%p", -0.012)
    dag.add([_tup("Q수량"), _tup("S주식수")])
    cf = dag.common_factors()
    assert any("계열:거래량" in c for c in cf)
    assert "공통요인" in dag.render()


def test_dag_render_marks_status_and_cut_reason():
    dag = TargetDAG("고유", "고유 -1.2%p", -0.012)
    dag.add([_tup("Q수량"), _tup("S주식수")])
    dag.edges[0].finding = to_finding({"causal": True, "conclusion": "성립 근거",
                                       "se": {"kind": "0/1", "name": "vol_fire",
                                              "value": "1", "meaning": "발화 여부"}})
    dag.edges[1].finding = to_finding({"causal": False, "cut_reason": "역사가 반대"})
    txt = dag.render()
    assert "✓" in txt and "✂" in txt and "역사가 반대" in txt and "SEM 재료" in txt
    assert len(dag.connected()) == 1 and len(dag.cut()) == 1


# ── 검정 판정 파싱 ────────────────────────────────────────────────────────
def test_to_finding_maps_three_verdicts():
    # 성립/기각/판정불가 삼값 - null 은 희소해야 하지만 형은 받아야 한다.
    assert to_finding({"causal": True}).status == "성립"
    assert to_finding({"causal": False, "cut_reason": "x"}).status == "기각"
    assert to_finding({"causal": None}).status == "판정불가"
    f = to_finding({"causal": True, "se": {"kind": "시계열", "name": "usdkrw_z",
                                           "value": "+2.9", "meaning": "환율 충격"}})
    assert (f.se_kind, f.se_name, f.se_value) == ("시계열", "usdkrw_z", "+2.9")
    assert f.se_meaning == "환율 충격"      # SEM 은 이 의미로 항을 세운다


# ── 대상 선택 ─────────────────────────────────────────────────────────────
def test_targets_are_bounded_and_ranked():
    # 대상 ≤3, |기여| 순. 5bp 미만 층은 설명할 것이 없다 - 대상이 아니다.
    fs = [LayerFact("시장", "m", -0.074), LayerFact("섹터", "s", -0.014),
          LayerFact("고유", "i", 0.009), LayerFact("섹터", "s2", 0.0001)]
    got = pick_targets(fs)
    assert [f.kind for f in got] == ["시장", "섹터", "고유"]
    assert all(abs(f.pct) >= 0.0005 for f in got) and len(got) <= 3


# ── 같은 상태기계 재사용 ──────────────────────────────────────────────────
class _Cat:
    def call(self, name, arg=""):
        return {"events": "사건 없음: 이 셀 장중 사건 0건 (조회 성공)",
                "news": "근거 문서 없음", "panel": "패널 수치: n=110 · p=0.124",
                "cell": "셀", "coverage": "커버리지"}.get(name, "ok")


def test_judge_uses_same_machine_with_panel_gate():
    # 검정 에이전트는 **같은** fsm.Machine 을 쓴다 - 메뉴와 SCREEN 판정 도구만 다르다.
    # panel 호출이 SCREEN 가드를 채워야 EMIT 까지 간다.
    m = Machine(catalog=_Cat(), menus=JUDGE_MENUS,
                screen_tools=("screen", "series", "panel"))
    assert m.state == GROUND
    m.observe("events")                   # 부재 확인 (부재도 증거다)
    m.observe("news")                     # 근거 열람 → SCREEN
    assert m.state == SCREEN
    assert any(n == "panel" for n, _ in m.allowed())
    m.observe("panel")                    # 패널 관측이 SCREEN 가드를 채운다
    assert m.done


def test_hypothesis_machine_unchanged_by_default():
    # 기본 인자면 기존 가설 기계 그대로다 - 파라미터화가 기존 계약을 안 바꾼다.
    m = Machine(catalog=_Cat())
    assert m.allowed() == MENUS[GROUND] + tuple(
        __import__("edge_analysis.statics.fsm", fromlist=["FREE"]).FREE)


def test_tuple_carries_intent():
    # 의도는 튜플의 1급 슬롯이다 - 간선에 실려 검정자에게 간다.
    assert "성립한다" in _tup().intent
