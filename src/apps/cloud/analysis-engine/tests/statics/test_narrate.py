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
    assert "[채널] P판가" in s and "최대 +0.04%p" in s and "집합 밖 주장은 금지" in s
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


def test_unexplained_becomes_interval_when_edges_apply():
    # 12차: [몫]과 [채널]의 화해 - 적용 엣지의 식별집합을 빼면 미설명도 구간이 된다.
    # 점을 지어내지 않는 유일한 회계 형태다.
    rows = [_row("잔여1", 0.0129)]
    e = Edge(channel="P판가", event_type="T.X", verdict="성립", applied=True,
             iset_lo=0.0003, iset_hi=0.0128)
    s = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                edges=(e,))
    assert "미설명 [+0.01%p, +1.26%p]" in s and "점이 아니라 구간이 정직하다" in s
    # 모순 엣지(iset 없음)가 섞이면 뺄 수 없다 - 점 어법으로 후퇴.
    bad = Edge(channel="C", event_type="T.Y", verdict="성립", applied=True,
               contradiction=True)
    s2 = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                 edges=(e, bad))
    assert "미설명 [" not in s2 and "서사가 아니라 데이터다" in s2


def test_rejected_route_suppresses_all_magnitude_quotes():
    # 13차: 게이트 D 가 거절한 셀에서 [채널]이 크기를 인용하면 산문이 자기모순이다.
    # 존재 판정은 패널(타 종목) 소관이라 살아남고, 크기만 보류된다. [몫]도 구간
    # 회계를 접고 점 어법으로 후퇴한다.
    rows = [_row("잔여1", 0.0129)]
    e = Edge(channel="P판가", event_type="T.X", verdict="성립", applied=True,
             iset_lo=0.0003, iset_hi=0.0128)
    s = narrate(ticker="T", name="N", day="d", route="거절", rows=rows, grounded={},
                edges=(e,))
    assert "점귀속은 거절된다" in s                       # 귀속 형태 선언
    assert "크기는 **보류** — 셀 점귀속 거절" in s        # 채널판이 인용을 거부
    assert "기여는 많아야" not in s and "미설명 [" not in s  # 크기 인용 전무


def _gap_row(log_ret: float) -> Row:
    w = Window("갭", datetime(2026, 6, 1, 8), datetime(2026, 6, 1, 9), "gap", ())
    return Row(Share(w, log_ret))


def test_gap_covariate_is_partial_identified_never_a_point():
    # 16차 (§9): 갭 공변량도 부분식별 - β CI × 야간 지수 → 설명 구간, 잔여 구간.
    from edge_analysis.statics.narrate import GapCovariate
    rows = [_gap_row(-0.06), _row("잔여1", -0.02)]
    g = GapCovariate(factor_ret=-0.03, n=120, beta_lo=1.0, beta_hi=1.5,
                     explained=(-0.045, -0.03))
    s = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                gap_cov=g)
    assert "[갭]" in s and "β [1.00, 1.50]" in s and "공통충격" in s
    assert "[-4.50, -3.00]%p" in s and "[-3.00, -1.50]%p" in s   # 설명·잔여 둘 다 구간
    assert "구간이 정직하다" in s
    # 방향 모순 → 공통충격 설명 0 어법.
    c = GapCovariate(factor_ret=+0.02, n=120, beta_lo=1.0, beta_hi=1.5,
                     explained=None, contradiction=True)
    s2 = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                 gap_cov=c)
    assert "방향이 어긋난다" in s2 and "공통충격 설명 0" in s2


def test_gap_covariate_absence_needs_reason_and_names_backfill():
    from edge_analysis.statics.narrate import GapCovariate
    rows = [_gap_row(-0.06)]
    s = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                gap_cov=GapCovariate(reason="us_market 10일 - 백필 필요"))
    assert "공변량 미계측" in s and "백필" in s and "통째로 미설명" in s
    with pytest.raises(NarrationError, match="사유가 없다"):
        narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                gap_cov=GapCovariate())


def test_beta_ci_is_sane_on_synthetic_slope():
    from edge_analysis.statics.attribute import _beta_ci
    import numpy as np
    rng = np.random.default_rng(0)
    x = rng.normal(size=200)
    y = 1.2 * x + rng.normal(scale=0.1, size=200)
    lo, hi = _beta_ci(x, y)
    assert lo < 1.2 < hi and hi - lo < 0.1          # 참 기울기 포함 + 좁은 구간
    assert _beta_ci([1.0, 1.0, 1.0], [1, 2, 3]) is None   # 분산 없음 → 부재


