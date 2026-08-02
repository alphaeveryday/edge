"""층 분해 계약 - 산술이 맞아야 하고, 통계적 적합이 설명을 참칭하면 안 된다."""
import datetime as dt
import re

import numpy as np
import pytest

from edge_analysis.statics.layers import (MARKET_CODE, Rollup, decompose, overlap,
                                          residual_rho)

DAYS = [dt.date(2026, 1, 1) + dt.timedelta(d) for d in range(90)]


def _prices(rets: np.ndarray) -> list[float]:
    return list(100.0 * np.exp(np.concatenate([[0.0], np.cumsum(rets)])))


class FakeLake:
    """`layers_daily` 와 `s3_etf_holdings` 두 질의 모양만 응답한다."""

    def __init__(self, series: dict, holds: dict):
        self.series, self.holds = series, holds

    def sql(self, q: str):
        if "FROM layers_daily" in q:
            kinds = re.search(r"kind IN \(([^)]*)\)", q).group(1)
            want = {k.strip().strip("'") for k in kinds.split(",")}
            return [[s, m["name"], _prices(m["ret"]), DAYS[: len(m["ret"]) + 1],
                     m.get("vol", [1e6] * (len(m["ret"]) + 1))]
                    for s, m in self.series.items() if m["kind"] in want]
        if "FROM s3_etf_holdings" in q:
            etf = re.search(r"etf_id = '([^']+)'", q).group(1)
            return [[t, f"종목{t}", w * 100.0] for t, w in self.holds.get(etf, {}).items()]
        raise AssertionError(f"예상 못 한 질의: {q[:60]}")


def _lake(*, sector_beta: float = 0.0, alien_beta: float = 0.0):
    rng = np.random.default_rng(0)
    n = 80
    mkt = rng.normal(0, 0.01, n)
    sec = rng.normal(0, 0.01, n)          # 시장과 독립인 섹터 요인
    ali = rng.normal(0, 0.01, n)
    tgt = 1.2 * mkt + sector_beta * sec + alien_beta * ali + rng.normal(0, 0.001, n)
    S = {
        MARKET_CODE: {"name": "KODEX 200", "kind": "market", "ret": mkt},
        "T": {"name": "대상ETF", "kind": "sector", "ret": tgt},
        "TWIN": {"name": "쌍둥이", "kind": "sector", "ret": tgt + rng.normal(0, 1e-4, n)},
        "SEC": {"name": "인접섹터", "kind": "sector", "ret": sec},
        "ALIEN": {"name": "무관ETF", "kind": "sector", "ret": ali},
    }
    H = {                                  # T 와의 비중 겹침: TWIN 0.9 · SEC 0.15 · ALIEN 0
        "T":     {"a": 0.5, "b": 0.3, "c": 0.2},
        "TWIN":  {"a": 0.5, "b": 0.3, "c": 0.2},
        "SEC":   {"a": 0.15, "x": 0.85},
        "ALIEN": {"y": 0.6, "z": 0.4},
        MARKET_CODE: {"a": 0.1, "q": 0.9},
    }
    for tk in "abcxyzq":
        S[tk] = {"name": f"종목{tk}", "kind": "stock",
                 "ret": 1.0 * mkt + rng.normal(0, 0.01, n)}
    return FakeLake(S, H)


def _day(n: int = 80) -> str:
    return DAYS[n].isoformat()


# ── 산술 ──────────────────────────────────────────────────────────────────
def test_layer_sum_plus_idio_equals_total_exactly():
    # 20R 실측: 서사가 "원수익 -9.61 = 시장 -6.11 + 고유 +0.55" 를 인쇄했는데
    # 합이 -5.56 이었다 - 산업층 -4.35 를 통째로 빠뜨렸다. 읽는 사람이 검산하면
    # 무너지는 산출은 직관 이전에 신뢰의 문제다. 고유는 **잔여로 정의**되므로
    # 항등식은 부동소수 오차 안에서 정확해야 한다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    assert r is not None
    assert sum(x.contribution for x in r.layers) + r.idio == pytest.approx(r.total, abs=1e-12)


def test_market_layer_always_enters_first():
    # 공통충격이 섹터로 새면 섹터 서사가 거짓이 된다 - 시장은 경쟁 없이 먼저 들어간다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    assert r.layers[0].kind == "시장" and r.layers[0].code == MARKET_CODE


