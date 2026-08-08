"""칼만 시변 β 배선(ALPHA-803 2단계) - 층 회계·가설 제안 입력의 계약.

왜 이 테스트들인가 (Rule 9):
  - 시장 기여가 **Σ β_t·r_m,t (경로 적분)** 이어야 층 항등식이 회계로 남는다.
    독립 재계산(kbeta 코어 직접 호출)과의 일치가 배선의 정의다.
  - 참 β=1.5 합성에서 기여가 β=1 값(r_m 그대로)과 달라야 한다 - 이 핀이 없으면
    β=1 로 되돌리는 회귀가 조용히 지나간다.
  - 폴백은 정직해야 한다: 전일 5분봉 부재·계열 부족이면 β=1 로 접되 사유가
    `lake.exists["market_beta"]` 에 남는다(Rule 12 - 조용한 폴백 금지).
  - 일 모드는 불변이다 - 이 배선은 구간/커밋 봉 모드에만 닿는다.
  - facts 의 β 요약(`beta_path_line`)은 결정론 문자열이다 - LLM 프롬프트 입력이라
    포맷이 흔들리면 제안 재현이 흔들린다.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from edge_analysis.statics.kbeta import wired_beta
from edge_analysis.statics.layers import MARKET_CODE, decompose

DAY = "2026-07-16"
DAYS = [dt.date(2026, 7, 12) + dt.timedelta(d) for d in range(5)]  # 마지막 = DAY

# 합성 재료 - 참 β=1.5. 전일 5분봉(78봉)이 Q·R·β0 을, 당일 30봉이 경로를 준다.
_rng = np.random.default_rng(42)
_x_prev = _rng.normal(0, 0.0015, 78)
_y_prev = 1.5 * _x_prev + _rng.normal(0, 5e-4, 78)
M_PREV = 100.0 * np.exp(np.cumsum(_x_prev))
S_PREV = 50.0 * np.exp(np.cumsum(_y_prev))
X = _rng.normal(0, 0.0015, 30)                     # 당일 시장 봉수익
Y = 1.5 * X + _rng.normal(0, 5e-4, 30)             # 당일 대상 봉수익
PATHS = {"T": tuple(Y), MARKET_CODE: tuple(X)}
M_NOW = float(np.sum(X))                           # β=1 이면 시장 기여가 이 값 그대로다
INTRADAY = {"T": (float(np.sum(Y)), False),
            MARKET_CODE: (M_NOW, False),
            "a": (0.01, False)}


class FakeLake:
    """구간 모드 + 전일 `bars_5m` 파티션 질의 모양만 응답한다."""

    def __init__(self, prev_bars: bool = True):
        self.prev_bars = prev_bars
        self.exists: dict = {}

    def sql(self, q: str):
        if "FROM bars_5m" in q and "trade_date <" in q:      # 전일 파티션 (β 재료)
            if not self.prev_bars:
                return []
            return ([["T", i, float(S_PREV[i])] for i in range(len(S_PREV))]
                    + [[MARKET_CODE, i, float(M_PREV[i])] for i in range(len(M_PREV))])
        if "FROM bars_5m" in q:                              # 구간 패널 - intraday 가 덮는다
            return []
        if "FROM layers_daily" in q and "list(close" not in q:
            return [["T", "대상ETF"], [MARKET_CODE, "KODEX 200"]]
        if "FROM layers_daily" in q:                         # 일 모드 계열
            ret = {"T": 0.02, MARKET_CODE: 0.01}
            return [[s, s, [100.0, 100.0 * np.exp(r)], DAYS[-2:], [1e6, 1e6]]
                    for s, r in ret.items()]
        if "FROM s3_etf_holdings" in q:
            return [["a", "종목a", 100.0]] if "etf_id = 'T'" in q else []
        if "FROM s3_etf_profile" in q:
            return []
        if "FROM sector_member" in q or "FROM sector_index" in q:
            return []
        raise AssertionError(f"예상 못 한 질의: {q[:60]}")


def _roll(lake=None, paths=PATHS):
    lake = lake if lake is not None else FakeLake()
    r = decompose(lake, "T", DAY, clock=("09:00:00", "15:30:00"),
                  intraday=INTRADAY, paths=paths)
    assert r is not None
    return lake, r


# ── 항등식: 시장 기여 = Σ β_t·r_m,t, 층 합 + 고유 = 구간수익 ────────────────
def test_market_contribution_is_the_path_integral():
    lake, r = _roll()
    market = r.layers[0]
    assert market.kind == "시장"
    # 독립 재계산 - 같은 재료로 kbeta 코어를 직접 돌린 값과 일치해야 배선이다
    res = wired_beta(S_PREV, M_PREV, Y, X)
    assert res["verdict"] == "성립"
    assert market.contribution == pytest.approx(float(res["beta"] @ X))
    # 고유는 잔여 정의 - 항등식이 부동소수 오차 안에서 정확하다
    assert sum(x.contribution for x in r.layers) + r.idio == \
        pytest.approx(r.total, abs=1e-12)
    # 시변 β 가 섰으므로 폴백 사유가 없어야 한다 - 있으면 오진이다
    assert "market_beta" not in lake.exists
    # Layer.ret 은 원 시장수익 그대로다 - 기여만 경로 적분으로 바뀐다
    assert market.ret == pytest.approx(M_NOW)


def test_true_beta_pin_contribution_differs_from_beta_one():
    """참 β=1.5 합성 - β=1 로 되돌리면 이 테스트가 깨진다."""
    _, r = _roll()
    c = r.layers[0].contribution
    assert c != pytest.approx(M_NOW, abs=1e-6), "기여가 β=1 값 그대로다 - 배선이 죽었다"
    # 참 β=1.5 이므로 기여는 1.5·r_m 에 β=1 값보다 가까워야 한다
    assert abs(c - 1.5 * M_NOW) < abs(c - M_NOW)


def test_beta_summary_fields_on_rollup():
    _, r = _roll()
    assert len(r.beta_quarters) == 4                      # 경로 4분할
    assert all(np.isfinite(q) for q in r.beta_quarters)
    # 참 β=1.5 근방을 좇는다 (전일 OLS prior 가 이미 1.5 근방)
    assert all(1.0 < q < 2.0 for q in r.beta_quarters)
    assert r.beta_ci is not None and r.beta_ci > 0        # P_t 유도 신뢰폭


# ── 폴백: 정직해야 한다 (Rule 12) ──────────────────────────────────────────
def test_fallback_when_prev_day_bars_missing():
    lake, r = _roll(lake=FakeLake(prev_bars=False))
    assert "시장 층 β=1 폴백" in lake.exists["market_beta"]
    assert r.layers[0].contribution == pytest.approx(M_NOW)   # β=1 값으로 선다
    assert r.beta_quarters == () and r.beta_ci is None


def test_fallback_when_paths_missing():
    lake, r = _roll(paths=None)
    assert "시장 층 β=1 폴백" in lake.exists["market_beta"]
    assert "계열 미제공" in lake.exists["market_beta"]
    assert r.layers[0].contribution == pytest.approx(M_NOW)


def test_fallback_when_paths_too_short():
    lake, r = _roll(paths={"T": (0.001,), MARKET_CODE: (0.001,)})
    assert "시장 층 β=1 폴백" in lake.exists["market_beta"]
    assert r.layers[0].contribution == pytest.approx(M_NOW)


# ── 일 모드 불변 ───────────────────────────────────────────────────────────
def test_day_mode_is_untouched():
    lake = FakeLake()
    r = decompose(lake, "T", DAYS[-1].isoformat())
    assert r is not None
    assert r.layers[0].contribution == pytest.approx(0.01)    # β=1 회계 그대로
    assert r.beta_quarters == () and r.beta_ci is None
    assert "market_beta" not in lake.exists                   # 폴백 사유도 없다


# ── facts 의 β 요약: 결정론 문자열 ─────────────────────────────────────────
def test_beta_path_line_is_deterministic():
    from edge_analysis.statics.interval import beta_path_line
    line = beta_path_line((1.141, 1.02, 1.487, 0.804), -0.0169, 0.0012)
    assert line == "장중 β 1.14→1.02→1.49→0.80 · 시장 기여 -1.69%p [±0.12]"
    assert line == beta_path_line((1.141, 1.02, 1.487, 0.804), -0.0169, 0.0012)
    assert beta_path_line((), -0.0169, 0.0012) == ""          # β=1 폴백이면 빈 문자열
    assert beta_path_line((1.1, 1.0), None, None) == ""       # 시장 층 부재
