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


# 과대식별 모순의 크기 인용 금지는 `test_additive_budget.py` 로 옮겼다 -
# 판정 주체가 엣지별 SEM 구간에서 셀 단위 가법 제약으로 바뀌었다.


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
    # 크기 없는 엣지(iset 부재)가 섞이면 뺄 수 없다 - 점 어법으로 후퇴. SEM 폐기
    # 후 이것이 기본값이다: 게이트 경로는 존재만 판정하고 크기를 만들지 않는다.
    bad = Edge(channel="C", event_type="T.Y", verdict="성립", applied=True)
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
    # 방향 어긋남 → 공통충격 설명 0 어법. (`contradiction` 에서 이름을 바꿨다 -
    # SEM 과대식별 모순과 같은 단어로 다른 것을 가리켰다.)
    c = GapCovariate(factor_ret=+0.02, n=120, beta_lo=1.0, beta_hi=1.5,
                     explained=None, opposed=True)
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
    # 20R: 크기 추정량은 ar_ind 단위인데 예산을 원수익으로 쓰면 단위가 다른 두 수를
    # 비교한다. 실측(005930 2026-07-30): 원수익 -0.72% · 시장 -1.10% · 고유 +0.38%
    # — 부호 역전. 시장이 끌고 간 날에 종목 사건으로 설명하려 드는 것을 이 문단이
    # 막는다. (예산과 크기의 교차 자체는 이제 가법 제약이 한다 -
    # test_additive_budget.py.)
    rows = [_row("잔여1", -0.0072)]
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                  layers=(("시장", -0.01097), ("고유", 0.00375)))
    assert "[층]" in txt and "고유" in txt
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


def test_layer_identity_is_a_paragraph_not_silence():
    """층 분해는 두 번째 항등식이다 - 하루의 98%가 시장인 날에 '전부 미설명'은 거짓말이다.

    실측(042700 07-31): 시장 +24.22 · 섹터 -3.47 · 고유 +3.92 = +24.67 (하루 총합).
    데이터는 알고 있었는데 산문에 축이 없어서 못 말했다.
    """
    rows = [_row("잔여1", 0.2467)]
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                  layers=(("시장", 0.2422), ("섹터", -0.0347), ("고유", 0.0392)))
    assert "[층]" in txt
    assert "시장 +24.22%p" in txt and "고유 +3.92%p" in txt
    assert "항등식이지 인과가 아니다" in txt
    # 지배 층이 고유가 아니면 '종목 이야기' 서사를 반박하는 문장이 따로 선다
    assert "시장층" in txt and "층 분해가 반박한다" in txt


def test_us_factor_is_sector_matched_not_broad():
    """반도체 종목의 갭을 S&P500 으로 재면 밤사이 반도체 +8.5% 를 +0.77% 로 설명하려 든다.

    facts 는 이미 반도체 지수를 출력하고 있었는데 gap_covariate 만 광의 지수를 봤다.
    팩터는 적합도가 아니라 **사람이 정한 업종 매핑**으로 고른다.
    """
    from edge_analysis.statics.attribute import US_FACTOR, US_FACTOR_DEFAULT, _us_factor

    class L:
        def __init__(self, code):
            self.code = code

        def sql(self, q):
            return [(self.code,)] if self.code else []

    assert _us_factor(L("1012"), "042700", "2026-07-31") == "SOX"   # 기계·장비
    assert _us_factor(L("1013"), "005930", "2026-07-31") == "SOX"   # 전기전자
    assert _us_factor(L("1002"), "000000", "2026-07-31") == US_FACTOR_DEFAULT
    assert _us_factor(L(None), "999999", "2026-07-31") == US_FACTOR_DEFAULT
    assert set(US_FACTOR.values()) <= {"SOX", "SOXX", "SMH", "GSPC", "IXIC"}


def test_unexplained_is_scoped_to_the_causal_budget():
    """층 회계가 가져간 몫은 '설명 실패' 가 아니다 - 인과 엣지가 청구할 수 없는 것이다.

    실측(042700 07-31): 하루 +24.67 중 층 회계 +20.75, 고유 +3.92. 산문이
    "미설명 +24.67" 이라 말해 바로 아래 층 문단과 정면으로 어긋났다.
    """
    rows = [_row("잔여1", 0.2467)]
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                  layers=(("시장", 0.2422), ("섹터", -0.0347), ("고유", 0.0392)))
    assert "미설명 +3.92%p" in txt, txt
    assert "미설명 +24.67%p" not in txt, "층 회계 몫을 설명 실패로 셌다"
    # 층이 없으면 예전대로 시간 항등식 기준
    plain = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={})
    assert "미설명 +24.67%p" in plain


