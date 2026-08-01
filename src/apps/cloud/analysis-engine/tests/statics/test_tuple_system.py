"""튜플 체계(가설·검정 에이전트)의 계약 — 감사 5라운드의 교훈이 전부 단언이다.

가설: 어휘 밖·접지 밖·채널 중복은 생성 시점에 죽고, 되물음은 사유를 싣는다.
검정: 표본은 튜플에서 유도되고, 부재는 판정불가+사유이며, 같은 입력은 같은
판정을 낸다(결정론). 용량-반응이 실재하면 성립, 없으면 불성립.
"""
import numpy as np
import pytest

from edge_analysis.statics.hypothesize import propose
from edge_analysis.statics.paneltest import edge_test
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
            # 날조 타입 + 어휘 밖 채널 + 채널 중복 → 유효 1개뿐 → 되물음
            return {"hypotheses": [_h(), _h(ident="EVT_지어냄"),
                                   _h(channel="새채널"), _h(channel="Q수량")]}
        return {"hypotheses": [_h(), _h(channel="FX환",
                                        ident="MARKET_STRUCTURE.INDEX.INCLUSION")]}

    valid, rejected = propose(ask, facts="사실", event_types=ETYPES)
    assert len(valid) == 2 and {t.channel for t in valid} == {"Q수량", "FX환"}
    assert any("날조" in r for r in rejected)          # 접지 밖 = 날조로 명명
    assert any("중복" in r for r in rejected)
    assert len(calls) == 2 and "거부 사유" in calls[1]  # 되물음이 사유를 싣는다


def test_propose_returns_empty_handed_rather_than_forcing():
    ask = lambda s, u: {"hypotheses": [_h(ident="없는타입")]}   # noqa: E731
    valid, rejected = propose(ask, facts="사실", event_types=ETYPES)
    assert valid == [] and rejected                    # 억지 가설보다 빈손

# ── 검정 에이전트 ────────────────────────────────────────────────────────
T = HypothesisTuple(
    vulnerabilities=(Vulnerability("수급", "누적", ">=", 0.9),),
    trigger=Trigger("점", "COMPANY.PRODUCT.LAUNCH"), channel="Q수량",
    exposure=ExposureSource("속성", "가격잔차", transform="누적"),
    from_role="ISSUER", to_role="ISSUER", outcome="수익률", sign=1)


class _Lake:
    """용량-반응이 실재하는 가짜 패널: ar = 0.02×(노출 상위) + 잡음."""

    def __init__(self, n=200, effect=0.02, seed=1):
        rng = np.random.default_rng(seed)
        x = rng.normal(size=n)
        hi = x >= np.quantile(x, 0.8)
        ar = effect * hi + rng.normal(scale=0.005, size=n)
        dates = [f"2026-0{1 + i % 5}-01" for i in range(n)]
        self._panel = [(f"i{k}", dates[k], float(ar[k]), float(x[k])) for k in range(n)]

    def sql(self, q):
        return [(1.0,)] if "me AS" in q else self._panel


def test_edge_test_passes_real_dose_response_and_is_deterministic():
    r1 = edge_test(_Lake(), T, "2026-06-01", cell_instrument_id="i0")
    r2 = edge_test(_Lake(), T, "2026-06-01", cell_instrument_id="i0")
    assert r1.verdict == "성립" and r1.p is not None and r1.p < 0.05
    assert (r1.p, r1.n) == (r2.p, r2.n)               # 같은 셀 재실행 = 같은 판정
    assert r1.today_exposure_pct is not None


def test_edge_test_rejects_flat_exposure_as_undetermined_not_false():
    r = edge_test(_Lake(effect=0.0), T, "2026-06-01")
    assert r.verdict == "불성립"                        # 효과 없음은 불성립이고
    thin = _Lake(n=MIN_N - 1)
    assert edge_test(thin, T, "2026-06-01").verdict == "판정불가"   # 표본 부족은 모름이다


def test_edge_test_declares_unmeasurable_exposure():
    t2 = HypothesisTuple(vulnerabilities=(), trigger=Trigger("점", "X"), channel="R금리신용",
                         exposure=ExposureSource("속성", "신용", transform="수준"),
                         from_role="a", to_role="b", outcome="수익률", sign=-1)
    r = edge_test(_Lake(), t2, "2026-06-01")
    assert r.verdict == "판정불가" and "못 잰다" in r.reason
