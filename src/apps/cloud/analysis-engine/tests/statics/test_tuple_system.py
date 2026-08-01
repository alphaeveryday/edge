"""튜플 체계(가설·검정 에이전트)의 계약 — 감사 5라운드의 교훈이 전부 단언이다.

가설: 어휘 밖·접지 밖·채널 중복은 생성 시점에 죽고, 되물음은 사유를 싣는다.
검정: 표본은 튜플에서 유도되고(취약성 = INUS 조건화), 부재는 판정불가+사유이며,
같은 입력은 같은 판정(결정론). 성립해도 오늘 취약성 미충족이면 부적용.
반사실은 positivity 를 갖출 때만 채워진다.
"""
import numpy as np
import pytest

from edge_analysis.statics.hypothesize import propose
from edge_analysis.statics.paneltest import MIN_OPPOSITE, edge_test
from edge_analysis.statics.vocab import (ExposureSource, HypothesisTuple,
                                         MIN_N, Trigger, Vulnerability)

ETYPES = ["COMPANY.PRODUCT.LAUNCH", "MARKET_STRUCTURE.INDEX.INCLUSION"]


def _h(channel="Q수량", ident="COMPANY.PRODUCT.LAUNCH", **kw):
    base = {"vulnerabilities": [{"family": "수급", "transform": "누적",
                                 "comparator": ">=", "percentile": 0.9}],
            "trigger": {"kind": "점", "ident": ident},
            "channel": channel,
            "exposure": {"kind": "속성", "ident": "가격잔차", "transform": "누적"},
            "from_role": "ISSUER", "to_role": "ISSUER",
            "outcome": "수익률", "sign": 1, "reduction_note": "n"}
    base.update(kw)
    return base


# ── 가설 에이전트 ────────────────────────────────────────────────────────
def test_propose_kills_fabrication_and_duplicates_and_reasks():
    calls = []

    def ask(system, user):
        calls.append(user)
        if len(calls) == 1:
            return {"hypotheses": [_h(), _h(ident="EVT_지어냄"),
                                   _h(channel="새채널"), _h(channel="Q수량")]}
        return {"hypotheses": [_h(), _h(channel="FX환",
                                        ident="MARKET_STRUCTURE.INDEX.INCLUSION")]}

    valid, rejected = propose(ask, facts="사실", event_types=ETYPES)
    assert len(valid) == 2 and {t.channel for t in valid} == {"Q수량", "FX환"}
    assert any("날조" in r for r in rejected)
    assert any("중복" in r for r in rejected)
    assert len(calls) == 2 and "거부 사유" in calls[1]


def test_propose_returns_empty_handed_rather_than_forcing():
    ask = lambda s, u: {"hypotheses": [_h(ident="없는타입")]}   # noqa: E731
    valid, rejected = propose(ask, facts="사실", event_types=ETYPES)
    assert valid == [] and rejected


def test_propose_surfaces_measurable_affordance():
    seen = {}
    ask = lambda s, u: seen.setdefault("sys", s) and {} or {"hypotheses": [_h(), _h(channel="FX환")]}  # noqa: E731
    propose(ask, facts="x", event_types=ETYPES, measurable=[("가격잔차", "누적")])
    assert "잴 수 있는 노출" in seen["sys"] and "가격잔차" in seen["sys"]


# ── 검정 에이전트 ────────────────────────────────────────────────────────
def _tuple(vuln_family="수급", vuln_tr="누적", trigger=("점", "COMPANY.PRODUCT.LAUNCH"),
           sign=1, pct=0.5):
    return HypothesisTuple(
        vulnerabilities=(Vulnerability(vuln_family, vuln_tr, ">=", pct),),
        trigger=Trigger(*trigger), channel="Q수량",
        exposure=ExposureSource("속성", "가격잔차", transform="누적"),
        from_role="ISSUER", to_role="ISSUER", outcome="수익률", sign=sign)


