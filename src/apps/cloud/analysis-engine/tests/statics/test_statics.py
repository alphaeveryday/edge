"""정적 층의 계약 검증 — 각 검사는 설계 문서의 규율 하나를 지킨다.

깨지면 통계가 조용히 거짓말을 시작하는 지점들만 검사한다: 어휘 폐쇄(분기
자유도), 창 결정론(p-hacking), 합=1(항등식), 3값(부재≠기각), 예산 상한
(가법 제약), 동순위(순위 날조).
"""
from datetime import datetime

import numpy as np
import pytest

from edge_analysis.statics import (
    CHANNELS, GateInputs, HypothesisTuple, Row, Share, Trigger,
    ExposureSource, VocabError, Condition, build_windows,
    decompose, edge_gate, rank_with_ties, render, route)
from edge_analysis.statics.frame import validate_edge

O, C = datetime(2026, 7, 15, 9, 0), datetime(2026, 7, 15, 15, 30)


def _tuple(**kw):
    base = dict(
        conditions=(Condition("수급", "누적", ">=", 0.9),),
        trigger=Trigger("점", "COMPANY.EARNINGS.RESULT"),
        channel="Q수량",
        exposure=ExposureSource("속성", "재무파생"),  outcome="수익률")
    base.update(kw)
    return HypothesisTuple(**base)


def test_vocab_is_closed_new_words_die_at_creation():
    # 어휘 밖 값이 검정에 닿으면 분기 자유도가 생긴다 — 생성 시점에 죽어야 한다.
    with pytest.raises(VocabError):
        _tuple(channel="새채널")
    with pytest.raises(VocabError):
        Condition("붐빔", "누적", ">=", 0.9)
    with pytest.raises(VocabError):
        Trigger("계열", "없는계열")
    with pytest.raises(VocabError):
        ExposureSource("속성", "가격잔차", transform="제곱")
    assert _tuple().channel in CHANNELS


def test_frame_rejects_paths_outside_mechanism():
    # 프레임 밖 경로 주장은 기계적으로 불가능한 인과다.
    validate_edge("ETF_FLOW", "PREMIUM")
    with pytest.raises(ValueError):
        validate_edge("ETF_FLOW", "NAV")            # 수급이 NAV 를 직접 만들 수 없다


def test_windows_partition_and_determinism():
    taus = [(datetime(2026, 7, 15, 10, 0), "e1"), (datetime(2026, 7, 15, 10, 10), "e2")]
    ws = build_windows(O, C, taus)
    intra = [w for w in ws if w.kind != "gap"]
    # 전피복·서로소 — 깨지면 합=1 이 거짓이 된다.
    assert intra[0].start == O and intra[-1].end == C
    assert all(a.end == b.start for a, b in zip(intra, intra[1:]))
    # e1 창은 다음 τ 에서 잘린다 (결정론 겹침 규칙).
    assert next(w for w in ws if w.event_ids == ("e1",)).end == datetime(2026, 7, 15, 10, 10)


def test_tree_identity_sums_exactly():
    ws = build_windows(O, C, [(datetime(2026, 7, 15, 10, 0), "e1")])
    bars = [(datetime(2026, 7, 15, 9, 0), 101.0), (datetime(2026, 7, 15, 9, 55), 100.5),
            (datetime(2026, 7, 15, 10, 5), 99.0), (datetime(2026, 7, 15, 15, 25), 98.0)]
    shares = decompose(bars, 100.0, ws)
    assert abs(sum(s.log_ret for s in shares) - np.log(0.98)) < 1e-12
    # 갭과 첫 장중 창이 이중계상되지 않는다 (경계 규약).
    assert abs(shares[0].log_ret - np.log(1.01)) < 1e-12


def test_edge_gate_three_values_absence_is_not_rejection():
    assert edge_gate(10, 0.001) == "판정불가"       # N 부족 = 모른다
    assert edge_gate(100, 0.001) == "성립"
    assert edge_gate(100, 0.5) == "불성립"


def test_attribution_route_rejects_large_caps():
    ok = GateInputs(20, 0.4, True, False, 0.01, True)
    assert route(ok) == "점추정"
    # 요인 오염(D): 지수 비중 큰 종목은 자기 사건이 요인을 움직인다 → 거절.
    assert route(GateInputs(20, 0.4, True, False, 0.30, True)) == "거절"
    # 예고(C): τ 이전 드리프트 → 배제만.
    assert route(GateInputs(20, 0.4, True, True, 0.01, True)) == "배제만"


