"""층 회계 계약(ALPHA-862) - 산술이 맞아야 하고, 적합이 설명을 참칭하면 안 된다.

β=1 고정 뒤의 계약은 셋이다: (1) 합 항등식이 부동소수 오차 안에서 정확하다,
(2) 층 기여는 회귀가 아니라 **회계**다 - 시장은 그대로, 섹터는 시장 차감,
(3) 섹터 후보는 적합이 아니라 **구성 겹침**이 정한다. 회귀·ρ·표본 게이트가
있던 자리의 검정은 칼만(ALPHA-803)이 신뢰구간과 함께 다시 가져온다.
"""
import datetime as dt
import re

import numpy as np
import pytest

from edge_analysis.statics.layers import MARKET_CODE, Rollup, decompose, overlap

DAYS = [dt.date(2026, 1, 1) + dt.timedelta(d) for d in range(90)]


def _prices(rets: np.ndarray) -> list[float]:
    return list(100.0 * np.exp(np.concatenate([[0.0], np.cumsum(rets)])))


class FakeLake:
    """`layers_daily` 와 `s3_etf_holdings` 두 질의 모양만 응답한다."""

    def __init__(self, series: dict, holds: dict):
        self.series, self.holds = series, holds
        self.exists: dict = {}

    def sql(self, q: str):
        if "FROM layers_daily" in q:
            kinds = re.search(r"kind IN \(([^)]*)\)", q).group(1)
            want = {k.strip().strip("'") for k in kinds.split(",")}
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
        if "FROM sector_member" in q or "FROM sector_index" in q:
            return []                     # KRX 표 부재 - 후보 없음으로 접힌다
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
    r = decompose(_lake(), "T", _day())
    assert r is not None
    assert sum(x.contribution for x in r.layers) + r.idio == pytest.approx(r.total, abs=1e-12)


def test_market_contribution_is_the_market_return_itself():
    """β=1 계약: 시장 기여 = 시장 당일 수익률 **그대로**(ALPHA-862).

    회귀 β 를 곱하면 이 단언이 깨진다 - 계수 고정을 누가 조용히 되돌리면 여기서
    드러난다. 시변 β 는 칼만(ALPHA-803)이 신뢰구간과 함께 가져오는 것이지, 60일
    상수 회귀가 몰래 돌아올 자리가 아니다.
    """
    lake = _lake()
    r = decompose(lake, "T", _day())
    m = next(x for x in r.layers if x.kind == "시장")
    assert m.contribution == pytest.approx(_now(lake, MARKET_CODE), abs=1e-12)
    assert m.ret == m.contribution


def test_sector_contribution_is_market_subtracted():
    """섹터 기여 = 섹터 수익 − 시장 수익 (β=1 직교화).

    차감이 없으면 시장 몫이 두 번 세어진다 - "시장이 민 건지 섹터가 민 건지"
    배분 순서 논쟁이 그대로 돌아온다. `ret` 은 원 수익률로 남긴다: 산문이
    "섹터 +1.2% (시장 차감 +0.4%p)" 를 말할 재료다.
    """
    lake = _lake()
    r = decompose(lake, "T", _day())
    s = next(x for x in r.layers if x.kind == "섹터")
    assert s.code == "SEC"
    assert s.ret == pytest.approx(_now(lake, "SEC"), abs=1e-12)
    assert s.contribution == pytest.approx(
        _now(lake, "SEC") - _now(lake, MARKET_CODE), abs=1e-12)


# ── 부재는 침묵이 아니다 ──────────────────────────────────────────────────
def test_decompose_records_why_it_returned_none():
    """`None` 은 정상 반환값이지만 **침묵이면 안 된다** - 2026-08-06 장중 전 런이
    `layer_route=미상` 이었는데 왜인지 로그로 못 봤다. 이 모듈은 로깅을 모르므로
    `exists` 에 적고 소비는 호출자가 한다."""
    lake = _lake()
    assert decompose(lake, "없는종목", _day()) is None
    assert "없는종목" in lake.exists["layers"]


def test_target_without_todays_row_is_absent_not_stale():
    """당일 행이 없으면 어제 값으로 지어내지 않는다 - 부재로 선다.

    낡은 값으로 회계하면 산출은 나오는데 그 수치가 어제 것이다 - 조용히 틀린
    설명이 부재보다 나쁘다.
    """
    lake = _lake()
    lake.series["T"]["ret"] = lake.series["T"]["ret"][:-1]   # T 만 하루 늦다
    assert decompose(lake, "T", _day()) is None
    assert "layers" in lake.exists


def test_missing_market_layer_leaves_a_reason():
    """시장 층이 조용히 빠지면 남은 섹터·고유가 시장 몫까지 떠안는다 - 사유가 남는다."""
    lake = _lake()
    del lake.series[MARKET_CODE]
    r = decompose(lake, "T", _day())
    assert r is not None
    assert all(x.kind != "시장" for x in r.layers)
    assert MARKET_CODE in lake.exists["market_layer"]
    # 시장 층 부재면 섹터도 차감 기준이 없다 - 섹터를 세우지 않는다.
    assert all(x.kind != "섹터" for x in r.layers)


