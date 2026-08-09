"""층 회계 계약(ALPHA-862) - 산술이 맞아야 하고, 적합이 설명을 참칭하면 안 된다.

β=1 고정 뒤의 계약은 셋이다: (1) 합 항등식이 부동소수 오차 안에서 정확하다,
(2) 층 기여는 회귀가 아니라 **회계**다 - 시장은 그대로, 섹터는 시장 차감,
(3) 섹터는 **KRX 업종지수뿐**이다(ALPHA-877) - 프록시 ETF 겹침 선택은 폐기했고,
업종지수 분봉이 없는 구간 모드는 정직 부재다. 회귀·ρ·표본 게이트가
있던 자리의 검정은 칼만(ALPHA-803)이 신뢰구간과 함께 다시 가져온다.
"""
import datetime as dt
import re

import numpy as np
import pytest

from edge_analysis.statics.layers import MARKET_CODE, Rollup, decompose

DAYS = [dt.date(2026, 1, 1) + dt.timedelta(d) for d in range(90)]
CLOCK = ("09:00:00", "10:00:00")


def _prices(rets: np.ndarray) -> list[float]:
    return list(100.0 * np.exp(np.concatenate([[0.0], np.cumsum(rets)])))


class FakeLake:
    """`layers_daily`·`s3_etf_holdings`·`bars_5m` 질의 모양만 응답한다."""

    def __init__(self, series: dict, holds: dict):
        self.series, self.holds = series, holds
        self.exists: dict = {}

    def sql(self, q: str):
        if "FROM bars_5m" in q:
            # 구간 모드 5분봉. **pick 필터를 일부러 무시한다** - 섹터 ETF 계열이
            # 패널에 실려 와도 층이 안 서야 하는 계약을 더 세게 시험하기 위해서다.
            return [[s, m["lr5"], 1e6]
                    for s, m in self.series.items() if "lr5" in m]
        if "FROM layers_daily" in q:
            kinds = re.search(r"kind IN \(([^)]*)\)", q).group(1)
            want = {k.strip().strip("'") for k in kinds.split(",")}
            if "list(close" not in q:     # 구간 모드의 이름 조회(_CLOCK_NAMES)
                return [[s, m["name"]] for s, m in self.series.items()
                        if m["kind"] in want]
            only = re.search(r"symbol IN \(([^)]*)\)", q)
            picked = ({s.strip().strip("'") for s in only.group(1).split(",")}
                      if only else None)
            return [[s, m["name"], _prices(m["ret"]), DAYS[: len(m["ret"]) + 1],
                     m.get("vol", [1e6] * (len(m["ret"]) + 1))]
                    for s, m in self.series.items()
                    if m["kind"] in want and (picked is None or s in picked)]
        if "FROM s3_etf_holdings" in q:
            etf = re.search(r"etf_id = '([^']+)'", q).group(1)
            return [[t, f"종목{t}", w * 100.0] for t, w in self.holds.get(etf, {}).items()]
        if "FROM s3_etf_profile" in q:
            return []
        raise AssertionError(f"예상 못 한 질의: {q[:60]}")


def _lake():
    rng = np.random.default_rng(0)
    n = 80
    mkt = rng.normal(0, 0.01, n)
    sec = rng.normal(0, 0.01, n)
    ali = rng.normal(0, 0.01, n)
    tgt = 1.2 * mkt + 0.8 * sec + rng.normal(0, 0.001, n)
    S = {
        MARKET_CODE: {"name": "KODEX 200", "kind": "market", "ret": mkt},
        "T": {"name": "대상ETF", "kind": "sector", "ret": tgt},
        "TWIN": {"name": "쌍둥이", "kind": "sector", "ret": tgt + rng.normal(0, 1e-4, n)},
        "SEC": {"name": "인접섹터", "kind": "sector", "ret": sec},
        "ALIEN": {"name": "무관ETF", "kind": "sector", "ret": ali},
    }
    H = {                                  # T 와의 비중 겹침: TWIN 1.0 · SEC 0.15 · ALIEN 0
        "T":     {"a": 0.5, "b": 0.3, "c": 0.2},
        "TWIN":  {"a": 0.5, "b": 0.3, "c": 0.2},
        "SEC":   {"a": 0.15, "x": 0.85},
        "ALIEN": {"y": 0.6, "z": 0.4},
        MARKET_CODE: {"a": 0.1, "q": 0.9},
    }
    for tk in "abcxyzq":
        S[tk] = {"name": f"종목{tk}", "kind": "stock",
                 "ret": 1.0 * mkt + (0.8 * sec if tk in "abc" else 0.0)
                        + rng.normal(0, 0.01, n)}
    # 일봉 실행 경로를 되살리지 않고 모든 계약을 같은 clock 패널로 검증한다.
    for values in S.values():
        values["lr5"] = float(values["ret"][-1])
    return FakeLake(S, H)


def _day(n: int = 80) -> str:
    return DAYS[n].isoformat()


def _now(lake, sym: str) -> float:
    return float(lake.series[sym]["ret"][-1])


