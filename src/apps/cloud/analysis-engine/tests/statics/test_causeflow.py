"""인과 흐름 계약 - 간선엔 의도가 실리고, 검정은 결론을 지고, 대상은 유한하다."""
import pytest

from edge_analysis.statics.core.causeflow import LayerFact, pick_targets
from edge_analysis.statics.core.fsm import GROUND, MENUS, Machine
from edge_analysis.statics.core.vocab import (ExposureSource, HypothesisTuple, Trigger,
                                         Condition)


def _tup(channel="Q수량", intent="거래량 급증이 매도 압력이면 고유 하락이 성립한다"):
    return HypothesisTuple(
        conditions=(Condition("수급", "누적", ">=", 0.9),),
        trigger=Trigger("계열", "거래량"), channel=channel,
        exposure=ExposureSource("속성", "거래량", "변화"),
        outcome="수익률", intent=intent)


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


def test_hypothesis_machine_unchanged_by_default():
    # 기본 인자면 기존 가설 기계 그대로다 - 파라미터화가 기존 계약을 안 바꾼다.
    m = Machine(catalog=_Cat())
    assert m.allowed() == MENUS[GROUND] + tuple(
        __import__("edge_analysis.statics.core.fsm", fromlist=["FREE"]).FREE)


def test_tuple_carries_intent():
    # 의도는 튜플의 1급 슬롯이다 - 간선에 실려 검정자에게 간다.
    assert "성립한다" in _tup().intent