def test_market_proxy_explaining_itself_is_not_an_absence():
    """대상이 시장 프록시 자신이면 시장 층 없음은 정상이다 - 사유를 적으면
    정상 런마다 존재하지 않는 결손을 신고한다(조용한 부재를 시끄러운 오진으로)."""
    lake = _lake()
    r = decompose(lake, MARKET_CODE, _day())
    assert r is not None
    assert "market_layer" not in lake.exists


def test_a_later_success_clears_an_earlier_absence():
    """한 런이 `decompose` 를 두 번 부른다(라우팅·설명) - 앞 호출의 실패가 남으면
    뒤 호출이 성공해도 커버리지가 실패를 말한다. 부재를 안 지우는 것은 부재를
    지어내는 것과 같다."""
    lake = _lake()
    assert decompose(lake, "없는종목", _day()) is None
    assert "layers" in lake.exists
    assert decompose(lake, "T", _day()) is not None
    assert "layers" not in lake.exists


def test_decompose_tolerates_a_lake_without_coverage_dicts():
    lake = _lake()
    del lake.exists
    assert decompose(lake, "T", _day()) is not None


# ── 섹터 후보: 겹침이 자격이다 ─────────────────────────────────────────────
def test_twin_etf_is_barred_as_tautology():
    """겹침 ≥ TAUTOLOGY_CUT 은 같은 것이다 - "반도체가 왜 빠졌냐"에 "반도체가
    빠져서"는 설명이 아니다. 조용히 빼지 않고 twins 로 남긴다."""
    r = decompose(_lake(), "T", _day())
    assert any("쌍둥이" in t for t in r.twins)
    assert all(x.code != "TWIN" for x in r.layers)


def test_unrelated_etf_is_barred_even_when_it_fits():
    """겹침 < MIN_OVERLAP 은 근거가 없다 - 게임 ETF 가 2차전지를 "설명"하는 일이
    실제로 있었다. 산술이 맞아도 아무도 안 믿는다."""
    r = decompose(_lake(), "T", _day())
    assert any("무관ETF" in a for a in r.alien)
    assert all(x.code != "ALIEN" for x in r.layers)


def test_sector_is_picked_by_overlap_not_by_fit():
    """후보 선정은 구성 겹침이지 적합이 아니다(ALPHA-862 에서 회귀 선택 폐지).

    적합으로 고르면 '가장 잘 맞는 무엇'이 섹터를 참칭한다 - 042700 07-31 에서
    삼성전자가 섹터로 뽑힌 그 실수. 겹침은 포트폴리오 사실이라 데이터가 우연히
    맞아도 안 바뀐다.
    """
    r = decompose(_lake(), "T", _day())
    s = next(x for x in r.layers if x.kind == "섹터")
    assert s.code == "SEC"
    assert s.overlap == pytest.approx(0.15)


# ── 종목 귀속 ─────────────────────────────────────────────────────────────
def test_names_idio_is_return_minus_layer_contributions():
    """종목 고유 = 종목수익 − Σ층기여 (β=1). 순위는 비중 × 고유다 - 큰 종목의
    작은 움직임과 작은 종목의 큰 움직임을 같은 자로 잰다."""
    lake = _lake()
    r = decompose(lake, "T", _day())
    base = sum(x.contribution for x in r.layers)
    for n in r.names:
        assert n.idio == pytest.approx(_now(lake, n.ticker) - base, abs=1e-12)
        assert n.contribution == pytest.approx(n.weight * n.idio, abs=1e-12)
    ranks = [abs(n.contribution) for n in r.names]
    assert ranks == sorted(ranks, reverse=True)


def test_halted_names_are_excluded_and_counted():
    """거래정지 종목의 수익률 0 은 참이 아니다 - 빼되, 뺐다는 사실을 센다."""
    lake = _lake()
    lake.series["a"]["vol"] = [1e6] * 80 + [0]      # 당일만 거래량 0
    r = decompose(lake, "T", _day())
    assert all(n.ticker != "a" for n in r.names)
    assert r.halted == 1


def test_short_history_yields_no_decomposition_not_a_zero():
    """이력이 한 점뿐이면 당일 수익률 자체가 없다 - 0 이 아니라 부재다."""
    lake = _lake()
    for m in lake.series.values():
        m["ret"] = m["ret"][:0]
    assert decompose(lake, "T", DAYS[0].isoformat()) is None


# ── 구조 ──────────────────────────────────────────────────────────────────
def test_overlap_is_weight_intersection():
    lake = _lake()
    assert overlap(lake, "T", "TWIN", _day()) == pytest.approx(1.0)
    assert overlap(lake, "T", "SEC", _day()) == pytest.approx(0.15)
    assert overlap(lake, "T", "ALIEN", _day()) == pytest.approx(0.0)


def test_rollup_is_frozen_dataclass():
    r = decompose(_lake(), "T", _day())
    assert isinstance(r, Rollup)
    with pytest.raises(AttributeError):
        r.total = 0.0
