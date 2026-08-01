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
    w = Window(name, datetime(2026, 6, 1, 9), datetime(2026, 6, 1, 11), "residual", ())
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


def test_refuted_windows_leave_the_unknown_paragraph():
    # 10차 정정: 창의 접지 타입 전부가 패널 기각이면 창도 불성립 - [모른다]로
    # 뭉개면 [아닌 것 먼저]의 엣지 문장("서지 않는다")과 산문 안에서 모순된다.
    from edge_analysis.statics.attribute import _assign_rows
    from edge_analysis.statics.windows import Window
    from edge_analysis.statics import Share
    ev = Window("창@10:00", datetime(2026, 6, 1, 10), datetime(2026, 6, 1, 10, 15),
                "event", ("e1",))
    mixed = Window("창@11:00", datetime(2026, 6, 1, 11), datetime(2026, 6, 1, 11, 15),
                   "event", ("e2", "e3"))
    labels = {"e1": "T.REFUTED", "e2": "T.REFUTED", "e3": "T.UNTESTED"}
    rows = _assign_rows([Share(ev, 0.01), Share(mixed, 0.01)], labels,
                        passing={}, refuted={"T.REFUTED"})
    assert rows[0].verdict == "불성립"            # 전부 기각 → 창 수준 배제
    assert rows[1].verdict == "판정불가"          # 미검정 후보가 남으면 미지
    s = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded=labels)
    assert "T.REFUTED: 그 타입 엣지가 패널에서 서지 않는다" in s
    assert "지어내지 않는다" in s                 # [모른다]가 사유를 날조하지 않는다


def test_route_rejection_is_spoken_when_factor_weight_is_high():
    # 게이트 D 1단: 광역 ETF 비중 상한 초과 → 거절이 산문 둘째 문장에 선다.
    rows = [_row("잔여1", 0.02)]
    s = narrate(ticker="T", name="N", day="d", route="거절", rows=rows, grounded={})
    assert "점귀속은 거절된다" in s and "구조적 한계" in s


def test_repeated_refuted_windows_fold_into_one_sentence():
    # 서술은 목록이 아니라 요약이다 - 같은 타입의 반증 창 5개가 같은 문장 5번이면
    # [아닌 것 먼저]가 벽이 된다 (11차 라이브에서 실측된 결함).
    from edge_analysis.statics.attribute import _assign_rows
    from edge_analysis.statics.windows import Window
    from edge_analysis.statics import Share
    shares = [Share(Window(f"창{i}", datetime(2026, 6, 1, 10 + i), datetime(2026, 6, 1, 10 + i, 15),
                           "event", (f"e{i}",)), 0.01) for i in range(5)]
    labels = {f"e{i}": "T.SAME" for i in range(5)}
    rows = _assign_rows(shares, labels, passing={}, refuted={"T.SAME"})
    s = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded=labels)
    assert s.count("서지 않는다") == 1 and "T.SAME (창 ×5)" in s
