"""flow_detail 계약 - 주체별 z 는 독립 재계산과 맞고, **오늘 수급은 안 본다**.

(b) 가 이 파일의 존재 이유다. 투자자별 매매 집계는 장 마감 후 18:00 KST 에
공표되므로 오늘 값을 오늘 움직임의 근거로 쓰면 아직 관측되지 않은 값을 원인
자리에 놓는 것이 된다 - 그건 인과가 아니라 정의상 동시발생이다. 선견은 코드에
흔적을 남기지 않고 숫자만 좋아지므로, 오늘 행을 **넣어도 결과가 안 바뀐다**는
회귀 테스트만이 그것을 잡는다.
"""
from datetime import date, timedelta
from statistics import mean, stdev

import duckdb
import pytest

from edge_analysis.statics.core import paneltest, tool_flow
from edge_analysis.statics.core.tool_flow import ACTORS, Z_WINDOW

IID = "inst_TEST"
TKR = "000660"
WINDOW = 5
TURNOVER = 100_000.0
N_HIST = 40                      # MIN_N=30 을 넘긴다
DAYS = [date(2026, 3, 2) + timedelta(days=t) for t in range(N_HIST + 1)]
TODAY = DAYS[N_HIST]             # 이 날은 아직 공표되지 않은 날이다
SPIKE_ACTOR = "pension"


def _nv(col: str, t: int) -> float:
    """결정론 의사변동 + 연금만 마지막 이력일에 급증. 급증을 심는 이유는 top 판정이
    자명하지 않으면 그 단정이 검정된 것이 아니기 때문이다."""
    i = [c for c, _ in ACTORS].index(col)
    v = float(((t * 7 + i * 3) % 11) - 5) * 1_000.0
    if col == SPIKE_ACTOR and t == N_HIST - 1:
        v += 500_000.0
    return v


class _Lake:
    """가짜 레이크 - 실제 DuckDB 위에 원천 표만 놓는다. SQL 을 파이썬으로 흉내내면
    검정되는 것이 흉내이므로, 도구가 내보내는 질의 원문을 그대로 돌린다."""

    def __init__(self, with_today: bool) -> None:
        self.con = duckdb.connect()
        cols = ", ".join(f"net_val_{c} BIGINT" for c, _ in ACTORS)
        self.con.execute("CREATE TABLE t_instrument(instrument_id VARCHAR, ticker VARCHAR)")
        self.con.execute("CREATE TABLE t_pit(instrument_id VARCHAR, trade_date DATE, turnover DOUBLE)")
        self.con.execute(f"CREATE TABLE s3_investor_flow(ticker VARCHAR, trade_date DATE, {cols})")
        self.con.execute("CREATE TABLE s3_investor_value("
                         "ticker VARCHAR, trade_date DATE, investor_type VARCHAR, net_value BIGINT)")
        self.con.execute("INSERT INTO t_instrument VALUES (?, ?)", [IID, TKR])

        span = range(N_HIST + 1) if with_today else range(N_HIST)
        for t in span:
            self.con.execute("INSERT INTO t_pit VALUES (?, ?, ?)", [IID, DAYS[t], TURNOVER])
            # 넓은 형식은 최근분만(실측 11거래일), 긴 형식이 이력을 덮는다.
            # 겹치는 35~36 일은 같은 값 - 합집합 중복 제거가 값을 흔들지 않아야 한다.
            if t >= 35:
                vals = [TKR, DAYS[t]] + [_nv(c, t) for c, _ in ACTORS]
                self.con.execute(
                    f"INSERT INTO s3_investor_flow VALUES ({', '.join('?' * len(vals))})", vals)
            if t <= 36:
                for c, _ in ACTORS:
                    self.con.execute("INSERT INTO s3_investor_value VALUES (?, ?, ?, ?)",
                                     [TKR, DAYS[t], c, _nv(c, t)])

    def sql(self, q: str) -> list:
        return self.con.execute(q).fetchall()


@pytest.fixture(autouse=True)
def _fake_base(monkeypatch):
    """`_base(day)` 자리에 v_instrument·v_pit 만 있는 최소 표면을 끼운다. 도구는
    `_base(day) + _SQL` 을 **선행 콤마**로 잇는다는 계약을 그대로 검정한다."""
    monkeypatch.setattr(paneltest, "_base",
                        lambda day, clock="00:00:00":
                        "WITH v_instrument AS (SELECT * FROM t_instrument), "
                        "v_pit AS (SELECT * FROM t_pit)")


def _expected() -> dict[str, float]:
    """주체별 z 를 SQL 과 무관하게 다시 계산한다. cum_norm = 창 내 순매수 합 /
    창 내 평균 거래대금 · z = 직전 Z_WINDOW 행(당일 제외) 대비 표본 z."""
    out = {}
    for col, ko in ACTORS:
        cn = []
        for t in range(N_HIST):
            lo = max(0, t - WINDOW + 1)
            # 거래대금이 상수라 창 평균도 상수다 - 분모를 단순화해도 정의는 같다.
            cn.append(sum(_nv(col, u) for u in range(lo, t + 1)) / TURNOVER)
        prior = cn[max(0, N_HIST - 1 - Z_WINDOW):N_HIST - 1]
        out[ko] = (cn[-1] - mean(prior)) / stdev(prior)
    return out


def test_actor_z_and_top():
    got = tool_flow._flow_detail(_Lake(with_today=False), day=TODAY.isoformat(),
                                 instrument_id=IID, window=WINDOW)
    assert got["verdict"] == "계산됨" and got["n_days"] == N_HIST
    exp = _expected()
    for ko, z in exp.items():
        a = got["by_actor"][ko]
        assert a["verdict"] == "계산됨", a["reason"]
        assert a["z"] == pytest.approx(z, rel=1e-9, abs=1e-12)
    # 급증을 심은 주체가 |z| 최대여야 한다 - 그리고 그 급증은 넓은 형식에만 있다.
    ko_spike = dict(ACTORS)[SPIKE_ACTOR]
    assert got["top"] == ko_spike
    assert max(exp, key=lambda k: abs(exp[k])) == ko_spike
    # 절대 금액이 아니라 거래대금 정규화 값이다 - 급증 50만원 / 창 평균 10만원.
    assert got["by_actor"][ko_spike]["cum_norm"] > 4.0


def test_today_flow_is_not_used():
    """오늘(=day) 행을 넣어도 결과가 한 자리도 안 바뀐다. 18:00 공표 지연 규율의
    회귀 테스트다 - 어제 수급은 개장 전에 알려져 있으니 근거가 되지만 오늘 수급은
    아니다. 분모(거래대금)도 같은 규율로 잘려야 하므로 v_pit 에도 오늘을 넣는다."""
    kw = dict(day=TODAY.isoformat(), instrument_id=IID, window=WINDOW)
    assert (tool_flow._flow_detail(_Lake(with_today=True), **kw)
            == tool_flow._flow_detail(_Lake(with_today=False), **kw))