class _Lake:
    """가짜 패널. 취약성(거래량/수준) 충족 반쪽에서만 용량-반응이 실재한다."""

    def __init__(self, n=400, effect=0.02, seed=1, today=(1.0, 1.0), today_n=0):
        rng = np.random.default_rng(seed)
        x = rng.normal(size=n)                       # 노출
        v = rng.normal(size=n)                       # 취약성 피처
        sat = v >= np.quantile(v, 0.5)
        hi = x >= np.quantile(x, 0.8)
        ar = effect * (hi & sat) + rng.normal(scale=0.004, size=n)
        dates = [f"2026-0{1 + i % 5}-01" for i in range(n)]
        self.panel = [(f"i{k}", dates[k], float(ar[k]), float(x[k]), float(v[k]))
                      for k in range(n)]
        self.today_row = [today]
        self.today_panel = [(f"t{k}", "2026-06-01", 0.01 * (k % 2), float(k), float(k))
                            for k in range(today_n)]

    def sql(self, q):
        if "trade_date = DATE" in q and "instrument_id = '" in q:
            return self.today_row                    # 오늘 셀 피처
        if "se.event_date = DATE" in q:
            return self.today_panel                  # 환원 검사 (오늘 횡단면)
        if "abs(z_" in q:
            return self.panel                        # 계열 방아쇠
        return self.panel                            # 점 방아쇠 과거 패널


T = _tuple(vuln_family="거래량", vuln_tr="수준")     # 측정 가능한 취약성


def test_inus_conditioning_and_apply_today():
    r = edge_test(_Lake(), T, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict == "성립" and r.p < 0.05
    assert r.n == 200                                # 취약성 조건화로 패널이 절반
    assert r.vuln_satisfied is True and r.applies_today   # 오늘 p높음 → 적용
    r2 = edge_test(_Lake(today=(1.0, -9.9)), T, "2026-06-01", cell_instrument_id="i0")
    assert r2.verdict == "성립" and r2.vuln_satisfied is False
    assert not r2.applies_today                      # 성립해도 오늘 미충족 = 부적용 (INUS)


def test_counterfactual_needs_positivity_and_reports_opposite_class():
    r = edge_test(_Lake(), T, "2026-06-01")
    assert "미충족 부류" in r.counterfactual         # 반대 사례 200 ≥ 5 → 반사실 쌍
    thin = _tuple(vuln_family="거래량", vuln_tr="수준", pct=0.001)   # 반대가 거의 없음
    r2 = edge_test(_Lake(), thin, "2026-06-01")
    assert "침묵" in r2.counterfactual or r2.counterfactual == ""


def test_series_trigger_panel_runs():
    t = _tuple(trigger=("계열", "가격잔차"), vuln_family="거래량", vuln_tr="수준")
    r = edge_test(_Lake(), t, "2026-06-01")
    assert r.verdict in ("성립", "불성립") and r.n == 200


def test_determinism_and_thin_panel():
    a = edge_test(_Lake(), T, "2026-06-01")
    b = edge_test(_Lake(), T, "2026-06-01")
    assert (a.p, a.n) == (b.p, b.n)                  # 같은 셀 재실행 = 같은 판정
    assert edge_test(_Lake(n=MIN_N - 1), T, "2026-06-01").verdict == "판정불가"


def test_unmeasurable_declared_not_silent():
    t = HypothesisTuple(vulnerabilities=(), trigger=Trigger("점", "X"), channel="R금리신용",
                        exposure=ExposureSource("속성", "신용", transform="수준"),
                        from_role="a", to_role="b", outcome="수익률", sign=-1)
    r = edge_test(_Lake(), t, "2026-06-01")
    assert r.verdict == "판정불가" and "못 잰다" in r.reason
    t2 = _tuple(trigger=("계열", "수급"))
    r2 = edge_test(_Lake(), t2, "2026-06-01")
    assert r2.verdict == "판정불가" and "혁신값" in r2.reason


def test_reduction_check_flags_today_misalignment():
    # 오늘 횡단면이 패널과 반대 방향 → 환원 불일치 → 부적용.
    lake = _Lake(today_n=10)
    # today_panel: ar 이 노출(k)과 무관하게 번갈아 - 방향은 계산상 음수가 되게 뒤집는다
    lake.today_panel = [(f"t{k}", "2026-06-01", -0.01 * (k >= 5), float(k), float(k))
                        for k in range(10)]
    r = edge_test(lake, T, "2026-06-01", cell_instrument_id="i0")
    assert r.reduction.startswith("불일치")
    assert not r.applies_today