def test_causal_budget_is_idiosyncratic_not_raw():
    # 20R: τ 는 ar_ind 단위인데 예산을 원수익으로 쓰면 단위가 다른 두 수를 교차한다.
    # 실측(005930 2026-07-30): 원수익 -0.72% · 시장 -1.10% · 고유 +0.38% — 부호 역전.
    # 시장이 끌고 간 날에 종목 사건으로 설명하려 드는 것을 이 문단이 막는다.
    from edge_analysis.statics.attribute import _clip, _iset
    from edge_analysis.statics.paneltest import EdgeReport

    r = EdgeReport("성립", 206, 0.010, None, None, None, ci_lo=0.0049, ci_hi=0.0142)
    assert _iset(r, -0.0072) is None            # 원수익 예산이면 방향 모순
    assert _iset(r, +0.00375) == (0.0049, 0.00375) or _iset(r, +0.00375) is None
    assert _clip(0.001, 0.002, 0.00375) == (0.001, 0.002)   # 고유 예산 안이면 그대로

    rows = [_row("잔여1", -0.0072)]
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                  idio=(0.00375, -0.01097))
    assert "[대상]" in txt and "고유" in txt
    assert "부호가 원수익과 반대" in txt          # 초과수익이었다는 사실을 숨기지 않는다


def test_narration_units_are_additive():
    """몫·기여는 로그 %p(가법)로 말한다 - 단순수익으로 부분을 말하면 합이 안 맞는다.

    실측(042700 07-31): 갭 +22.91 · 잔여 +4.13 = 27.04 인데 합계 칸은 +27.98.
    표의 assert 는 로그에서 검산하고 표시는 단순수익이었다.
    """
    from edge_analysis.statics.narrate import _pct, _pp
    assert _pp(0.2062) == "+20.62%p"          # 로그 그대로
    assert _pct(0.2467) == "+27.98%"          # 하루 총수익만 단순
    # 가법성: 부분의 표시값 합 == 총합의 표시값
    a, b = 0.2062, 0.0405
    assert abs(float(_pp(a)[:-2]) + float(_pp(b)[:-2]) - float(_pp(a + b)[:-2])) < 0.02


def test_channel_sentence_states_only_what_was_checked():
    """조건이 없고 환원이 미실행인 엣지에 '조건 충족 · 환원 일치'를 찍으면 부재를
    통과로 위장하는 것이다 (실측 042700 07-31 A1)."""
    from edge_analysis.statics.narrate import Edge, narrate
    from edge_analysis.statics.render import Row
    from edge_analysis.statics.tree import Share
    from edge_analysis.statics.windows import Window
    from datetime import datetime
    w = Window("잔여1", datetime(2026, 7, 31, 9), datetime(2026, 7, 31, 15, 35), "residual", ())
    rows = [Row(Share(w, 0.04))]
    e = Edge(channel="FX환", event_type="거시", verdict="성립", applied=True,
             iset_lo=0.0, iset_hi=0.0004)
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=rows,
                  grounded={}, edges=(e,))
    assert "조건 없음" in txt and "환원 미실행" in txt
    assert "조건 충족" not in txt, "검사하지 않은 것을 통과로 말했다"


def test_negative_budget_size_keeps_direction():
    """예산이 음수인 층에서 '많아야 +0.00%p' 는 방향을 잃은 어법이다."""
    from edge_analysis.statics.narrate import Edge, narrate
    from edge_analysis.statics.render import Row
    from edge_analysis.statics.tree import Share
    from edge_analysis.statics.windows import Window
    from datetime import datetime
    w = Window("잔여1", datetime(2026, 7, 31, 9), datetime(2026, 7, 31, 15, 35), "residual", ())
    e = Edge(channel="P판가", event_type="거시", verdict="성립", applied=True,
             iset_lo=-0.00416, iset_hi=0.0)
    txt = narrate(ticker="T", name="N", day="d", route=None,
                  rows=[Row(Share(w, -0.03))], grounded={}, edges=(e,))
    assert "최대 -0.42%p" in txt, txt
    assert "0 일 수도" in txt