# ── 산술: 회계는 항등식이다 ────────────────────────────────────────────────
def test_layer_sum_plus_idio_equals_total_exactly():
    # 20R 실측: 서사가 "원수익 -9.61 = 시장 -6.11 + 고유 +0.55" 를 인쇄했는데
    # 합이 -5.56 이었다 - 산업층 -4.35 를 통째로 빠뜨렸다. 읽는 사람이 검산하면
    # 무너지는 산출은 직관 이전에 신뢰의 문제다. 고유는 **잔여로 정의**되므로
    # 항등식은 부동소수 오차 안에서 정확해야 한다.
    r = decompose(_lake(), "T", _day(), clock=CLOCK)
    assert r is not None
    assert sum(x.contribution for x in r.layers) + r.idio == pytest.approx(r.total, abs=1e-12)


def test_market_contribution_is_the_market_return_itself():
    """β=1 계약: 시장 기여 = 시장 당일 수익률 **그대로**(ALPHA-862).

    회귀 β 를 곱하면 이 단언이 깨진다 - 계수 고정을 누가 조용히 되돌리면 여기서
    드러난다. 시변 β 는 칼만(ALPHA-803)이 신뢰구간과 함께 가져오는 것이지, 60일
    상수 회귀가 몰래 돌아올 자리가 아니다.
    """
    lake = _lake()
    r = decompose(lake, "T", _day(), clock=CLOCK)
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.contribution == pytest.approx(_now(lake, MARKET_CODE), abs=1e-12)
    assert m.ret == m.contribution


def test_decompose_records_why_it_returned_none():
    """`None` 은 정상 반환값이지만 **침묵이면 안 된다** - 2026-08-06 장중 전 런이
    `layer_route=미상` 이었는데 왜인지 로그로 못 봤다. 이 모듈은 로깅을 모르므로
    `exists` 에 적고 소비는 호출자가 한다."""
    lake = _lake()
    assert decompose(lake, "없는종목", _day(), clock=CLOCK) is None
    assert "없는종목" in lake.exists["layers"]


def test_missing_market_layer_leaves_a_reason():
    """시장 층이 조용히 빠지면 남은 섹터·고유가 시장 몫까지 떠안는다 - 사유가 남는다."""
    lake = _lake()
    del lake.series[MARKET_CODE]
    r = decompose(lake, "T", _day(), clock=CLOCK)
    assert r is not None
    assert all(x.kind != "시장" for x in r.layers)
    assert MARKET_CODE in lake.exists["market_layer"]
    # 시장 층 부재면 섹터도 차감 기준이 없다 - 섹터를 세우지 않는다.
    assert all(x.kind != "섹터" for x in r.layers)


def test_a_later_success_clears_an_earlier_absence():
    """한 런이 `decompose` 를 두 번 부른다(라우팅·설명) - 앞 호출의 실패가 남으면
    뒤 호출이 성공해도 커버리지가 실패를 말한다. 부재를 안 지우는 것은 부재를
    지어내는 것과 같다."""
    lake = _lake()
    assert decompose(lake, "없는종목", _day(), clock=CLOCK) is None
    assert "layers" in lake.exists
    assert decompose(lake, "T", _day(), clock=CLOCK) is not None
    assert "layers" not in lake.exists


def test_decompose_tolerates_a_lake_without_coverage_dicts():
    lake = _lake()
    del lake.exists
    assert decompose(lake, "T", _day(), clock=CLOCK) is not None


# ── 섹터 후보: KRX 업종지수뿐이다 (ALPHA-877) ─────────────────────────────
def test_clock_mode_sector_layer_is_honestly_absent():
    """구간 모드의 섹터 층은 정직 부재다(ALPHA-877) - 업종지수는 분봉이 없고,
    섹터 ETF 5분봉이 패널에 실려 와도 층이 서면 안 된다(프록시 ETF 겹침 선택이
    몰래 부활하면 여기서 깨진다). 사유가 남고 시장+고유 2층 회계는 그대로 선다."""
    lake = _lake()
    lake.series["T"]["lr5"] = 0.02
    lake.series[MARKET_CODE]["lr5"] = 0.01
    lake.series["SEC"]["lr5"] = 0.015     # 프록시 후보였던 섹터 ETF 의 구간수익
    lake.series["TWIN"]["lr5"] = 0.02
    r = decompose(lake, "T", _day(), clock=("09:00:00", "10:00:00"))
    assert r is not None
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.contribution == pytest.approx(0.01, abs=1e-12)
    assert all(x.kind != "섹터" for x in r.layers)
    assert r.twins == () and r.alien == ()
    assert "업종지수 분봉 미수집" in lake.exists["sector_layer"]
    assert sum(x.contribution for x in r.layers) + r.idio == pytest.approx(
        r.total, abs=1e-12), "2층 회계 항등식은 그대로 선다"


