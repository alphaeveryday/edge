"""`base_rate` — 오늘의 움직임이 자기 과거에 비해 드문가.

이 두 검사가 잡는 버그는 각각 하나씩 있고, 둘 다 라이브에서만 드러나면 늦다.

(a) **오늘이 분포에 섞이는 버그**와 **부호 절대값을 잊는 버그**. 오늘 값을 과거
    분포에 포함하면 극단일수록 자기 순위가 최상위로 고정돼 초과확률이 `1/(n+1)` 로
    눌린다 - 즉 평범한 날도 드물어 보인다. 반대로 절댓값을 빼먹으면 큰 **하락**이
    "상위 0%" 로 나와 초과확률이 1 에 붙고, 폭락한 날이 '평범한 날' 로 보고된다.
    극단·평범 두 입력에 같은 코드를 태워 `exceed_p` 가 갈리는지로 둘 다 잡는다.

(b) **부재를 0 으로 말하는 버그**. 표본이 없을 때 `exceed_p=1.0` 같은 수를 돌려주면
    호출자(설명 에이전트)는 그것을 "드물지 않다 = 설명할 게 없다" 로 읽는다. 부재는
    기각이 아니다 - 이 저장소가 가장 싫어하는 실패 모드라 판정불가와 사유를 강제한다.
"""
from __future__ import annotations

from edge_analysis.statics.paneltest import MIN_N
from edge_analysis.statics.tool_baserate import _base_rate

DAY = "2026-06-01"


class _Lake:
    """가짜 레이크. (trade_date, ar, is_evt) 세 열만 돌려준다.

    `n` 개의 과거일은 ±0.5% 를 번갈아 낸다(결정론). 오늘 값만 인자로 바꾼다.
    """

    def __init__(self, today: float, n: int = 80, evt: int = 0):
        self.rows = [(f"2026-0{1 + k // 28}-{1 + k % 28:02d}",
                      0.005 if k % 2 else -0.005,
                      1 if k < evt else 0)
                     for k in range(n)]
        self.rows.append((DAY, today, 0))

    def sql(self, q: str):
        assert "trade_date <= DATE" in q            # 오늘 행이 와야 today 를 잰다
        return self.rows


def test_extreme_today_is_rare_and_ordinary_today_is_not():
    rare = _base_rate(_Lake(0.09), day=DAY, instrument_id="i0")
    assert rare["verdict"] == "계산됨" and rare["n"] == 80
    assert rare["exceed_p"] == 0.0                  # ±0.5% 뿐인 과거에 +9% 는 초과 0건
    assert rare["pct_rank"] == 1.0
    assert rare["today"] == 0.09

    plain = _base_rate(_Lake(0.001), day=DAY, instrument_id="i0")
    assert plain["exceed_p"] == 1.0                 # 과거 전부가 오늘보다 크다
    assert plain["exceed_p"] > rare["exceed_p"]

    # 절댓값을 잊으면 폭락이 '평범' 으로 보고된다 - 하락도 드물어야 한다.
    crash = _base_rate(_Lake(-0.09), day=DAY, instrument_id="i0")
    assert crash["exceed_p"] == 0.0 and crash["pct_rank"] == 0.0

    # 사건일이 얇으면 조건부는 침묵하되 cond_n 은 그대로 말한다(부재≠무관).
    thin = _base_rate(_Lake(0.09, evt=2), day=DAY, instrument_id="i0",
                      etype="COMPANY.EARNINGS.RELEASE")
    assert thin["cond_n"] == 2 and thin["cond_pct_rank"] is None
    assert "COMPANY.EARNINGS.RELEASE" in thin["note"]


def test_thin_history_is_undecidable_with_a_reason():
    r = _base_rate(_Lake(0.09, n=MIN_N - 1), day=DAY, instrument_id="i0")
    assert r["verdict"] == "판정불가"
    assert str(MIN_N) in r["reason"] and str(MIN_N - 1) in r["reason"]
    assert r["exceed_p"] is None and r["pct_rank"] is None   # 수를 지어내지 않는다

    class Dead:
        def sql(self, q):
            raise RuntimeError("Catalog Error: rdb does not exist")

    d = _base_rate(Dead(), day=DAY, instrument_id="i0")
    assert d["verdict"] == "판정불가" and "RuntimeError" in d["reason"]
