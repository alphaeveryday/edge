"""서술 계약의 채널판 — 튜플 판정이 산문에 도달하는 유일한 경로를 지킨다.

8차까지의 교훈: 블록에만 있는 판정은 없는 판정이다 — 사람이 읽는 건 산문이고,
산문에 안 실린 반증(성립-미적용)은 침묵으로 기각을 위장한다.
"""
from datetime import datetime

import pytest

from edge_analysis.statics import Row, Share
from edge_analysis.statics.narrate import Edge, NarrationError, narrate
from edge_analysis.statics.windows import Window


def _row(name: str, log_ret: float) -> Row:
    w = Window(datetime(2026, 6, 1, 9), datetime(2026, 6, 1, 11), "residual", ())
    return Row(Share(w, log_ret))


def test_edge_channel_sentences_follow_the_contract():
    # 채널판: 적용 엣지는 식별집합 어법으로 [채널] 문단, 성립-미적용은 사유와 함께
    # [아닌 것 먼저]로 - 반증이 긍정보다 앞선다 (NTSB 규율의 채널 확장).
    rows = [_row("잔여1", 0.02)]
    applied = Edge(channel="P판가", event_type="COMPANY.MANAGEMENT.EXECUTIVE_CHANGE",
                   verdict="성립", applied=True, iset_lo=0.0, iset_hi=0.0004)
    refuted = Edge(channel="K위험", event_type="EXOGENOUS.ACCIDENT.X",
                   verdict="성립", applied=False, why_not="횡단면 방향 반대 (환원 불일치)")
    s = narrate(ticker="T", name="N", day="2026-06-01", route=None, rows=rows,
                grounded={}, edges=(applied, refuted))
    assert "[채널] P판가" in s and "많아야 +0.04%p" in s and "상한 밖 주장은 금지" in s
    assert "K위험" in s.split("[몫]")[0]          # 반증은 [아닌 것 먼저] (몫 앞)
    assert s.index("[아닌 것 먼저]") < s.index("[채널]")


def test_edge_guards_kill_unqualified_sentences():
    rows = [_row("잔여1", 0.02)]
    # 게이트 없는 적용 주장 → 즉사
    with pytest.raises(NarrationError, match="게이트 없는 적용"):
        narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                edges=(Edge(channel="C", event_type="E", verdict="불성립", applied=True),))
    # 성립-미적용인데 사유 없음 → 기각 위장, 즉사
    with pytest.raises(NarrationError, match="사유가 없다"):
        narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                edges=(Edge(channel="C", event_type="E", verdict="성립", applied=False),))


def test_edge_contradiction_forbids_magnitude():
    # 과대식별 모순이면 구간 인용 금지 - '크기 보류' 어법만.
    rows = [_row("잔여1", 0.02)]
    s = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                edges=(Edge(channel="C", event_type="E", verdict="성립", applied=True,
                            contradiction=True),))
    assert "크기는 **보류**" in s and "식별집합 [" not in s