def test_peer_cross_section_contradicting_layers_is_confessed():
    """β≈1 인 동종이 시장에서만 +21%를 받았다면 총수익이 +7.6%p 일 수 없다.

    실측(042700 07-31): 층 분해 시장 +24.22 · 섹터 -3.47 인데 동종 12종목 중위
    총수익은 +7.59%p. 시총가중 지수(반도체 30%)가 섹터 충격을 시장 팩터로 흡수하고,
    시장직교 섹터층은 남은 게 없어 음수로 나온다. 숨기면 시장 몫이 과대배정된다.
    """
    rows = [_row("잔여1", 0.2467)]
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                  layers=(("시장", 0.2422), ("섹터", -0.0347), ("고유", 0.0392)),
                  peers=("기계·장비(코스피)", 12, 0.0759, 1.0))
    assert "[동종]" in txt and "중위 +7.59%p" in txt
    assert "[모순]" in txt, "층 분해와 횡단면의 불일치를 침묵했다"
    assert "시장 몫은 상한으로 읽어라" in txt
    # 동종 중위가 층 분해와 정합하면 모순 문장은 안 나온다
    ok = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                 layers=(("시장", 0.2422), ("섹터", -0.0347), ("고유", 0.0392)),
                 peers=("전기전자(코스피)", 45, 0.2300, 0.6))
    assert "[모순]" not in ok


def test_render_folds_but_stays_additive():
    """사건 78건이면 창이 137개가 된다 - 137행 표는 설명이 아니라 로그다.

    실측(000660 07-29). 접어도 합계 검산은 성립해야 한다 (assert 가 지킨다).
    판정·기여가 붙은 행은 설명의 본체라 접지 않는다.
    """
    from edge_analysis.statics.render import render
    rows = [_row(f"창{i}", (i - 20) * 0.001) for i in range(40)]
    txt = render(rows, top=6)
    assert "…나머지" in txt and "접음" in txt
    body = [ln for ln in txt.splitlines() if ln and not ln.startswith(("창", "시각", "─", "합계", "단위", "…"))]
    assert len(txt.splitlines()) < 15, "접기가 안 먹었다"
    assert "-2.00" in txt and "+1.90" in txt           # |몫| 큰 창은 남는다
    # 판정이 붙은 행은 접히지 않는다
    from edge_analysis.statics.render import Row
    keep = Row(rows[0].share, verdict="성립", est=0.001)
    txt2 = render([keep, *rows[1:]], top=2)
    assert "성립" in txt2