# ── 종목 귀속 ─────────────────────────────────────────────────────────────
def test_names_idio_is_return_minus_layer_contributions():
    """종목 고유 = 종목수익 − Σ층기여 (β=1). 순위는 비중 × 고유다 - 큰 종목의
    작은 움직임과 작은 종목의 큰 움직임을 같은 자로 잰다."""
    lake = _lake()
    r = decompose(lake, "T", _day(), clock=CLOCK)
    base = sum(x.contribution for x in r.layers)
    for n in r.names:
        assert n.idio == pytest.approx(_now(lake, n.ticker) - base, abs=1e-12)
        assert n.contribution == pytest.approx(n.weight * n.idio, abs=1e-12)
    ranks = [abs(n.contribution) for n in r.names]
    assert ranks == sorted(ranks, reverse=True)


def test_rollup_is_frozen_dataclass():
    r = decompose(_lake(), "T", _day(), clock=CLOCK)
    assert isinstance(r, Rollup)
    with pytest.raises(AttributeError):
        r.total = 0.0


# ── 1분봉 실측 주입 (ALPHA-866) ───────────────────────────────────────────
def test_intraday_overrides_target_and_market_returns():
    """대상·시장의 구간수익은 호출자가 커밋된 1분봉에서 계산한 값이 선다 - 레이크
    `bars_5m` 은 정본이 낡으면 폴백으로 내려가는 별도 경로라, 발화를 만든 봉과 층
    판정이 다른 가격을 보면 안 된다. 이름은 레이크 것을 지킨다."""
    lake = _lake()
    r = decompose(lake, "T", _day(),
                  clock=CLOCK, intraday={"T": (0.02, False), MARKET_CODE: (0.01, False)})
    assert r.total == pytest.approx(0.02, abs=1e-12)
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.contribution == pytest.approx(0.01, abs=1e-12)
    assert m.name == "KODEX 200", "이름은 레이크 것 - 수익률만 갈아 끼운다"
    assert r.total == pytest.approx(
        sum(x.contribution for x in r.layers) + r.idio, abs=1e-12)


def test_intraday_erects_the_market_layer_when_the_lake_is_stale():
    """레이크에 시장 계열이 아예 없어도(스테일 정본) 1분봉 실측이 시장 층을 세운다 -
    '레이크가 낡아서' 시장 층이 빠지면 남은 층이 시장 몫까지 떠안는다."""
    lake = _lake()
    del lake.series[MARKET_CODE]
    r = decompose(lake, "T", _day(),
                  clock=CLOCK, intraday={"T": (0.02, False), MARKET_CODE: (0.01, False)})
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.ret == pytest.approx(0.01, abs=1e-12)
    assert "market_layer" not in lake.exists, "층이 섰는데 부재 사유가 남으면 오진이다"


def test_intraday_halt_excludes_the_name_and_counts_it():
    """1분봉 실측의 정지 판정(구간 거래량 0)도 레이크 판정과 같은 계약을 탄다 -
    빼되, 뺐다는 사실을 센다."""
    lake = _lake()
    r = decompose(lake, "T", _day(), clock=CLOCK, intraday={
        "T": (0.02, False), MARKET_CODE: (0.01, False),
        "a": (0.1, True), "b": (0.0, False), "c": (0.0, False)})
    assert all(n.ticker != "a" for n in r.names)
    assert r.halted == 1


def test_intraday_mode_market_gap_does_not_fall_back_to_the_lake():
    """커밋 봉 모드에서 시장 결손이면 시장 층을 **세우지 않는다** - 레이크에 시장
    계열이 멀쩡히 있어도. 내려가면 발화를 만든 봉과 판정이 다른 가격을 보는 갈림이
    결손일마다 조용히 부활하고, 원장 어디에도 그 표식이 없다(Rule 12). 차감 기준이
    없으니 섹터도 안 선다."""
    lake = _lake()                    # 레이크에는 시장 계열이 멀쩡히 있다
    r = decompose(lake, "T", _day(), clock=CLOCK, intraday={"T": (0.02, False)})
    assert r is not None
    assert all(x.kind != "시장" for x in r.layers)
    assert "커밋 1분봉 결손" in lake.exists["market_layer"]
    assert all(x.kind != "섹터" for x in r.layers)


def test_intraday_mode_target_gap_is_absence_not_a_lake_read():
    """커밋 봉 모드에서 대상 자신이 결손이면 분해는 부재다 - 레이크의 대상 계열로
    지어내지 않는다. 부재 사유가 남는다."""
    lake = _lake()
    r = decompose(lake, "T", _day(), clock=CLOCK, intraday={MARKET_CODE: (0.01, False)})
    assert r is None
    assert "layers" in lake.exists


def test_intraday_mode_names_exclude_units_the_ledger_dropped():
    """원장이 결손으로 떨어뜨린 구성종목은 귀속에서도 빠진다 - 레이크에 그 종목이
    있어도 되살리지 않는다. dropped_units 와 귀속이 같은 세계를 말해야 한다."""
    lake = _lake()                    # 레이크에는 a·b·c 전부 있다
    r = decompose(lake, "T", _day(), clock=CLOCK, intraday={
        "T": (0.02, False), MARKET_CODE: (0.01, False), "a": (0.3, False)})
    assert {n.ticker for n in r.names} == {"a"}
    assert r.weight_covered == pytest.approx(0.5, abs=1e-12), "커버리지가 얇음을 수치로 말해야 한다"


# ── 광역 ETF 2층 (ALPHA-871) ──────────────────────────────────────────────