# 크기 상한 검사는 `test_additive_budget.py` 로 옮겼다. SEM(`EdgeEstimate` ·
# `clip_to_share` · `exposure_slope`)은 폐기됐다 - 기울기 τ̂ 를 하루 수준처럼
# 읽히게 하는 구조적 오독원이었고, 같은 반증(과대식별)은 ATT 의 가법 제약이
# **합산**으로 대신한다. 엣지별 교차보다 엄격하다.


def test_rank_overlapping_intervals_tie():
    rng = np.random.default_rng(1)
    r = dict(rank_with_ties({"a": rng.normal(1.0, 0.02, 400),
                             "b": rng.normal(0.99, 0.02, 400),
                             "c": rng.normal(0.1, 0.02, 400)}))
    assert r["a"] == r["b"] == 1 and r["c"] == 3    # 겹침 = 동순위, 아니면 날조


def test_render_table_self_audits():
    ws = build_windows(O, C, [(datetime(2026, 7, 15, 10, 0), "e1")])
    bars = [(datetime(2026, 7, 15, 9, 0), 101.0), (datetime(2026, 7, 15, 15, 25), 98.0)]
    shares = decompose(bars, 100.0, ws)
    rows = [Row(s) for s in shares]
    out = render(rows)
    assert "합계" in out and "미설명" in out        # 미설명이 1급 항목이다


# ── 서술 계약 — 정성 품질은 가드로 강제된다 ─────────────────────────────
def _rows_for_narration():
    from datetime import datetime
    from edge_analysis.statics import Share
    from edge_analysis.statics.windows import Window
    o = datetime(2026, 6, 1, 9, 0)
    gap = Share(Window("갭", o, o, "gap", ()), 0.02)
    ev = Share(Window("창@10:00", datetime(2026, 6, 1, 10, 0),
                      datetime(2026, 6, 1, 10, 15), "event", ("e1",)), -0.006)
    return gap, ev


def test_narration_refuses_ungrounded_citation():
    from edge_analysis.statics import NarrationError, Row, narrate
    _, ev = _rows_for_narration()
    with pytest.raises(NarrationError):
        narrate(ticker="t", name="n", day="d", route=None,
                rows=[Row(ev, verdict="성립", est=-0.005)], grounded={})


def test_narration_negatives_precede_shares_and_unknown_is_not_rejection():
    from edge_analysis.statics import Row, narrate
    gap, ev = _rows_for_narration()
    txt = narrate(ticker="t", name="n", day="d", route=None,
                  rows=[Row(gap), Row(ev, verdict="판정불가")], grounded={"e1": "국방AI"})
    assert txt.index("[아닌 것 먼저]") < txt.index("[몫]")
    assert "기각이 아니라 미지" in txt and "시간 알리바이" in txt


def test_narration_counterfactual_needs_positivity_and_significance():
    from edge_analysis.statics import Conditional, NarrationError, Row, narrate
    gap, ev = _rows_for_narration()
    rows = [Row(gap), Row(ev, verdict="판정불가")]
    ok = Conditional("포지셔닝", -1.8, -0.4, n_opposite=41, interaction_significant=True)
    txt = narrate(ticker="t", name="n", day="d", route=None, rows=rows,
                  grounded={"e1": "x"}, conditional=ok)
    assert "정상이었다면" in txt and "41건" in txt
    for bad in (Conditional("포지셔닝", -1.8, -0.4, 2, True),
                Conditional("포지셔닝", -1.8, -0.4, 41, False)):
        with pytest.raises(NarrationError):
            narrate(ticker="t", name="n", day="d", route=None, rows=rows,
                    grounded={"e1": "x"}, conditional=bad)


def test_narration_dedupes_unknown_labels():
    from edge_analysis.statics import Row, narrate
    from datetime import datetime
    from edge_analysis.statics import Share
    from edge_analysis.statics.windows import Window
    evs = tuple(f"e{i}" for i in range(30))
    w = Share(Window("갭", datetime(2026, 6, 1, 9, 0), datetime(2026, 6, 1, 9, 0),
                     "gap", evs), 0.01)
    txt = narrate(ticker="t", name="n", day="d", route=None,
                  rows=[Row(w, verdict="판정불가")],
                  grounded={e: "같은타입" for e in evs})
    assert "같은타입 ×30" in txt and txt.count("같은타입") == 1