def test_kalman_beta_tracks_regime_jump_and_guards_absence():
    """Q=0 이면 초기값에 갇힌다 - Q 규칙이 결과를 지배하니 전역 고정이어야 한다.

    그리고 β CI 가 안 좁혀지면 판정불가여야 한다 (Epps·비동시거래). 부재를 0 으로
    쓰면 일중 층 분해가 조용히 거짓이 된다.
    """
    import numpy as np

    from edge_analysis.statics.kbeta import CI_MAX, kalman

    rng = np.random.default_rng(0)
    n = 200
    x = rng.normal(scale=0.004, size=n)
    true = np.concatenate([np.full(n // 2, 1.0), np.full(n - n // 2, 2.0)])
    y = true * x + rng.normal(scale=0.0005, size=n)
    b, p = kalman(y, x, 1.0, 0.04, 1e-4, 0.0005 ** 2)
    assert abs(b[-1] - 2.0) < 0.35, "점프를 못 따라간다"
    b0, _ = kalman(y, x, 1.0, 0.04, 0.0, 0.0005 ** 2)
    assert abs(b0[-1] - 2.0) > abs(b[-1] - 2.0), "Q=0 이 초기값에 갇히지 않았다"
    assert (p > 0).all()
    assert CI_MAX > 0


def test_path_paragraph_names_the_market_segment():
    """붕괴 구간이 시장이면 산문이 '종목 이야기가 아니다' 를 말해야 한다.

    실측 000660 07-29: 10:17 서킷브레이커 구간 -12.0%p 중 시장 -12.5, 고유 +0.5.
    """
    rows = [_row("잔여1", -0.101)]
    txt = narrate(ticker="T", name="N", day="d", route=None, rows=rows, grounded={},
                  path_segs=(("09:05–10:15", -0.0494, -0.0299, 1.45),
                             ("10:20–13:10", -0.1247, 0.0048, 1.29),
                             ("13:15–14:55", 0.0512, 0.0174, 1.41)))
    assert "[경로]" in txt and "β 1.29" in txt
    assert "종목 이야기가 아니다" in txt
    assert "10:20–13:10" in txt


def test_paragraph_tags_are_unique_per_meaning():
    """같은 `[태그]` 가 다른 것을 가리키면 독자가 구분할 수 없다.

    실측: 칼만 경로 문단을 `[경로]` 로 붙였는데 ETF 괴리 판정이 이미 쓰고 있었다.
    ETF 셀에서 둘이 같이 나오면 '경로' 가 두 뜻이 된다.
    """
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[2] / "src/edge_analysis/statics/narrate.py"
    body = src.read_text(encoding="utf-8").split("def narrate(")[1]
    tags = re.findall(r'out\.append\(\s*f?"\[([^\]]+)\]', body)
    # 태그별로 어떤 조건 분기에서 나오는지는 다를 수 있지만, 의미는 하나여야 한다.
    assert "괴리" in tags, "ETF 괴리 판정 태그가 사라졌다"
    assert tags.count("경로") == 2, "경로 태그는 일중 β 문단 2개뿐이어야 한다"
    assert "층" in tags and "시장" in tags and "동종" in tags and "모순" in tags


def test_verifier_folds_broken_diagnostics_but_not_unmeasured_placebo():
    """사전추세·균형이 깨진 ATT 는 처치효과가 아니다 - 접는다.

    그러나 **위약 미계측은 실패가 아니다**. 접으면 재보도 대조군이 없는 타입 전부가
    영구 침묵한다 - 실측 CONTRACT.SIGNING 에서 짝 0 으로 함의 전량이 접혔고, 그 중
    PREFERRED_BIDDER p=0.000 이 있었다. 미계측은 넘기되 산문에 박는다.
    """
    from edge_analysis.statics.verifier import Implication, say_implications

    ok = Implication("실적이 고유를 +0.32%p", 0.0032, 0.001, 1221,
                     ("배수", "수준"), "통과", True, True)
    assert ok.credible and "위약 미계측" not in say_implications([ok])

    un = Implication("x", 0.01, 0.001, 100, None, "미계측", True, True)
    assert un.credible, "위약 미계측을 접으면 그 타입은 영구 침묵한다"
    assert "위약 미계측" in say_implications([un])

    for kw in ({"pretrend_ok": False}, {"balanced": False}):
        base = {"pretrend_ok": True, "balanced": True} | kw
        bad = Implication("x", 0.01, 0.001, 100, None, "통과", **base)
        assert not bad.credible
        assert "[접음]" in say_implications([bad])
    # 추정 자체가 없으면 자격 없음
    assert not Implication("x", None, None, 0, None, "통과", True, True).credible
    assert "없음" in say_implications([])


def test_plain_narration_forbids_numbers_and_jargon_by_code():
    """토스식은 **수치·전문용어 금지**를 코드가 강제한다 - 프롬프트 부탁이 아니다.

    사람이 이 화면을 여는 순간은 가격 급변과 시장 대비 이탈이다. 'p=0.004' 는 답이
    아니고, 프롬프트로 '숫자 쓰지 마라' 만 적으면 모델은 늘 흘린다(실측).
    그리고 이유를 모를 때 그럴듯한 문장을 내는 것은 거짓이므로 모른다고 말해야 한다.
    """
    import pytest

    from edge_analysis.statics.plain import (SIZE_TOP, PlainError, context, dual,
                                             guard, relation_word, size_word)

    ctx = context(ticker_name="KODEX 반도체", day_log=0.25, idio_log=0.006,
                  route_kind="시장", market_name="코스피 대형주",
                  recent={"when": "밤사이", "events": ["미국 반도체 강세"]},
                  established=["미국 반도체 강세"], overnight=["미국 반도체"],
                  unexplained_top=False)
    assert ctx["크기"] == SIZE_TOP and ctx["시장관계"] == "시장 따라"
    ok = ("밤사이 미국 반도체가 강해서 오늘 KODEX 반도체가 아주 크게 올랐어요. "
          "시장 전체가 함께 올라 이 상품만의 일은 아니에요.")
    assert guard(ok, ctx) == ok

    for bad in ("밤사이 미국 반도체가 8% 올랐어요.",            # 숫자
                "밤사이 유의하게 올랐어요.",                    # 전문용어
                "밤사이 '엔비디아' 때문이에요.",                # 접지 없는 이름
                "오늘 크게 올랐어요. 시장 따라 갔어요."):       # 최근 시점 누락
        with pytest.raises(PlainError):
            guard(bad, ctx)

    # 크기·시장관계는 코드가 매긴다 - 모델이 '급등' 을 고르면 강도가 날마다 흔들린다
    assert size_word(0.005) == "조금" and size_word(0.5) == SIZE_TOP
    assert relation_word(-0.05, 0.04) == "시장과 반대로"
    # 정직한 설명이 먼저다 - 순서를 뒤집으면 근거 없이 읽고 덮는다
    d = dual("통계 산문", "쉬운 산문")
    assert d.index("통계 산문") < d.index("쉬운 산문")


def test_claims_carry_basis_and_evidence_bundle_ids():
    """주장마다 {statistical|narrative, 묶음id} 가 붙어야 한다.

    쉬운 설명에는 수치가 없다 - 수치가 없으면 그 문장이 검정에서 나왔는지 기사
    서사에서 나왔는지 나중에 구분할 수 없고, 구분 못 하면 **서사를 검정 결과처럼
    읽는다**. 묶음 id 는 코드가 만든 내용 해시다(모델이 지어내면 접지가 무너진다).
    """
    import pytest

    from edge_analysis.statics.evidence import narrative_allowed, news_bundle
    from edge_analysis.statics.plain import PlainError, _assemble, context

    ctx = context(ticker_name="A", day_log=0.05, idio_log=0.04, route_kind="고유",
                  market_name="M", recent={"when": "오후"}, established=["x"],
                  overnight=[], unexplained_top=False)
    news = [{"ref": "n1", "news_id": "NEWS_A", "title": "수주", "type": "SIGNING",
             "thread": "t1", "t": "13:10"}]
    stt = [{"ref": "s1", "etype": "CONTRACT.SIGNING", "p": 0.004, "n": 138}]
    br = {o["ref"]: o for o in news} | {o["ref"]: o for o in stt}

    txt, bs = _assemble(
        [{"text": "오후에 크게 올랐어요", "basis": "statistical", "refs": ["s1"],
          "sign": 1},
         {"text": "새로 계약을 따냈다는 소식이 있었어요", "basis": "narrative",
          "refs": ["n1"], "sign": 1}], ctx, br, news, "000660.KS", "2026-07-31", "고유")
    assert "{statistical, ev_" in txt and "{narrative, ev_" in txt
    assert [b.basis for b in bs] == ["statistical", "narrative"]
    assert bs[1].news_ids == ("NEWS_A",), "서사 묶음은 뉴스 id 목록을 담는다"
    assert bs[0].stats["p"] == 0.004, "통계 묶음은 그 가설의 검정 수치를 담는다"
    # 같은 내용이면 같은 id - 재실행 비교가 가능해야 한다
    assert bs[1].bundle_id == news_bundle(
        "000660.KS", "2026-07-31", "새로 계약을 따냈다는 소식이 있었어요",
        news, ["n1"], layer="고유", sign=1).bundle_id

    # 한 주장에 통계와 서사를 섞으면 무엇이 근거인지 흐려진다
    with pytest.raises(PlainError):
        _assemble([{"text": "오후에 올랐어요", "basis": "narrative",
                    "refs": ["s1", "n1"], "sign": 1}], ctx, br, news, "c", "d", "")

    # 서사 경로는 통계가 전멸했을 때만 - 성립 엣지가 있으면 검정된 것을 말한다.
    # 지금은 경로 자체가 꺼져 있고, **끈 것을 사유로 말해야** 한다 - 조용히 빠지면
    # '뉴스가 없어서' 와 '경로를 껐어서' 를 구분할 수 없다.
    import edge_analysis.statics.evidence as ev
    assert not narrative_allowed(credible=0, applied_edges=1)[0]
    # 스위치를 끄면 사유가 '비활성' 이다 - '뉴스가 없어서' 와 구분된다
    ev.NARRATIVE_ENABLED = False
    try:
        off, why = narrative_allowed(credible=0, applied_edges=0)
        assert not off and "비활성" in why
    finally:
        ev.NARRATIVE_ENABLED = True
    assert narrative_allowed(credible=0, applied_edges=0)[0]


def test_plain_prose_cannot_overstate_weak_statistics():
    """**무유의 ≠ 영향 없음**, 결손 근거 ≠ 인과 단정. 코드가 등급을 읽는다.

    실측(091160 07-31): EXPORT_CONTROL 이 ATT -2.5%p · p=0.232 · 처치일 10 인데
    산문이 '큰 영향을 주지 않았어요' 라고 단정했다. 표본이 얇아 못 가른 것을
    '영향 없음' 으로 바꾸면 부재를 기각으로 위장한다(설계 §11).
    그리고 β 미계측 근거로 '~때문이에요' 라고 하면 결손을 감춘 것이다.
    """
    import pytest

    from edge_analysis.statics.plain import PlainError, _stat_guard

    insig = [{"p": 0.232, "att": -0.025}]
    with pytest.raises(PlainError, match="못 가른"):
        _stat_guard(1, "무역 정책은 영향을 주지 않았어요", insig)
    with pytest.raises(PlainError, match="못 가른"):
        _stat_guard(1, "관세 때문이 아니에요", insig)
    _stat_guard(1, "무역 정책 영향은 뚜렷하지 않아요", insig)   # 이건 정직하다

    weak = [{"p": None, "note": "β 표본 37 < 40 (SOX) - 백필 필요"}]
    with pytest.raises(PlainError, match="결손"):
        _stat_guard(2, "밤사이 미국 반도체 때문이에요", weak)
    _stat_guard(2, "밤사이 미국 반도체가 오른 영향으로 보여요", weak)

    sig = [{"p": 0.004, "att": 0.011}]
    _stat_guard(3, "이 소식이 영향을 받았어요", sig)             # 유의하면 단정 허용


def test_recent_window_never_returns_empty_when_windows_exist():
    """시점이 비면 **가드가 조용히 꺼진다** - 빈 문자열은 검사를 건너뛰기 때문이다.

    실측(000660 07-27): 창이 많아 어떤 창도 총합의 20%를 못 넘겨 전부 탈락하고
    `최근_시점`이 "" 이 됐다. 그러면 '최근 창을 반드시 말한다' 계약이 무력화되고
    산문은 하루 요약으로 도망갈 수 있다. 창이 있으면 반드시 하나를 고른다.
    """
    from edge_analysis.statics.plain import recent_window

    class W:
        def __init__(self, kind, start):
            self.kind, self.start = kind, start

    class S:
        def __init__(self, w, r):
            self.window, self.log_ret = w, r

    thin = [S(W("event", f"2026-07-27 1{i}:00:00"), 0.001) for i in range(10)]
    assert recent_window(thin).get("when"), "floor 미달이어도 시점은 있어야 한다"
    # 가장 큰 몫이 갭이면 '밤사이' 로 부른다
    thin.insert(0, S(W("gap", "2026-07-27 09:00:00"), 0.05))
    assert recent_window(thin)["when"] == "밤사이"
    assert recent_window([]) == {}, "창이 아예 없으면 빈손이 정직하다"


def test_first_claim_must_state_the_day_direction():
    """**반대 방향을 말하면 그 산문은 거짓이다.**

    실측(091160 · 2026-07-27): 하루가 **+3.03%p 상승**인데 산문이 '국내 반도체 ETF도
    따라 내렸어요' 라고 썼다. 밤사이 해외가 내린 것은 사실이라 하락 어휘 자체는
    정당하고 - 틀린 것은 **주체**다. 숫자·용어·접지 가드는 전부 통과했다: 방향을
    아무도 안 봤다. 첫 주장이 하루 방향을 말하게 강제하면 이 종류가 죽는다.
    """
    import pytest

    from edge_analysis.statics.plain import PlainError, _dir_guard, context

    up = context(ticker_name="KODEX 반도체", day_log=0.0303, idio_log=0.013,
                 route_kind="혼합", market_name="M", recent={"when": "밤사이"},
                 established=["x"], overnight=["y"], unexplained_top=False)
    assert up["방향"] == "올랐어요"
    with pytest.raises(PlainError, match="반대 낱말"):
        _dir_guard("밤사이 해외 반도체 ETF들이 내렸어요", up)
    _dir_guard("오늘 뚜렷하게 올랐어요", up)

    down = context(ticker_name="K", day_log=-0.05, idio_log=-0.04, route_kind="시장",
                   market_name="M", recent={"when": "오후"}, established=[],
                   overnight=[], unexplained_top=True)
    with pytest.raises(PlainError):
        _dir_guard("오후에 크게 올랐어요", down)
    _dir_guard("오후에 크게 빠졌어요", down)

    # 무변동은 방향 낱말을 요구하지 않는다 - 없는 방향을 말하라고 할 수 없다
    flat = context(ticker_name="K", day_log=0.0, idio_log=0.0, route_kind="시장",
                   market_name="M", recent={"when": "오후"}, established=[],
                   overnight=[], unexplained_top=True)
    _dir_guard("오후에 거의 움직이지 않았어요", flat)


def test_each_claim_carries_a_machine_readable_direction():
    """쉬운 설명의 주장마다 **트리거 시점 방향**을 `{basis, id, ±1}` 로 싣는다.

    산문은 낱말로 말하고 이 칸은 기계가 읽는다 - 같은 하루에 순풍(+1)과 역풍(-1)이
    섞이는 것이 정상이고, 낱말로만 두면 집계도 검산도 못 한다. 그리고 **첫 주장의
    방향은 하루 방향과 같아야** 한다(첫 문장이 '오늘 무엇이 어떻게 됐는지'이므로) -
    이것이 텍스트 가드(`_dir_guard`)의 기계 판본이다.
    """
    import pytest

    from edge_analysis.statics.evidence import Bundle
    from edge_analysis.statics.plain import PlainError, _assemble, context

    ctx = context(ticker_name="K", day_log=0.05, idio_log=0.04, route_kind="고유",
                  market_name="M", recent={"when": "오후"}, established=["x"],
                  overnight=[], unexplained_top=False)
    stt = [{"ref": "s1", "etype": "X", "p": 0.004, "n": 100}]
    br = {"s1": stt[0]}

    txt, bs = _assemble(
        [{"text": "오후에 크게 올랐어요", "basis": "statistical", "refs": ["s1"],
          "sign": 1},
         {"text": "일부 종목은 오히려 발목을 잡았어요", "basis": "statistical",
          "refs": ["s1"], "sign": -1}], ctx, br, [], "c", "d", "고유")
    assert ", +1}" in txt and ", -1}" in txt, "부호는 명시적으로 적는다"
    assert [b.sign for b in bs] == [1, -1], "역풍이 섞이는 것은 정상이다"
    # 부호가 묶음 id 에 들어간다 - 다른 부호는 다른 주장이다
    assert bs[0].bundle_id != Bundle(
        "statistical", "c", "d", "오후에 크게 올랐어요", "고유", (),
        {k: v for k, v in stt[0].items() if k != "ref"}, -1).bundle_id

    for bad, why in (({"sign": 2}, "범위 밖"), ({"sign": "up"}, "수가 아님"),
                     ({}, "누락")):
        with pytest.raises(PlainError):
            _assemble([{"text": "오후에 올랐어요", "basis": "statistical",
                        "refs": ["s1"], **bad}], ctx, br, [], "c", "d", "")

    # 첫 주장이 하루와 반대 방향이면 즉사
    with pytest.raises(PlainError, match="첫 주장의 방향"):
        _assemble([{"text": "오후에 크게 올랐어요", "basis": "statistical",
                    "refs": ["s1"], "sign": -1}], ctx, br, [], "c", "d", "")

    # 근거 없는 주장도 방향 칸은 남긴다 - '0 이다' 와 '못 매겼다' 가 달라야 한다
    blind = context(ticker_name="K", day_log=0.05, idio_log=0.04, route_kind="고유",
                    market_name="M", recent={"when": "오후"}, established=[],
                    overnight=[], unexplained_top=True)
    t2, b2 = _assemble([{"text": "오후에 올랐지만 뚜렷한 이유는 아직 안 보여요",
                         "basis": "none", "refs": [], "sign": 1}],
                       blind, {}, [], "c", "d", "")
    assert "{none, -, +1}" in t2 and not b2


def test_recent_window_reads_labels_as_an_event_id_map():
    """`labels` 는 {사건id: 사건타입} **맵**이다 - 위치로 맞추면 안 된다.

    실측(000660 06-01): `labels[i:i+1]` 로 잘라 `KeyError: slice(1,2,None)` 이 나며
    셀 전체가 죽었다. 위치 대응 자체가 틀렸다 - 창 i 의 사건이 맵의 i 번째일 이유가
    없다. 창은 자기 `event_ids` 로 이름을 찾아야 한다.
    """
    from edge_analysis.statics.plain import recent_window

    class W:
        def __init__(self, kind, start, ids):
            self.kind, self.start, self.event_ids = kind, start, ids

    class S:
        def __init__(self, w, r):
            self.window, self.log_ret = w, r

    ss = [S(W("gap", "2026-06-01 09:00:00", ()), 0.001),
          S(W("intraday", "2026-06-01 14:00:00", ("ev_b", "ev_c")), 0.02)]
    labels = {"ev_a": "A.TYPE", "ev_b": "B.TYPE", "ev_c": "C.TYPE"}

    got = recent_window(ss, labels)
    assert got["when"] == "오후" and got["kind"] == "intraday"
    assert got["events"] == ["B.TYPE", "C.TYPE"], "창의 id 로 찾는다 - 위치가 아니다"
    # 맵에 없는 id 는 조용히 사라지지 않는다 (없는 창과 못 찾은 창은 다르다)
    assert recent_window(ss, {})["events"] == ["ev_b", "ev_c"]
    # labels 를 안 줘도 죽지 않는다
    assert recent_window(ss)["events"] == ["ev_b", "ev_c"]
    # 사건 없는 창은 빈 목록
    assert recent_window([ss[0]], labels)["events"] == []


def test_moderation_reaches_the_plain_explanation_and_is_not_called_a_cause():
    """조절 조건이 **쉬운 설명 재료로 넘어간다** - 안 넘기면 두 산출물이 다른 말을 한다.

    이전에는 `_workflow` 가 산문 문자열만 남기고 `Implication` 객체를 버렸다. 그래서
    정직한 설명에는 `조절: 주주/수준 높을수록 +0.31%p` 가 찍히는데 쉬운 설명은 그
    존재를 몰라 평균 효과만 말했다. 같은 셀에서 두 설명이 다른 것을 말하면 하나는 거짓이다.

    그리고 **"원인" 이라 쓰지 못한다**: 라쏘가 뽑은 것은 예측에 유용한 조절자이고 인과
    조절자가 아니다. 부호는 조절계수와 교차검사된다(`_sign_guard`).
    """
    import pytest

    from edge_analysis.statics.plain import PlainError, _assemble, context

    ctx = context(ticker_name="K", day_log=0.03, idio_log=0.02, route_kind="고유",
                  market_name="M", recent={"when": "오후"}, established=["x"],
                  overnight=[], unexplained_top=False)
    mod = {"ref": "s1", "kind": "조절 조건", "사건": "RESULT_RELEASE",
           "조건": "주주/수준", "방향말": "높을수록 더 크게", "조절계수": 0.0031,
           "안정성": 0.86, "p": 0.003, "판정": "조절 성립 (원인 아님 - 조절이다)"}
    br = {"s1": mod}

    txt, bs = _assemble(
        [{"text": "오후에 올랐어요", "basis": "statistical", "refs": ["s1"], "sign": 1},
         {"text": "외국인 지분이 많은 종목에서 더 크게 나타났어요", "basis": "statistical",
          "refs": ["s1"], "sign": 1}], ctx, br, [], "c", "d", "고유")
    assert "외국인" in txt and "원인" not in txt
    assert all(b.stats.get("조절계수") == 0.0031 for b in bs)

    # 조절계수가 양수인데 방향을 -1 로 주장하면 즉사 - 근거가 반증한다.
    # (첫 주장은 하루 방향 가드가 먼저 잡으므로 둘째 주장에서 검사한다)
    with pytest.raises(PlainError, match="조절계수"):
        _assemble([{"text": "오후에 올랐어요", "basis": "statistical",
                    "refs": ["s1"], "sign": 1},
                   {"text": "외국인 지분이 많은 종목에서 덜 났어요",
                    "basis": "statistical", "refs": ["s1"], "sign": -1}],
                  ctx, br, [], "c", "d", "")

    # 프롬프트가 '원인 금지' 를 실제로 말한다 (선언 = 배선)
    from edge_analysis.statics.plain import _SYSTEM
    assert "조절 조건" in _SYSTEM and '"원인" 이라고 쓰지 마라' in _SYSTEM


def test_fabricated_quotes_die_and_real_ones_pass():
    """**인용문이 원문에 있는지** 검사한다 (C10) - 참조 존재만으로는 부족하다.

    실재하는 id 를 가리키면서 없는 문장을 지어낼 수 있다. STORM 의 base 가 정확히 그
    실패로 죽었다(`EVT_KR_20260601_001` 전량 허구 인용). 이 검사가 없어서 서사 경로를
    껐던 것이고, 검사가 들어왔으므로 켰다 - 스위치와 검사는 같은 커밋이다.

    막는 것은 **인용부호로 감싼 구간**뿐이다. 요약·의역은 막지 않는다(막을 방법이 없고,
    막으면 서사가 제목 복사로 퇴화한다). 인용했다고 표시한 것만 책임을 묻는다.
    """
    import pytest

    from edge_analysis.statics.plain import PlainError, _assemble, context

    ctx = context(ticker_name="K", day_log=0.05, idio_log=0.04, route_kind="고유",
                  market_name="M", recent={"when": "오후"}, established=["계약 체결"],
                  overnight=[], unexplained_top=False)
    news = [{"ref": "n1", "news_id": "NEWS_A", "type": "COMPANY.CONTRACT.SIGNING",
             "title": "SK하이닉스, 미국 고객사와 HBM 공급 계약 체결", "when": "13:20"}]
    br = {"n1": news[0]}

    def run(second: str):
        """첫 주장은 다른 가드(하루 방향·최근 시점)를 만족시키는 고정문이다."""
        return _assemble(
            [{"text": "오후에 크게 올랐어요", "basis": "narrative", "refs": ["n1"],
              "sign": 1},
             {"text": second, "basis": "narrative", "refs": ["n1"], "sign": 1}],
            ctx, br, news, "c", "d", "고유")

    # 원문에 있는 구간 인용 → 통과 (띄어쓰기 차이는 허용한다)
    txt, bs = run("\u300cHBM 공급 계약 체결\u300d 소식이 있었어요")
    assert "HBM" in txt and len(bs) == 2

    # 요약·의역은 인용부호가 없으면 막지 않는다
    run("미국 고객과 계약을 따냈다는 소식이 있었어요")

    # 지어낸 인용 → 즉사. 인용부호 종류를 바꿔도 잡힌다.
    # 두 가드가 겹쳐 덮는다: `_quote_guard`(인용한 기사 제목에 없다) 와 선행하는
    # 접지 가드(재료 어디에도 없는 이름). 둘 중 무엇이 먼저 걸리든 통과는 없다 -
    # 다만 **제목 대조로 죽는 경로가 실제로 있다**는 것을 따로 단언한다.
    for bad in ("\u300c삼성전자와 합병한다\u300d 고 밝혔어요",
                '"없는 문장이다" 라고 했어요',
                "'없는 문장이다' 라고 했어요",
                "\u201c없는 문장이다\u201d 라고 했어요"):
        with pytest.raises(PlainError, match="지어낸 인용|접지 없는 이름"):
            run(bad)
    # 재료에 있는 낱말들로 조립했지만 **그 제목에는 없는** 인용 - 제목 대조만이 잡는다
    with pytest.raises(PlainError, match="지어낸 인용"):
        run("\u300cHBM 계약 체결 미국\u300d 이라고 했어요")


def test_the_first_claim_may_state_the_day_without_evidence():
    """첫 주장은 **설명 대상**(하루 방향)을 말한다 - 근거를 요구하지 않는다.

    실측(000660 06-01): 통계가 전멸한 셀에서 모델이 방향과 '아직 이유는 안 보여요' 를
    두 문장으로 정확히 나눴는데 #1 에서 죽었다 - 첫 주장은 방향을 말해야 하고(방향 가드)
    동시에 모른다고 말해야 했다(접지 가드). **두 가드가 서로 모순했다.**

    하루 수익률은 이 셀이 존재하는 이유이고 가격 계열에 접지돼 있다. 이유 주장(i≥2)은
    여전히 근거나 '모른다' 를 요구한다 - 그게 완화되면 날조가 통과한다.
    """
    import pytest

    from edge_analysis.statics.plain import PlainError, _assemble, context

    blind = context(ticker_name="K", day_log=0.013, idio_log=0.012, route_kind="고유",
                    market_name="M", recent={"when": "장 열린 직후"}, established=[],
                    overnight=[], unexplained_top=True)

    txt, bs = _assemble(
        [{"text": "장 열린 직후부터 오늘 뚜렷하게 올랐어요", "basis": "none",
          "refs": [], "sign": 1},
         {"text": "아직 뚜렷한 이유는 안 보여요", "basis": "none", "refs": [],
          "sign": 0}], blind, {}, [], "c", "d", "")
    assert "올랐어요" in txt and not bs, "묶음은 없다 - 근거가 없으므로"
    assert "{none, -, +1}" in txt, "방향 칸은 남는다"

    # 이유 주장은 여전히 근거나 '모른다' 를 요구한다 - 완화되면 날조가 통과한다.
    # (두 가드가 겹쳐 덮는다: 주장별 접지 가드와 산문 전체의 '모른다' 가드)
    with pytest.raises(PlainError, match="근거 없는 주장|모른다고 말하지 않았다"):
        _assemble([{"text": "장 열린 직후부터 올랐어요", "basis": "none", "refs": [],
                    "sign": 1},
                   {"text": "외국인이 크게 사들였어요", "basis": "none", "refs": [],
                    "sign": 1}], blind, {}, [], "c", "d", "")

    # 첫 주장이 방향을 말하지 않으면 면제되지 않는다
    with pytest.raises(PlainError):
        _assemble([{"text": "장 열린 직후에 무슨 일이 있었어요", "basis": "none",
                    "refs": [], "sign": 1}], blind, {}, [], "c", "d", "")
