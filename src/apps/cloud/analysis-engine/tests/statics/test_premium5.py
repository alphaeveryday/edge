"""5분 괴리 분해 — 항등식과 '부재는 넘어간다' 계약.

이 층이 지켜야 하는 것 둘:
  1. **두 몫의 합이 하루다.** 바스켓 몫과 괴리변화 몫을 더해 하루가 안 나오면 그 분해는
     설명이 아니다(로그 항등식이므로 정확히 맞아야 한다).
  2. **재료가 없으면 사유 한 줄로 넘어간다.** 괴리는 선제적 부가정보다 - 부재로 예외가
     나면 셀 설명 전체가 멈춘다(실측: NAV 33종목 중 5분봉 겹치는 것 1종목).
"""

from __future__ import annotations

import math

from edge_analysis.statics.premium5 import MIN_COVER, Split, Win, premium_5m


class _Lake:
    """레이크 대역. `rows` 를 주면 본문 질의 결과로 쓴다."""

    def __init__(self, *, rdb=True, bars=1, anchor=None, rows=(), boom=None):
        self.exists = {"rdb": rdb, "bars_5m": bars}
        self._anchor, self._rows, self._boom = anchor, list(rows), boom

    def sql(self, q: str):
        if self._boom:
            raise RuntimeError(self._boom)
        return [self._anchor] if "etf_nav_daily" in q else self._rows



def test_two_shares_sum_to_the_day():
    """하루 = 바스켓 몫 + 괴리변화 몫. 이것이 안 맞으면 분해가 아니다."""
    nav0, p0 = 100.0, 101.0                       # 시작 괴리 +1%
    rows = [("t1", 103.0, 0.010, 1.0), ("t2", 106.0, 0.040, 1.0)]
    lake = _Lake(anchor=("i1", nav0, p0), rows=rows)

    sp, note = premium_5m(lake, "091160", "2026-07-27")
    assert sp is not None, note
    assert abs(sp.total - sp.basket - sp.premium_move) < 1e-12
    # 창의 합도 하루다 - 창을 빠뜨리면 여기서 갈린다
    assert abs(sum(w.r_etf for w in sp.wins) - sp.total) < 1e-12
    for w in sp.wins:
        assert abs(w.r_etf - w.r_bk - w.d_prem) < 1e-12, w
    # 괴리 수준은 P/iNAV - 1 이다 (밤사이가 바스켓 누적에 들어 있다)
    assert abs(sp.wins[0].premium - (103.0 / (nav0 * math.exp(0.010)) - 1)) < 1e-12
    assert abs(sp.prem_open - 0.01) < 1e-12
    assert "주도" in sp.line


def test_missing_inputs_skip_with_a_reason_never_raise():
    """부재는 전부 **사유 한 줄**로 흐른다 - 괴리 때문에 셀이 멈추면 안 된다."""
    cases = [
        (_Lake(rdb=False), "RDB 부재"),
        (_Lake(bars=0), "5분봉 부재"),
        (_Lake(anchor=(None, None, None)), "미등록"),
        (_Lake(anchor=("i1", None, 100.0)), "NAV 없음"),
        (_Lake(anchor=("i1", 100.0, None)), "종가 없음"),
        (_Lake(anchor=("i1", 100.0, 101.0), rows=[("t1", 103.0, 0.01, 1.0)]), "2창 미만"),
        (_Lake(anchor=("i1", 100.0, 101.0), boom="붐"), "질의 실패"),
    ]
    for lake, want in cases:
        sp, why = premium_5m(lake, "091160", "2026-07-27")
        assert sp is None and want in why, (want, why)
        assert why.startswith("괴리 5분 분해 넘어감"), why


def test_half_a_basket_is_missing_data_not_a_premium():
    """커버리지가 바닥 밑이면 넘어간다 - 빠진 종목을 괴리로 읽으면 거짓이 커진다."""
    thin = [("t1", 103.0, 0.01, MIN_COVER - 0.01), ("t2", 104.0, 0.02, 0.9)]
    sp, why = premium_5m(_Lake(anchor=("i1", 100.0, 101.0), rows=thin),
                         "091160", "2026-07-27")
    assert sp is None and "커버리지" in why and "결측" in why

    ok = [("t1", 103.0, 0.01, MIN_COVER), ("t2", 104.0, 0.02, 0.9)]
    sp2, _ = premium_5m(_Lake(anchor=("i1", 100.0, 101.0), rows=ok),
                        "091160", "2026-07-27")
    assert sp2 is not None, "바닥에 걸치면 통과한다"


def test_leader_is_whichever_share_is_bigger():
    """어느 몫이 하루를 끌었는지 산문이 말한다 - 절대크기 비교다."""
    flow = Split(0.02, 0.001, 0.019, 0.0, 0.019,
                 (Win("t", 0.019, 0.02, 0.001, 0.019, 1.0),))
    assert "수급(ETF 고유)" in flow.line
    basket = Split(0.02, 0.019, 0.001, 0.0, 0.001,
                   (Win("t", 0.001, 0.02, 0.019, 0.001, 1.0),))
    assert "주도 바스켓" in basket.line
