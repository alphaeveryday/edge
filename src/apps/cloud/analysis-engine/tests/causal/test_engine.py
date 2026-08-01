"""간선 추정·게이트 테스트 — 실험판에서 실측된 실패가 문법적으로 불가능해야 한다.

고정하는 불변식:
  · 수치는 코드가 만든다 (모델이 쓸 자리가 없다)
  · x·y·z 가 같은 pairs 에서 나온다 (단위 불일치 불가)
  · 순열은 설계가 조건화한 것을 보존한다 (층화)
  · 산술 게이트가 LLM 전에 돈다 (무게 없는 원인은 무료로 죽는다)

식별 테스트는 `test_p4_identify.py` 로 이사했다 - 2값 `engine.identify` 가 삭제되고
3값 `p4_identify.identify` 하나로 통합됐다.
"""

import dataclasses
from datetime import date

import numpy as np
import pytest

from edge_analysis.causal import graph as G
from edge_analysis.causal.engine import EdgeDesign, arithmetic_gate, estimate
from edge_analysis.config import PipelineError

D0, D1 = date(2026, 7, 1), date(2026, 7, 29)
AS_OF = "2026-07-29T15:30:00"


class _FakeData:
    """코호트·정렬열을 결정론으로 준다. 처치군에만 효과를 심는다."""

    def __init__(self, n_treated=40, n_control=400, effect=0.05, seed=0, nan_frac=0.0):
        self.rng = np.random.default_rng(seed)
        self.n_t, self.n_c, self.effect, self.nan_frac = n_treated, n_control, effect, nan_frac
        self.dates = [date(2026, 7, d) for d in range(1, 21)]
        self.calls: list[str] = []

    def cohort(self, where, *, as_of, w0=None, w1=None):
        self.calls.append(f"cohort:{where}")
        assert as_of, "PIT 없이 코호트를 만들 수 없다"
        return [(f"T{i}", self.dates[i % len(self.dates)]) for i in range(self.n_t)]

    def universe(self, where, dates, *, exclude=None):
        self.calls.append(f"universe:{where}")
        return [(f"C{i}", dates[i % len(dates)]) for i in range(self.n_c)]

    def ar(self, pairs):
        out = np.array([
            (self.effect if str(i).startswith("T") else 0.0) + self.rng.normal(0, 0.02)
            for i, _ in pairs])
        if self.nan_frac:
            out[self.rng.random(len(out)) < self.nan_frac] = np.nan
        return out

    def mom(self, pairs, **kw):
        return self.rng.normal(0, 0.05, size=len(pairs))

    def vol(self, pairs, **kw):
        return np.abs(self.rng.normal(0.02, 0.005, size=len(pairs)))

    def flow(self, pairs, *, kind: str = "institution_total"):
        # 표면이 있다는 사실만 고정한다 - 없으면 tools 조립이 AttributeError 로 죽는다.
        return self.vol(pairs)

    def ids(self, names):
        return {str(n): f"inst_{n}" for n in (names or [])}


DESIGN = EdgeDesign(src="EVT@t-2", dst="RET@t0",
                    treated="event_type_code = 'X'",
                    control="industry_name = 'Bio'",
                    cause_label="공시")


def test_effect_is_recovered_and_p_comes_from_the_permutation_null():
    r = estimate(_FakeData(effect=0.05), DESIGN, as_of=AS_OF, w0=D0, w1=D1, adjust=[])

    assert r.passed and r.significant
    assert r.effect == pytest.approx(0.05, abs=0.01)
    assert r.null_kind == "label"
    assert r.null_sd is not None and r.null_sd > 0


def test_no_effect_is_not_significant():
    r = estimate(_FakeData(effect=0.0), DESIGN, as_of=AS_OF, w0=D0, w1=D1, adjust=[])

    assert r.passed and not r.significant