# ── 후보 자격 ─────────────────────────────────────────────────────────────
def test_twin_etf_is_barred_as_tautology():
    # "반도체가 왜 빠졌냐"에 "반도체가 빠져서" 는 산술은 맞아도 설명이 아니다.
    # 라이브 실측: KODEX 반도체를 TIGER 반도체(겹침 0.93)로 설명했다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    assert "쌍둥이" in r.twins
    assert all(x.code != "TWIN" for x in r.layers)


def test_unrelated_etf_is_barred_even_when_it_fits():
    # 라이브 실측: KODEX 2차전지산업을 KODEX 게임산업(겹침 0)이 β0.71[0.40,1.02] 로
    # "설명"했다. 60일 표본의 우연이고 투자자·금융학자 둘 다 즉시 거부한다.
    # **적합도가 아니라 구성 겹침이 후보 자격을 정한다.**
    r = decompose(_lake(sector_beta=0.0, alien_beta=0.9), "T", _day())
    assert "무관ETF" in r.alien
    assert all(x.code != "ALIEN" for x in r.layers)


def test_related_sector_enters_when_it_carries_the_day():
    r = decompose(_lake(sector_beta=1.5), "T", _day())
    assert any(x.code == "SEC" and x.kind == "섹터" for x in r.layers)


def test_layer_count_and_coverage_bound_the_budget():
    # 설명 예산이 유한해야 서사가 닫힌다 - 층 ≤3, 커버 도달 시 정지.
    r = decompose(_lake(sector_beta=1.5), "T", _day(), max_layers=3, cover=0.5)
    assert len(r.layers) <= 3
    if len(r.layers) < 3:                       # 조기 정지했다면 커버를 채웠거나 후보가 없다
        assert r.coverage >= 0.5 or all(x.kind == "시장" for x in r.layers)


# ── 정직성 ────────────────────────────────────────────────────────────────
def test_halted_names_are_excluded_and_counted():
    # 거래량 0 인 날의 수익률 0 은 거짓이다(정지 종가는 직전 값). 다만 **진짜 보합은
    # 정보다** - 시장이 -7% 미는데 안 빠졌으면 그게 그 종목의 힘이다. 거래량으로만 갈린다.
    lake = _lake(sector_beta=0.8)
    lake.series["a"]["vol"] = [0.0] * 81
    r = decompose(lake, "T", _day())
    assert r.halted >= 1
    assert all(n.ticker != "a" for n in r.names)


def test_names_are_ranked_by_weight_times_idio():
    # 큰 종목의 작은 움직임과 작은 종목의 큰 움직임을 같은 자로 잰다.
    r = decompose(_lake(sector_beta=0.8), "T", _day())
    got = [abs(n.contribution) for n in r.names]
    assert got == sorted(got, reverse=True)
    for n in r.names:
        assert n.contribution == pytest.approx(n.weight * n.idio)


def test_rho_reports_leftover_common_factor():
    # ρ≈0 이어야 '고유'라 부를 자격이 생긴다. 남아 있으면 이름 없는 공통요인의 직접 증거다.
    assert residual_rho([np.array([1.0, 2, 3]), np.array([1.0, 2, 3.1])]) is None
    same = [np.array([1.0, 2, 3, 4])] * 3
    assert residual_rho(same) == pytest.approx(1.0)


def test_overlap_is_weight_intersection():
    lake = _lake()
    assert overlap(lake, "T", "TWIN", _day()) == pytest.approx(1.0)
    assert overlap(lake, "T", "ALIEN", _day()) == pytest.approx(0.0)
    assert overlap(lake, "T", "SEC", _day()) == pytest.approx(0.15)


def test_short_history_yields_no_decomposition_not_a_zero():
    # 창이 안 차면 판정불가지 0 이 아니다 - 부재를 기각으로 위장하지 않는다.
    lake = _lake()
    for m in lake.series.values():
        m["ret"] = m["ret"][:10]
    assert decompose(lake, "T", DAYS[10].isoformat()) is None


def test_rollup_is_frozen_dataclass():
    assert Rollup.__dataclass_params__.frozen
