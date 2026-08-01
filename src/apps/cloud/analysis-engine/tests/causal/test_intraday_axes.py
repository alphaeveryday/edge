"""일중 축 다리 — P1 이 시간 분해의 킬러를 실제로 받는지.

에이전트 층 감사(2026-08-01)의 수술 검증: 종전에는 intraday_shape·timing 이
무조건 '원장 미보유'였다. 다리가 있으면 실측으로 대체되고(가설 킬러가 P2
프롬프트에 실린다), 다리가 죽으면 종전 부재 선언이 그대로 남아야 한다 —
측정 실패가 셀 실패로 번지면 안 된다.
"""
from datetime import datetime

from edge_analysis.causal.contracts import Question
from edge_analysis.causal.intraday_axes import measure
from edge_analysis.causal.p1_fingerprint import take


class _Lake:
    """CausalLake 호환 스텁 — 갭이 지배하고, 마감 후 보도가 2건인 하루."""
    exists = {"rdb": True}

    def taus(self, instrument_id, day):
        return [(datetime(2026, 6, 1, 10, 0), "e1"),
                (datetime(2026, 6, 1, 16, 30), "e2"),      # 마감 후
                (datetime(2026, 6, 1, 21, 0), "e3")]       # 마감 후

    def bars(self, ticker, day):
        return [(datetime(2026, 6, 1, 9, 0), 103.0),        # 갭 +3%
                (datetime(2026, 6, 1, 10, 5), 102.8),
                (datetime(2026, 6, 1, 15, 30), 102.9)]      # 장중 -0.1%

    def prev_close(self, ticker, day):
        return 100.0


class _DeadLake:
    exists = {"rdb": False}

    def bars(self, ticker, day):
        raise RuntimeError("bars 없음")

    def prev_close(self, ticker, day):
        raise RuntimeError("없음")


def test_measure_produces_killers_from_decomposition():
    axes = measure(_Lake(), "000660.KS", "inst_x", "2026-06-01")
    shape, timing = axes["intraday_shape"], axes["intraday_timing"]
    assert shape.available and timing.available
    # 갭(+3%)이 장중(|-0.2%|+...)을 지배한다 → 장중 사건 주도 부류가 죽는다.
    assert any("갭" in k for k in shape.kills)
    # 마감 후 보도 2건 → 알리바이 킬러.
    assert timing.value["after_close"] == 2
    assert any("마감 후" in k for k in timing.kills)
    assert timing.value["event_windows"] == 1                # 10:00 창 하나


def test_measure_failure_returns_empty_not_raise():
    assert measure(_DeadLake(), "t", "i", "2026-06-01") == {}


def test_take_replaces_placeholders_only_when_bridge_present():
    q = Question(etf_instrument_id="091160", etf_name="테스트 ETF",
                 trade_date=datetime(2026, 6, 1).date(),
                 as_of="2026-06-01T15:40:00+09:00", observed=0.01, residual=0.01,
                 route_code="EVENT", explanandum="r⊥ = +1.00%",
                 intervention="사건이 없던 세계", answer_form="구간")
    # cd·sql 없이도 P1 은 부재를 선언하며 돈다 (침묵 금지 규율).
    fp_without = take(None, None, question=q, candidates=[])
    ax = fp_without.get("intraday_shape")
    assert ax is not None and not ax.available                # 종전 동작 보존
    bridged = measure(_Lake(), "t", "i", "2026-06-01")
    fp_with = take(None, None, question=q, candidates=[], intraday=bridged)
    ax2 = fp_with.get("intraday_shape")
    assert ax2 is not None and ax2.available                  # 실측으로 대체
    assert any("갭" in k for k in fp_with.kills)              # 킬러가 브리프에 실린다