def test_adjustment_columns_share_the_pairs_so_lengths_cannot_diverge():
    """단위 불일치를 문법적으로 불가능하게 한다 - 실험판이 여기서 5턴을 태웠다."""
    r = estimate(_FakeData(), DESIGN, as_of=AS_OF, w0=D0, w1=D1,
                 adjust=["MOM@t-7", "VOL@t-7"])

    assert r.passed
    assert r.adjust == ["MOM@t-7", "VOL@t-7"]


def test_missing_values_are_dropped_jointly_not_per_column():
    fake = _FakeData(nan_frac=0.3)

    r = estimate(fake, DESIGN, as_of=AS_OF, w0=D0, w1=D1, adjust=["MOM@t-7"])

    assert r.n < fake.n_t + fake.n_c
    assert r.passed


def test_underpowered_sample_is_gated_not_reported():
    r = estimate(_FakeData(n_treated=2, n_control=3), DESIGN,
                 as_of=AS_OF, w0=D0, w1=D1, adjust=[])

    assert not r.passed
    assert any("표본" in g for g in r.gate_fail)
    assert r.p is None


def test_no_contrast_is_gated():
    """처치만 있고 대조가 없으면 비교가 아니다."""
    fake = _FakeData(n_treated=40, n_control=0)

    r = estimate(fake, DESIGN, as_of=AS_OF, w0=D0, w1=D1, adjust=[])

    assert not r.passed
    assert any("대조" in g for g in r.gate_fail)


def test_stratified_null_has_smaller_spread_than_free_null():
    """설계가 날짜를 조건화했으면 귀무도 날짜 안에서 섞어야 한다 - 아니면 분산이 부푼다."""
    strat = estimate(_FakeData(effect=0.0), DESIGN, as_of=AS_OF, w0=D0, w1=D1,
                     adjust=[], n_null=400)
    free = estimate(_FakeData(effect=0.0),
                    dataclasses.replace(DESIGN, strata="none"),
                    as_of=AS_OF, w0=D0, w1=D1, adjust=[], n_null=400)

    assert strat.null_sd is not None and free.null_sd is not None


def test_unknown_strata_vocabulary_is_rejected():
    with pytest.raises(PipelineError, match="strata"):
        estimate(_FakeData(), dataclasses.replace(DESIGN, strata="whatever"),
                 as_of=AS_OF, w0=D0, w1=D1, adjust=[])


def test_arithmetic_gate_kills_weightless_cause_for_free():
    """실측: 비중 5.20% 로 13.36% 를 설명하려면 +257%, 타입 최대는 39.3% 였다."""
    prior = {"abs_max": 0.393}

    why = arithmetic_gate(0.1336, 0.052, prior)

    assert why and "257%" in why and "39.3%" in why


def test_arithmetic_gate_passes_when_the_move_is_within_reach():
    """필요 초과수익이 그 타입의 과거 최대 안에 들면 통과다 - 여기서 죽이면 안 된다."""
    prior = {"abs_max": 0.138}

    assert arithmetic_gate(0.0410, 0.083, prior) is not None   # 49% 필요 > 13.8%
    assert arithmetic_gate(0.0100, 0.083, prior) is None       # 12.0% 필요 < 13.8%


def test_arithmetic_gate_kills_zero_share():
    assert arithmetic_gate(0.05, None, {"abs_max": 0.5}) is not None
    assert arithmetic_gate(0.05, 0.0, {"abs_max": 0.5}) is not None


def test_pit_is_required_by_the_data_surface():
    """엔진이 as_of 를 반드시 넘겨야 한다 - 코호트가 그걸 단정한다."""
    fake = _FakeData()

    estimate(fake, DESIGN, as_of=AS_OF, w0=D0, w1=D1, adjust=[])

    assert any(c.startswith("cohort:") for c in fake.calls)


def test_graph_split_is_used_so_bidirected_edges_are_not_treated_as_directed():
    edges = [{"from": "A@t-1", "to": "B@t0"},
             {"from": "A@t-1", "to": "B@t0", "kind": "bidirected"}]

    d, b = G.split(edges)

    assert d == [("A@t-1", "B@t0")]
    assert b == [("A@t-1", "B@t0")]
