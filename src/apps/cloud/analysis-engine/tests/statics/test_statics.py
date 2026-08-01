"""정적 층의 계약 검증 — 각 검사는 설계 문서의 규율 하나를 지킨다.

깨지면 통계가 조용히 거짓말을 시작하는 지점들만 검사한다: 어휘 폐쇄(분기
자유도), 창 결정론(p-hacking), 합=1(항등식), 3값(부재≠기각), 상한 교집합
(부분식별), 동순위(순위 날조), FE 소거(교란).
"""
from datetime import datetime

import numpy as np
import pytest

from edge_analysis.statics import (
    CHANNELS, EdgeEstimate, GateInputs, HypothesisTuple, Row, Share, Trigger,
    ExposureSource, VocabError, Vulnerability, build_windows, clip_to_share,
    decompose, edge_gate, exposure_slope, rank_with_ties, render, route)
from edge_analysis.statics.frame import validate_edge

O, C = datetime(2026, 7, 15, 9, 0), datetime(2026, 7, 15, 15, 30)


def _tuple(**kw):
    base = dict(
        vulnerabilities=(Vulnerability("수급", "누적", ">=", 0.9),),
        trigger=Trigger("점", "COMPANY.EARNINGS.RESULT"),
        channel="Q수량",
        exposure=ExposureSource("속성", "재무파생"),
        from_role="ISSUER", to_role="ISSUER", outcome="수익률", sign=-1)
    base.update(kw)
    return HypothesisTuple(**base)


def test_vocab_is_closed_new_words_die_at_creation():
    # 어휘 밖 값이 검정에 닿으면 분기 자유도가 생긴다 — 생성 시점에 죽어야 한다.
    with pytest.raises(VocabError):
        _tuple(channel="새채널")
    with pytest.raises(VocabError):
        Vulnerability("붐빔", "누적", ">=", 0.9)
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


def test_identification_set_is_capped_by_share():
    est = EdgeEstimate("FX환", tau=0.3, se=0.05, today_exposure=1.0)
    lo, hi = clip_to_share(est, share_logret=0.35)
    assert hi <= 0.35                               # 항등식 상한이 문다
    # 추정이 몫을 넘으면 식별집합이 빈다 — 과대식별 검산의 실패 신호.
    assert clip_to_share(EdgeEstimate("FX환", 0.9, 0.01, 1.0), 0.2) is None


def test_rank_overlapping_intervals_tie():
    rng = np.random.default_rng(1)
    r = dict(rank_with_ties({"a": rng.normal(1.0, 0.02, 400),
                             "b": rng.normal(0.99, 0.02, 400),
                             "c": rng.normal(0.1, 0.02, 400)}))
    assert r["a"] == r["b"] == 1 and r["c"] == 3    # 겹침 = 동순위, 아니면 날조


def test_fixed_effects_remove_event_confounder():
    rng = np.random.default_rng(2)
    ev = np.repeat(np.arange(30), 6)
    x = rng.normal(size=ev.size)
    u = 0.5 * x + rng.normal(size=30)[ev] * 5.0     # 사건 공통 교란이 지배적
    tau, se = exposure_slope(u, x, ev)
    assert abs(tau - 0.5) < 0.15                    # FE 가 소거한다


def test_render_table_self_audits():
    ws = build_windows(O, C, [(datetime(2026, 7, 15, 10, 0), "e1")])
    bars = [(datetime(2026, 7, 15, 9, 0), 101.0), (datetime(2026, 7, 15, 15, 25), 98.0)]
    shares = decompose(bars, 100.0, ws)
    rows = [Row(s) for s in shares]
    out = render(rows)
    assert "합계" in out and "미설명" in out        # 미설명이 1급 항목이다
