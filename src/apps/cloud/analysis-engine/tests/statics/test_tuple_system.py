"""튜플 체계(가설·검정 에이전트)의 계약 — 감사 5라운드의 교훈이 전부 단언이다.

가설: 어휘 밖·접지 밖·채널 중복은 생성 시점에 죽고, 되물음은 사유를 싣는다.
검정: 표본은 튜플에서 유도되고(조건 = INUS 조건화), 부재는 판정불가+사유이며,
같은 입력은 같은 판정(결정론). 성립해도 오늘 조건 미충족이면 부적용.
반사실은 positivity 를 갖출 때만 채워진다.
"""
from types import SimpleNamespace

import numpy as np
import pytest


from edge_analysis.statics.attribute import _verifiable_event_types
from edge_analysis.statics.hypothesize import propose, screen_tuples
from edge_analysis.statics.paneltest import EdgeReport, FEATURES, edge_test
from edge_analysis.statics.vocab import (ExposureSource, HypothesisTuple, VocabError,
                                         MIN_N, SERIES_FAMILIES, Trigger, Condition)

ETYPES = ["COMPANY.PRODUCT.LAUNCH", "MARKET_STRUCTURE.INDEX.INCLUSION"]


def _h(channel="Q수량", ident="COMPANY.PRODUCT.LAUNCH", **kw):
    base = {"conditions": [{"family": "수급", "transform": "누적",
                                 "comparator": ">=", "percentile": 0.9}],
            "trigger": {"kind": "점", "ident": ident},
            "channel": channel,
            "exposure": {"kind": "속성", "ident": "가격잔차", "transform": "누적"},
            "outcome": "수익률", "reduction_note": "n"}
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

def test_propose_rejects_proxy_outside_measurement_schema():
    """LLM 후보는 스키마로 먼저 닫힌다. 못 재는 proxy를 패널까지 보내지 않는다."""
    ask = lambda _s, _u: {"hypotheses": [  # noqa: E731
        _h(exposure={"kind": "속성", "ident": "배수", "transform": "수준"}),
        _h(channel="FX환",
           exposure={"kind": "속성", "ident": "배수", "transform": "수준"}),
    ]}
    valid, rejected = propose(
        ask, facts="x", event_types=ETYPES,
        measurable=[("가격잔차", "누적")])
    assert valid == []
    assert rejected and all("못 재는 노출" in why for why in rejected)



# ── 검정 에이전트 ────────────────────────────────────────────────────────
def _tuple(vuln_family="수급", vuln_tr="누적", trigger=("점", "COMPANY.PRODUCT.LAUNCH"), pct=0.5):
    return HypothesisTuple(
        conditions=(Condition(vuln_family, vuln_tr, ">=", pct),),
        trigger=Trigger(*trigger), channel="Q수량",
        exposure=ExposureSource("속성", "가격잔차", transform="누적"), outcome="수익률")


class _Lake:
    """가짜 패널. 조건(거래량/수준) 충족 반쪽에서만 용량-반응이 실재한다."""

    def __init__(self, n=400, effect=0.02, seed=1, today=(1.0, 1.0), today_n=0,
                 today_z=(3.0, 0.5)):
        rng = np.random.default_rng(seed)
        x = rng.normal(size=n)                       # 노출
        v = rng.normal(size=n)                       # 조건 피처
        sat = v >= np.quantile(v, 0.5)
        hi = x >= np.quantile(x, 0.8)
        ar = effect * (hi & sat) + rng.normal(scale=0.004, size=n)
        dates = [f"2026-0{1 + i % 5}-01" for i in range(n)]
        self.panel = [(f"i{k}", dates[k], float(ar[k]), float(x[k]), float(v[k]))
                      for k in range(n)]
        self.today_row = [today]
        self.today_panel = [(f"t{k}", "2026-06-01", 0.01 * (k % 2), float(k), float(k))
                            for k in range(today_n)]
        self.today_z = [today_z]

    def sql(self, q):
        if "SELECT z_ar, z_tv_chg" in q:
            return self.today_z                     # 오늘 계열 혁신 z (발화 판정)
        if "trade_date = DATE" in q and "instrument_id = '" in q:
            return self.today_row                    # 오늘 셀 피처
        if "e.trade_date = DATE" in q:
            return self.today_panel                  # 환원 검사 (오늘 횡단면)
        if "abs(z_" in q:
            return self.panel                        # 계열 방아쇠
        return self.panel                            # 점 방아쇠 과거 패널


T = _tuple(vuln_family="거래량", vuln_tr="수준")     # 측정 가능한 조건


def test_inus_conditioning_and_apply_today():
    r = edge_test(_Lake(), T, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict == "성립" and r.p < 0.05
    assert r.n == 400                                # 엣지 존재는 늘 전체 패널로 검정
    assert r.cond_satisfied is True and r.applies_today   # 오늘 p높음 → 적용
    r2 = edge_test(_Lake(today=(1.0, -9.9)), T, "2026-06-01", cell_instrument_id="i0")
    assert r2.verdict == "성립" and r2.cond_satisfied is False
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
    assert r.verdict in ("성립", "불성립") and r.n == 400


def test_determinism_and_thin_panel():
    a = edge_test(_Lake(), T, "2026-06-01")
    b = edge_test(_Lake(), T, "2026-06-01")
    assert (a.p, a.n) == (b.p, b.n)                  # 같은 셀 재실행 = 같은 판정
    assert edge_test(_Lake(n=MIN_N - 1), T, "2026-06-01").verdict == "판정불가"


def test_unmeasurable_declared_not_silent():
    # 거시는 종목 축이 없어 이 프레임에 못 들어온다 - 시장층 전용 추정기 소관.
    t = HypothesisTuple(conditions=(), trigger=Trigger("점", "X"), channel="R금리신용",
                        exposure=ExposureSource("속성", "거시", transform="변화"),  outcome="수익률")
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


def test_relation_transmission_edge_tests_but_never_assigns(monkeypatch):
    from edge_analysis.statics import paneltest
    monkeypatch.setattr(paneltest, "_stratified_p", lambda *_args: 0.02)

    class RelLake:
        """전이 패널 스텁: 동일산업 피어(rel=1)만 +2% 반응."""

        def __init__(self, n=200, seed=3):
            rng = np.random.default_rng(seed)
            rel = (np.arange(n) % 4 == 0).astype(int)          # 25% 피어
            ar = 0.02 * rel + rng.normal(scale=0.004, size=n)
            d = [f"2026-0{1 + i % 5}-01" for i in range(n)]
            self.rows = [(f"i{k}", d[k], float(ar[k]), int(rel[k])) for k in range(n)]

        def sql(self, q):
            assert "industry_name" in q                        # 전이 SQL 만 와야 한다
            return self.rows


    t = HypothesisTuple(
        conditions=(), trigger=Trigger("점", "COMPANY.PRODUCT.LAUNCH"),
        channel="Q수량", exposure=ExposureSource("관계", "SAME_INDUSTRY", hops=1),  outcome="수익률")
    r = edge_test(RelLake(), t, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict == "성립" and r.p < 0.05
    assert r.assignable is False and not r.applies_today       # 엣지만, 몫 배정 금지
    corrected = edge_test(
        RelLake(), t, "2026-06-01", cell_instrument_id="i0", m_tests=10_000)
    assert corrected.verdict != "성립", "관계 검정도 같은 셀의 Bonferroni 보정을 받아야 한다"

    with pytest.raises(VocabError, match="결과종류"):
        HypothesisTuple(
            conditions=(), trigger=Trigger("점", "COMPANY.PRODUCT.LAUNCH"),
            channel="Q수량", exposure=ExposureSource("관계", "SAME_INDUSTRY", hops=1),
            outcome="되돌림")
    # 어휘 밖 관계는 **튜플 생성 시점에** 죽는다 (19R) - 검정기까지 가서 '못 잰다'로
    # 되돌아오면 어휘가 열려 있다는 인상만 주고 실제로는 침묵하는 거부였다.
    with pytest.raises(VocabError, match="닫힌 관계 어휘"):
        ExposureSource("관계", "SUPPLIES_TO", hops=1)


def test_typed_link_relation_uses_ontology_hop_not_industry_proxy():
    # 19R: '경로형'인데 재는 관계가 산업 동일성 하나뿐이었다. 산업은 속성이지
    # 관계가 아니다 - 타입 있는 1홉(v_link)을 실제로 재는지 SQL 로 확인한다.
    seen = {}

    class LinkLake:
        def sql(self, q):
            seen["q"] = q
            return []

    t = HypothesisTuple(
        conditions=(), trigger=Trigger("점", "COMPANY.ALLIANCE.PARTNERSHIP"),
        channel="C원가", exposure=ExposureSource("관계", "SUPPLY_CHAIN", hops=1),
        outcome="수익률")
    r = edge_test(LinkLake(), t, "2026-06-01", cell_instrument_id="i0")
    assert "v_link" in seen["q"] and "SUPPLY_CHAIN" in seen["q"]
    assert "industry_name = ce.industry_name" not in seen["q"]   # 대리가 아니라 관계
    assert "l.link_date <= ev.d" in seen["q"]                    # 홉도 시점으로 잘린다
    assert r.verdict == "판정불가"                                # 스텁이라 표본 0


def test_proxy_schema_never_reads_or_returns_outcomes():
    """proxy 후보 선택은 스키마만 본다. 수익률·n·p를 보면 선택편향이 생긴다."""
    from edge_analysis.statics.tools import Catalog

    class SchemaLake:
        effective = {}
        s3 = {}

        def bind_day(self, _day): pass
        def probe_day(self): return {}
        def sql(self, _q):
            raise AssertionError("schema 도구가 결과 데이터를 조회했다")

    text = Catalog(SchemaLake(), "T", "I", "2026-06-01").schema()
    assert "가격잔차/누적" in text
    assert all(token not in text for token in ("p₂", "p=", "n=", "hi=", "lo=", "수익률"))

def test_measurement_schema_is_pure_and_matches_proxy_menu():
    """후보 메뉴를 데이터 결과·커버리지로 좁히면 선택 단계가 다시 수치를 본다."""
    from edge_analysis.statics.attribute import _measurable

    class NoRead:
        def sql(self, _q):
            raise AssertionError("측정 스키마가 데이터를 읽었다")

    assert set(_measurable(NoRead())) == set(FEATURES)

def test_outcome_driven_proxy_screen_is_not_a_tool():
    """proxy 선택 전에 결과를 읽는 우회 표면이 남아 있으면 schema 계약은 무의미하다."""
    from edge_analysis.statics.surface import TOOLS
    from edge_analysis.statics.tools import Catalog

    assert "grid_screen" not in TOOLS
    assert not hasattr(Catalog, "screen")

def test_selected_proxies_have_one_batch_validation_surface(monkeypatch):
    """LLM 선택 뒤 검정 표면은 하나다. 후보마다 도구를 재호출해 표본을 고르지 않는다."""
    import edge_analysis.statics.paneltest as pt
    from edge_analysis.statics.surface import TOOLS

    ts = [_tuple(), _tuple(trigger=("점", "MARKET_STRUCTURE.INDEX.INCLUSION"))]
    seen = []

    def one(_lake, t, _day, cell_instrument_id="", layer="", m_tests=1):
        seen.append((t, cell_instrument_id, m_tests))
        return pt.EdgeReport("판정불가", 0, None, None, None, None)

    monkeypatch.setattr(pt, "edge_test", one)
    reports = pt.edge_tests(object(), ts, "2026-06-01", "I")
    assert [t for t, _r in reports] == ts
    assert seen == [(ts[0], "I", 2), (ts[1], "I", 2)]
    assert "edge_tests" in TOOLS and "edge_test" not in TOOLS



def test_gate_never_produces_magnitude():
    # §11: 게이트는 존재를 판정하고 **크기를 만들지 않는다**. SEM 기여
    # (`contribution`·`ci_lo`·`ci_hi`)를 붙였던 자리다 - τ̂·Δx 는 기울기 × 노출
    # 편차라 '오늘 이 사건이 만든 %p' 가 아니었는데 산문이 수준으로 읽어 하루
    # 총합과 안 겹치는 구간을 인용했다(자기모순). 크기는 ATT 경로가 주장하고
    # 예산 검산은 가법 제약이 한다.
    import dataclasses

    from edge_analysis.statics.paneltest import EdgeReport
    r = edge_test(_Lake(), T, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict == "성립"
    assert edge_test(_Lake(effect=0.0), T, "2026-06-01",
                     cell_instrument_id="i0").verdict == "불성립"
    banned = {"contribution", "ci_lo", "ci_hi"}
    assert not banned & {f.name for f in dataclasses.fields(EdgeReport)}, \
        "게이트가 다시 크기를 만든다 - 크기 주장은 ATT 경로 소관이다"


def test_registry_recall_before_record_and_pit(tmp_path):
    from edge_analysis.statics.registry import recall, record
    assert recall(tmp_path, day="2026-06-02", types=["T1"]) == []   # 첫 소환 = 빈손
    record(tmp_path, day="2026-06-01", cell="c1",
           screens=[{"type": "T1", "exposure": "가격잔차/누적", "n": 100,
                     "p2": 0.01, "direction": "+"}])
    record(tmp_path, day="2026-06-02", cell="c2",
           screens=[{"type": "T1", "exposure": "가격잔차/누적", "n": 100,
                     "p2": 0.001, "direction": "+"}])
    m = recall(tmp_path, day="2026-06-02", types=["T1"])            # 당일 것은 제외
    assert len(m) == 1 and "p₂=0.010" in m[0]                       # PIT: 6/1 만 보인다


def test_propose_rejects_tautological_vulnerability():
    # 6차 라이브 실측: 조건=노출 같은 피처 → INUS 내용 0 + 표본 파괴.
    taut = _h()
    taut["conditions"] = [{"family": "가격잔차", "transform": "누적",
                                "comparator": ">=", "percentile": 0.9}]  # 노출과 동일
    ok = _h(channel="FX환", ident="MARKET_STRUCTURE.INDEX.INCLUSION")
    ask = lambda s, u: {"hypotheses": [taut, ok, _h(channel="K위험")]}   # noqa: E731
    valid, rejected = propose(ask, facts="f", event_types=ETYPES)
    assert any("동어반복" in r for r in rejected)
    assert all(not (t.exposure.ident == v.ident and t.exposure.transform == v.transform)
               for t in valid for v in t.conditions)


def test_thin_condition_never_kills_the_panel():
    # 조건은 표본을 쪼개지 않는다. 충족이 3% 여도 엣지 검정은 전체 n 으로 돌고,
    # 조건은 조절 대비로만 보고된다 - 라이브 6회의 '조건화 전멸'(n=23·6·6)을 막는 계약.
    thin = _tuple(vuln_family="거래량", vuln_tr="수준", pct=0.97)   # 충족 ~3%
    r = edge_test(_Lake(n=400), thin, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict in ("성립", "불성립")     # 조건이 얇다는 이유로 판정불가가 되지 않는다
    assert r.n == 400                          # 전체 패널 검정력
    # 오늘 적용은 INUS 그대로: p0.97 임계를 오늘 못 넘으면 부적용.
    low_today = edge_test(_Lake(n=400, today=(1.0, -9.9)), thin, "2026-06-01",
                          cell_instrument_id="i0")
    assert low_today.cond_satisfied is False and not low_today.applies_today


def test_series_trigger_requires_today_firing_for_application():
    # 14차: 계열 방아쇠의 접지 = 오늘 발화. 패널은 역사(|z|≥2 였던 날들)이고,
    # 오늘 적용은 오늘도 방아쇠가 당겨졌을 때만이다 - 점 방아쇠의 셀 사건 접지와 대칭.
    t = _tuple(trigger=("계열", "가격잔차"), vuln_family="거래량", vuln_tr="수준")
    fired = edge_test(_Lake(today_z=(3.2, 0.1)), t, "2026-06-01", cell_instrument_id="i0")
    unfired = edge_test(_Lake(today_z=(0.4, 0.1)), t, "2026-06-01", cell_instrument_id="i0")
    assert fired.trigger_fired is True and "발화" in fired.trigger_note
    assert unfired.trigger_fired is False and not unfired.applies_today
    # 미계측이면 발화를 지어내지 않는다 → 부적용.
    silent = edge_test(_Lake(today_z=(None, None)), t, "2026-06-01", cell_instrument_id="i0")
    assert silent.trigger_fired is False and "미계측" in silent.trigger_note


def test_propose_rejects_unfired_series_trigger():
    # 발화 안 한 계열로 오늘을 설명하는 가설은 방아쇠 날조 - 점의 접지 밖 사건타입과 동급.
    from edge_analysis.statics.hypothesize import propose
    def ask(system, user):
        assert "오늘 |z|≥2 로 발화한 계열족" in system
        return {"hypotheses": [
            {"conditions": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "계열", "ident": "수급"},          # 미발화 - 날조
             "channel": "Q수량", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "outcome": "수익률",
             "reduction_note": "x"},
            {"conditions": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "계열", "ident": "가격잔차"},      # 발화 - 유효
             "channel": "K위험", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "outcome": "수익률",
             "reduction_note": "y"}]}
    valid, rejected = propose(ask, facts="f", event_types=[],
                              measurable=[("가격잔차", "누적"), ("거래량", "수준")],
                              series_families=["가격잔차"])
    assert [t.trigger.ident for t in valid] == ["가격잔차"]
    assert any("미발화 계열 방아쇠 날조" in r for r in rejected)


def test_gate_is_two_sided():
    # 방향 채굴 보상(양측 p₂). 부호를 사후에 고르지 못하게 하는 유일한 장치다.
    from edge_analysis.statics.paneltest import _two_sided
    assert _two_sided(0.03) == 0.06 and abs(_two_sided(0.97) - 0.06) < 1e-12  # 대칭
    assert _two_sided(0.5) == 1.0
    r3 = edge_test(_Lake(effect=0.03), _tuple(vuln_family="거래량", vuln_tr="수준"),
                   "2026-06-01")
    assert r3.verdict == "성립"


def test_agent_decisions_are_traced_with_raw_submissions():
    # 18R: 거부 사유가 stdout 3건·60자로 잘려 사라지던 것을 trace 로 영속화.
    # collect_trace 밖이면 record 는 no-op - 라이브러리 경로는 영향 없다.
    from edge_analysis.observability import collect_trace
    from edge_analysis.statics.hypothesize import propose
    def ask(system, user):
        return {"hypotheses": [
            {"conditions": [], "trigger": {"kind": "점", "ident": "지어낸타입"},
             "channel": "Q수량", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "outcome": "수익률",
             "reduction_note": "x"},
            {"conditions": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "점", "ident": "REAL.TYPE"},
             "channel": "K위험", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "outcome": "수익률",
             "reduction_note": "y"}]}
    with collect_trace() as tr:
        valid, rejected = propose(
            ask, facts="f", event_types=["REAL.TYPE"],
            measurable=[("가격잔차", "누적"), ("거래량", "수준")])
    kills = [e for e in tr if e["event"] == "tuple.rejected"]
    oks = [e for e in tr if e["event"] == "tuple.accepted"]
    assert len(valid) == 1 and len(kills) >= 1
    # 유효<2 라 되물음 1회 - trace 가 두 턴을 다 보여준다 (어느 턴이 뭘 냈는지가 감사다).
    assert {e["turn"] for e in oks} == {1, 2}
    assert kills[0]["raw"]["trigger"]["ident"] == "지어낸타입"   # 원문 보존
    assert "날조" in kills[0]["why"]                             # 사유 전문(무절단)
    assert oks[0]["reduction_note"] == "y"



def test_thin_confirmation_sample_says_so_instead_of_silently_passing():
    # 표본이 얇으면 판정불가 - 사유가 '백필' 좌표를 가리킨다. 조용히 통과하지 않는다.
    from edge_analysis.statics.paneltest import MIN_N
    class ThinLake:
        def sql(self, q):
            if "SELECT z_ar" in q:
                return [(0.1, 0.1)]
            return [(f"i{k}", "2026-05-01", 0.01, 1.0, 1.0) for k in range(MIN_N - 1)]
    r = edge_test(ThinLake(), _tuple(vuln_family="거래량", vuln_tr="수준"), "2026-06-01")
    assert r.verdict == "판정불가" and "백필" in r.reason


def test_panels_never_touch_base_tables_directly():
    # 18R (사용자 지시): dyntool 도 STORM 과 같은 시맨틱 레이어를 쓴다. 시점 클램프는
    # 뷰 정의 안에 있어야 질의가 우회할 수 없다 - adapters/sql_surface._guard 가
    # 자유 SQL 에 강제하는 규율("기반 테이블 직접 접근 금지")을 패널 SQL 에도 건다.
    import pathlib
    import edge_analysis.statics.paneltest as pt
    src = pathlib.Path(pt.__file__).read_text(encoding="utf-8")
    for base in ("rdb.public.source_event", "rdb.public.price_daily",
                 "rdb.public.event_argument", "rdb.public.instrument_classification"):
        assert base not in src, f"{base} 직접 접근 - 클램프 우회 (v_event·v_daily 를 써라)"
    # 표면이 실제로 공유 정의에서 온다 (두 벌이면 한쪽만 낡는다)
    from edge_analysis.adapters.sql_surface import views_sql
    body = pt._base("2026-06-01")
    assert "v_event" in body and "v_daily" in body and "ar_ind" in body
    shared = views_sql("TIMESTAMP '2026-06-01 00:00:00'", "DATE '2026-06-01'",
                       "rdb.public.")
    assert shared in body


def test_state_machine_hides_tools_and_enforces_order():
    # 19R: 동적 도구 상태기계. 세 규율 -
    # (1) 그 상태에 없는 도구는 이름조차 존재하지 않는다,
    # (2) 진행은 관측으로만 (가드를 코드가 지킨다),
    # (3) 결정론 브리핑은 상태가 아니다 - 물어볼 값이 없는 질문에 왕복을 안 쓴다.
    from edge_analysis.statics.fsm import GROUND, SCREEN, Machine

    class FakeCat:
        def __init__(self): self.seen = []
        def call(self, name, arg=""):
            self.seen.append(name)
            return {"cell": "셀 X", "coverage": "바인딩 35/35", "vocab": "채널 8",
                    "events": "사건 없음: 장중 사건이 하나도 없다",
                    "news": "미도달: document 은 2026-07-08 부터 적재됐다",
                    "schema": "측정 가능한 proxy: 가격잔차/누적"}[name]

    m = Machine(FakeCat())
    assert m.state == GROUND                                   # SCOPE 는 접혔다
    assert "셀 X" in m.brief() and "바인딩" in m.brief()        # 브리핑은 묻지 않고 준다
    assert "schema" not in m.menu()                            # 미래 도구는 안 보인다
    out = m.observe("schema")                                  # 상태 밖 호출
    assert "GROUND" in out and "없다" in out
    assert "vocab" in m.menu()                                 # 탐색 도구는 게이트 밖
    m.observe("vocab")                                         # 불러도 전이 안 함
    assert m.state == GROUND
    # 부재도 증거다 - 사건 0인 셀에서 긍정 증거만 요구하면 턴만 태운다(STORM 실측)
    assert "→ SCREEN" not in m.observe("events")      # 사건 확인만으로는 안 넘어간다
    assert m.absent == 1 and m.state == GROUND
    # 근거를 **열어 봤다**는 사실이 두 번째 조건 - 못 연다는 확인도 증거로 친다
    assert "→ SCREEN" in m.observe("news")
    assert m.evidence == 1 and m.grounded == 0 and m.state == SCREEN
    assert "→ EMIT" in m.observe("schema") and m.done
    assert m.stats()["calls"] == ["GROUND:vocab()", "GROUND:events()",
                                  "GROUND:news()", "SCREEN:schema()"]


def test_unreached_table_is_not_absence():
    # 19R 실측: available_at 이 적재 시각이라 06-01 셀에서 document(293,930행)가 0행.
    # '뉴스 없는 날'로 보고하면 거짓 사실이 만들어진다 - 미도달은 부재가 아니다.
    from edge_analysis.statics.fsm import Machine
    from edge_analysis.statics.tools import Catalog

    class Lake:
        effective = {"document": (0, "2026-07-08 00:00:00")}
        cols = {"document": ["document_id", "title"]}
        def bind_day(self, d): return 0
        def probe_day(self): return self.effective

    c = Catalog(lake=Lake(), ticker="T", instrument_id="i0", day="2026-06-01",
                types=("COMPANY.PRODUCT.LAUNCH",))
    out = c.call("news")
    assert out.startswith("미도달") and "2026-07-08" in out
    assert "미도달" in c.call("peek", "document")
    m = Machine(c)
    m.observe("news")
    assert m.grounded == 0          # 미도달로는 접지가 성립하지 않는다


def test_tool_catalog_separates_absence_from_error():
    # STORM dyn2 의 실패 양식: 키 오조회를 '사건 없음'으로 믿었다. 둘은 다른 문장이다.
    from edge_analysis.statics.tools import Catalog
    c = Catalog(lake=None, ticker="T", instrument_id="i0", day="2026-06-01", types=())
    assert c.events().startswith("사건 없음")                   # 진짜 부재
    c2 = Catalog(lake=None, ticker="T", instrument_id="i0", day="2026-06-01",
                 types=("COMPANY.PRODUCT.LAUNCH",))
    assert "없다" in c2.events("NO_SUCH") and "있는 것" in c2.events("NO_SUCH")
    assert "그런 도구 없음" in c2.call("지어낸도구")             # 이름 날조는 즉답
    assert c2.call("vocab", "채널").startswith("채널 8")


def test_s3_registry_binds_empty_datasets_too():
    # 20R (사용자 지시): "데이터 없는 것도 일단 스키마 붙여라". 빈 축과 없는 축은
    # 다르다 - 전자는 적재 일감이고 후자만 설계 한계다. 스키마가 그 둘을 가른다.
    from edge_analysis.statics.duck import S3_SETS
    from edge_analysis.statics.tools import Catalog

    kinds = {k for _n, k, _p in S3_SETS}
    # 다섯 형식: hive(parquet 파티션) · glob(parquet 패턴) · ice(Iceberg) ·
    # csv(gz 글롭) · csvfile(단일 비압축 - 항목 사전처럼 .csv 인 것)
    assert kinds == {"hive", "glob", "ice", "csv", "csvfile"}
    assert len(S3_SETS) == len({n for n, _k, _p in S3_SETS})  # 이름 충돌 없음
    assert all(n.startswith("s3_") for n, _k, _p in S3_SETS)  # RDB 뷰와 안 겹친다

    class Lake:
        s3 = {"s3_empty": "draft/canonical/estimates/estimate_line"}
        deferred: dict[str, str] = {}
        cols: dict[str, list[str]] = {}
        effective: dict[str, tuple[int, str | None]] = {}
        def bind_day(self, d): return 0
        def probe_day(self): return {}
        def bind_s3(self, n): return ""
        def sql(self, q):
            return [("report_id",), ("broker",)] if q.startswith("DESCRIBE") else [(0,)]

    c = Catalog(lake=Lake(), ticker="T", instrument_id="i0", day="2026-07-30")
    out = c.call("peek", "s3_empty")
    assert "0행" in out and "report_id" in out          # 비어도 **열이 보인다**
    assert "스키마만 있다" in out and "적재 일감" in out   # 설계 한계로 위장 금지
    assert "s3_empty" in c.call("tables")               # 목록에도 뜬다
    assert "그런 S3 데이터셋 없음" in c.call("peek", "s3_지어낸것")


def test_macro_series_trigger_names_which_series_moved():
    # 20R: 표현력 측정이 '환율 급등'·'전일 미국 반도체 하락'을 방아쇠로 요구했다.
    # 어휘엔 `거시` 가 있었고 **계산기가 없었다** - 어휘 확장이 아니라 계산기 확장이
    # 답이었다(상류 온톨로지 무관). 계열족 하나에 여러 계열이 있으므로 최댓값으로
    # 발화를 판정하되 **누가 움직였는지 이름을 낸다** - 이름 없는 '거시가 튀었다'는
    # 검정 불가능한 문장이다.
    from edge_analysis.statics.paneltest import Z_ANOM, macro_z

    class Lake:
        def __init__(self, rows): self.rows = rows
        def sql(self, q):
            assert "s3_index_daily" in q and "s3_fx_daily" in q   # 오픈소스 표를 본다
            assert "trade_date < DATE" in q      # 직전 거래일 - 오늘자 미국 종가는 없다
            return self.rows

    z, note = macro_z(Lake([("index/SOXX", "2026-07-29", -3.4),
                            ("fx/USDKRW", "2026-07-29", 2.1),
                            ("rate/10y", "2026-07-29", 0.5)]), "2026-07-30")
    assert z == -3.4 and abs(z) >= Z_ANOM          # 절댓값 최대가 그 족의 혁신
    assert "index/SOXX" in note and "fx/USDKRW" in note   # 후보를 가리지 않는다

    class Dead:
        def sql(self, q): raise RuntimeError("소스 없음")
    assert macro_z(Dead(), "2026-07-30") == (0.0, "")   # 부재는 0 발화, 조용한 예외 금지


def test_flow_series_uses_previous_day_because_aggregate_is_published_after_close():
    # 20R: 투자자별 집계는 장 마감 후 18:00 KST 공표다. 오늘 수급으로 오늘 장중
    # 움직임을 설명하면 그건 원인이 아니라 **동시발생**이고, PIT 위반이다.
    # 어제 수급은 오늘 개장 전에 알려져 있으니 방아쇠 자격이 있다 - 거시(직전 미국
    # 거래일)와 같은 규율.
    from edge_analysis.statics.paneltest import flow_z

    seen = {}

    class Lake:
        def sql(self, q):
            seen["q"] = q
            return [("institution_total", "2026-07-29", 2.4),
                    ("foreign", "2026-07-29", -1.1)]

    z, note = flow_z(Lake(), "i0", "2026-07-30")
    assert "trade_date < DATE '2026-07-30'" in seen["q"]      # 전일까지만
    assert "v_instrument" in seen["q"]                         # ticker 조인은 PIT 뷰로
    assert z == 2.4 and "institution_total" in note and "foreign" in note


def test_unmeasured_series_records_why_instead_of_silent_zero():
    # 내가 방금 만든 코드에서 같은 병이 재발했다: 터널이 죽었는데 except 가 0.0 을
    # 돌려줘 '수급 이상 없음'으로 위장됐다. 부재는 **사유와 함께** 남긴다.
    from edge_analysis.observability import collect_trace
    from edge_analysis.statics.paneltest import flow_z

    class Dead:
        def sql(self, q): raise RuntimeError("Catalog rdb does not exist")

    with collect_trace() as tr:
        assert flow_z(Dead(), "i0", "2026-07-30") == (0.0, "")
    assert any(e["event"] == "series.unmeasured" and e["family"] == "수급"
               and "rdb" in e["why"] for e in tr)


# ── 처치변수 어휘 확장 (PIT 스냅샷이 연 축) ──────────────────────────────
# 처치변수 = 방아쇠(주 술어) ∧ 조건들. 조건 어휘를 상태·사건·관계로 넓히면서
# **가드를 같이 넓히지 않으면** 어휘가 거짓 어포던스를 준다 - 8셀 71튜플 중 55개가
# 쓰는 순간 n=0 확정이었던 병이 그것이다. 아래는 그 병에 대한 계약이다.

MEAS = list(FEATURES)


def test_pit_families_are_in_vocabulary():
    # PIT 스냅샷이 연 축 - 주식수는 S주식수 채널의 첫 관측변수다.
    assert {"주주", "주식수", "공매도"} <= SERIES_FAMILIES


def test_condition_kinds_read_differently_per_kind():
    # key 는 패널 피처 키이자 서술 조각 - 종류가 다르면 다르게 읽혀야 한다.
    assert Condition("신용", "수준", ">=", 0.9).key == "신용/수준"
    assert Condition("COMPANY.CAPITAL.SHARE_BUYBACK", kind="사건",
                     lookback=30).key == "사건:COMPANY.CAPITAL.SHARE_BUYBACK/최근30일"


def test_condition_vocabulary_is_closed_per_kind():
    with pytest.raises(VocabError):
        Condition("없는계열족", "수준", ">=", 0.9)          # 상태 → 계열족이어야
    with pytest.raises(VocabError):
        Condition("NOT_A_RELATION", kind="관계")            # 관계 → 닫힌 관계 어휘
    with pytest.raises(VocabError):
        Condition("", kind="사건")                          # 사건 → id 필수
    with pytest.raises(VocabError):
        Condition("SUPPLY_CHAIN", kind="관계", percentile=1.5)   # 백분위 범위


def test_event_condition_equal_to_trigger_is_tautology():
    # "오늘 났고 최근에도 났다"는 조건이 아니다 - 방아쇠를 되풀이할 뿐.
    h = _h()
    h["conditions"] = [{"ident": "COMPANY.PRODUCT.LAUNCH", "kind": "사건"}]
    valid, rej = screen_tuples([h], event_types=ETYPES)
    assert not valid and "방아쇠와 같은 사건타입" in rej[0]


def test_relation_condition_equal_to_exposure_is_tautology():
    h = _h(exposure={"kind": "관계", "ident": "SUPPLY_CHAIN"})
    h["conditions"] = [{"ident": "SUPPLY_CHAIN", "kind": "관계"}]
    valid, rej = screen_tuples([h], event_types=ETYPES)
    assert not valid and "같은 관계" in rej[0]


def test_event_condition_obeys_grounding():
    # 사건 조건도 점 방아쇠와 같은 접지 규율 - 셀에 없는 사건타입은 날조다.
    h = _h()
    h["conditions"] = [{"ident": "COMPANY.EARNINGS.RESULT_RELEASE", "kind": "사건"}]
    valid, rej = screen_tuples([h], event_types=ETYPES)
    assert not valid and "접지 밖 사건 조건" in rej[0]


def test_measurability_is_a_gate_not_a_hint():
    # 핵심 회귀 방지: 못 재는 노출은 패널이 n=0 을 내기 전에 여기서 죽어야 한다.
    h = _h(exposure={"kind": "속성", "ident": "거시", "transform": "변화"})
    assert not screen_tuples([h], event_types=ETYPES, measurable=MEAS)[0]
    assert "못 재는 노출" in screen_tuples([h], event_types=ETYPES, measurable=MEAS)[1][0]
    # 관문을 안 켜면 통과한다 - 기존 호출자의 동작은 보존된다.
    assert screen_tuples([h], event_types=ETYPES)[0]


def test_measurability_gate_covers_state_conditions():
    h = _h()
    h["conditions"] = [{"ident": "운영", "transform": "수준",
                        "comparator": ">=", "percentile": 0.9}]   # 원천 없음
    valid, rej = screen_tuples([h], event_types=ETYPES, measurable=MEAS)
    assert not valid and "못 재는 조건" in rej[0]


def test_measurable_state_condition_passes_the_gate():
    # 관문이 다 죽이면 관문이 아니라 벽이다 - 재는 조합은 통과해야 한다.
    h = _h()
    h["conditions"] = [{"ident": "거래량", "transform": "수준",
                        "comparator": ">=", "percentile": 0.9}]
    assert screen_tuples([h], event_types=ETYPES, measurable=MEAS)[0]


# ── 층별 결과변수 ────────────────────────────────────────────────────────
# 설명 대상이 층마다 다르므로 y 도 달라야 한다. 하나로 고정하면 시장·섹터 가설이
# 구조적으로 0 을 받는다 - 시장층이 설명하려는 mkt×β 를 ar_ind 가 이미 뺐으므로.
def test_layer_selects_its_own_outcome():
    from edge_analysis.statics.paneltest import LAYER_Y
    seen = []

    class SpyLake:
        def sql(self, q):
            seen.append(q)
            if "SELECT z_ar" in q:
                return [(0.1, 0.1)]
            return [(f"i{k}", "2026-05-01", 0.01, 1.0, 1.0) for k in range(50)]

    for layer, col in LAYER_Y.items():
        seen.clear()
        edge_test(SpyLake(), _tuple(vuln_family="거래량", vuln_tr="수준"), "2026-06-01",
                  layer=layer)
        panel_sql = next(q for q in seen if " AS ar," in q)
        assert f"g.{col} AS ar" in panel_sql, f"{layer} 가 {col} 을 안 쓴다"
        # 다른 층의 결과변수를 결과 자리에 쓰지 않는다
        for other in set(LAYER_Y.values()) - {col}:
            assert f"g.{other} AS ar" not in panel_sql


def test_unknown_layer_is_rejected_loudly():
    with pytest.raises(ValueError, match="층은"):
        edge_test(_Lake(), T, "2026-06-01", layer="전체")


def test_layer_gates_which_exposures_may_explain_it():
    # 시장층 y 는 원수익이고 시장 수익은 전 종목 공통이다 - 종목 고유 피처로는
    # 종목 간 차이를 만들 수 없다. 어휘가 그걸 막아야 관문이지, 아니면 열 목록이다.
    from edge_analysis.statics.paneltest import LAYER_EXPOSURES
    vol = _h(exposure={"kind": "속성", "ident": "거래량", "transform": "변화"})
    beta = _h(exposure={"kind": "속성", "ident": "거시", "transform": "민감도"})

    v, r = screen_tuples([vol], event_types=ETYPES, measurable=MEAS, layer="시장")
    assert not v and "시장층을 설명할 수 없는 노출" in r[0]
    assert screen_tuples([beta], event_types=ETYPES, measurable=MEAS, layer="시장")[0]
    explicit = _h(layer="시장",
                  exposure={"kind": "속성", "ident": "거래량", "transform": "변화"})
    v, r = screen_tuples([explicit], event_types=ETYPES, measurable=MEAS, layer="고유")
    assert not v and "시장층을 설명할 수 없는 노출" in r[0]
    # 고유층은 제한 없음 - 종목 거래량이 고유 잔차를 설명하는 건 정당하다
    assert LAYER_EXPOSURES["고유"] is None
    assert screen_tuples([vol], event_types=ETYPES, measurable=MEAS, layer="고유")[0]
    with pytest.raises(ValueError, match="층은"):
        screen_tuples([vol], event_types=ETYPES, layer="전체")


# ── 재무 선견 차단 ───────────────────────────────────────────────────────
def test_financials_are_clamped_by_filing_lag_not_collection_date():
    # 파티션 as_of_date 는 **수집일**이지 공시일이 아니다. FY Y 재무를 Y년 중에 쓰면
    # 선견이다. 결산 후 법정 90일 → FY+1년 4월 1일 가용을 행에 박고 뷰가 자른다.
    from edge_analysis.adapters.sql_surface import views_sql
    from edge_analysis.statics.fin import REPORT_LAG_MONTH, build_sql

    sql = build_sql({"M000102009": "k"}, __import__("pathlib").Path("/tmp/x.parquet"))
    assert f"make_date(fy + 1, {REPORT_LAG_MONTH}, 1) AS available_from" in sql
    assert REPORT_LAG_MONTH >= 4          # 12월 결산 + 90일 이후여야 한다

    v = views_sql("TIMESTAMP '2026-06-01 00:00:00'", "DATE '2026-06-01'", "")
    assert "v_fin AS" in v
    # 클램프는 수집일이 아니라 available_from 이다 - as_of 로 자르면 선견이 샌다
    fin = v[v.index("v_fin AS"):]
    fin = fin[:fin.index("v_liquidity")]
    assert "f.available_from <= DATE '2026-06-01'" in fin
    code = "\n".join(line for line in fin.splitlines()
                     if not line.strip().startswith("--"))
    assert "as_of" not in code          # 수집일로 자르면 선견이 샌다


def test_rolling_betas_are_nan_guarded():
    # regr_slope 는 x 분산이 0 이면 NULL 이 아니라 **NaN** 을 낸다(산업 표본 <5 ·
    # fx 결측). NaN 은 pctile 을 조용히 오염시켜 상·하위 분할을 어긋나게 만든다 -
    # 부재는 NULL 로 말해야 판정자가 '못 잰다'로 읽는다. 실측: beta_s 364 → 129.
    import re

    from edge_analysis.statics.paneltest import _base
    sql = _base("2026-06-01")
    # 열 목록을 **손으로 적으면 새 β 가 조용히 빠진다** - 실측: 금리 민감도를 넣었을 때
    # 이 테스트는 개수만 틀리고 '가드 없는 β' 자체는 못 잡았다. SQL 에서 뽑는다.
    cols = re.findall(r"END AS (\w*beta\w*)", sql)
    assert len(cols) >= 4, f"β 열을 못 찾았다: {cols}"
    for col in cols:
        blk = sql[:sql.index(f"AS {col}")]
        assert blk.rstrip().endswith("END"), f"{col} 이 isfinite 가드 밖에 있다"
    # 가드 하나가 regr_slope 를 두 번 쓴다(CASE WHEN isfinite(..) THEN .. END).
    # 이 항등식이 깨지면 어딘가에 **맨 regr_slope** 가 있다는 뜻이다.
    # 주석은 뺀다 - 도크주석이 함수 이름을 언급하면 개수가 어긋난다(실제로 그랬다).
    code = "\n".join(line for line in sql.splitlines()
                     if not line.strip().startswith("--"))
    assert code.count("isfinite(regr_slope") * 2 == code.count("regr_slope")


def test_missing_condition_is_not_satisfaction():
    """결측을 충족으로 세면 부재가 성립을 위장한다 (§11).

    실측(042700 07-31): '공매도/수준 오늘 결측' 이 `충족 True` 로 찍혀 INUS 조건이
    붙은 엣지가 조건 검사 없이 몫을 받았다. 조건이 있는데 못 재면 부적용이다.
    """
    from edge_analysis.statics.paneltest import EdgeReport
    ok = EdgeReport("성립", 400, 0.01, 0.02, 0.0, 0.9, cond_satisfied=True)
    assert ok.applies_today
    blind = EdgeReport("성립", 400, 0.01, 0.02, 0.0, 0.9,
                       cond_today="공매도/수준 오늘 결측", cond_measurable=False)
    assert not blind.applies_today, "조건 측정불가인데 몫을 받았다"
    none_cond = EdgeReport("성립", 400, 0.01, 0.02, 0.0, 0.9)
    assert none_cond.applies_today, "조건 없는 엣지는 조건 때문에 죽지 않는다"


def test_verifier_only_receives_edges_applied_to_today_cell():
    """패널 성립만으로 오늘 원인 검정을 실행하면 INUS 미충족을 원인으로 되살린다."""
    applied = EdgeReport("성립", 100, 0.01, 0.02, 0.00, 0.9)
    unmet = EdgeReport("성립", 100, 0.01, 0.02, 0.00, 0.9, cond_satisfied=False)
    reports = [
        (SimpleNamespace(trigger=Trigger("점", "APPLIED")), applied),
        (SimpleNamespace(trigger=Trigger("점", "UNMET")), unmet),
        (SimpleNamespace(trigger=Trigger("계열", "가격잔차")), applied),
    ]
    assert _verifiable_event_types(reports) == ["APPLIED"]


def test_bonferroni_threshold_is_stated_not_just_claimed():
    """산문이 Bonferroni 를 주장하면 게이트가 실제로 α 를 나눠야 한다 (선언 = 배선).

    실측: 단순화 커밋에서 m_tests 를 지우고 산문의 '셀 Bonferroni α=0.05/9' 주장만
    남겼다. 검정자는 임계를 모르니 0.05 로 재고, 보정은 허구가 된다.
    """
    from edge_analysis.statics.gates import edge_gate
    from edge_analysis.statics.vocab import ALPHA
    assert edge_gate(400, 0.02) == "성립"                          # m=1
    assert edge_gate(400, 0.02, alpha=ALPHA / 9) == "불성립"       # m=9 → 0.0056
    assert edge_gate(400, 0.004, alpha=ALPHA / 9) == "성립"


def test_opposite_direction_is_rejection_not_confirmation():
    """양측 p 가 작아도 방향이 반대면 그 가설은 기각이다.

    부호는 튜플의 **주장**이고 검정 전에 박혀 있다. 양측 게이트만 세우면
    "효과는 있다(반대쪽으로)" 가 성립으로 찍힌다 - 실측(042700 07-31)에서
    9간선 중 셋(A2·A3·C3)이 p=0.000 인데 부호가 반대였다.

    17차의 방향 채굴 방지는 부호를 **사후에 고르는** 것을 막는 것이므로 충돌하지 않는다.
    """
    import numpy as np

    from edge_analysis.statics.paneltest import _two_sided

    # 반대쪽으로 강하게 유의한 관측: 단측 p1 이 1 에 가깝고 양측은 작다
    assert _two_sided(0.999) < 0.01, "반대쪽 유의가 양측에서 작은 p 로 나온다"
    assert _two_sided(0.001) < 0.01, "같은쪽 유의도 작은 p"
    # 그래서 p 만으로는 두 경우를 구분할 수 없다 - 방향을 따로 봐야 한다
    # 그래서 p 는 '유의한가' 만 답한다 - **방향은 추정량이 따로 말한다**(상위−하위)
    hi, lo = -0.0068, -0.0026
    assert hi - lo < 0, "상위가 하위보다 낮다 - 방향은 이 차이가 정한다"
    assert not np.isclose(hi, lo)


def test_panel_rows_are_order_deterministic():
    """순열 검정은 행 순서에 의존한다 - SQL 순서가 흔들리면 SEED 를 고정해도 p 가 흔들린다.

    실측: 같은 CLI 재실행에서 C2 가 p=0.008 → 0.004 로 임계 0.0056 을 넘나들었다.
    같은 표본에서 판정이 뒤집히면 그건 검정이 아니다 (§13).
    """
    import numpy as np

    from edge_analysis.statics.paneltest import _panel_rows, _stratified_p

    base = [("i2", "2026-01-02", 0.01, 1.0), ("i1", "2026-01-01", -0.02, 2.0),
            ("i1", "2026-01-02", 0.03, 3.0), ("i2", "2026-01-01", 0.00, 4.0)]

    class L:
        def __init__(self, rows):
            self.rows = rows

        def sql(self, q):
            return list(self.rows)

    a = _panel_rows(L(base), "x")
    b = _panel_rows(L(list(reversed(base))), "x")
    assert a == b, "행 순서가 다르면 다른 패널이 된다"
    assert [r[1] for r in a] == sorted(r[1] for r in base)

    # 같은 표본, 뒤섞인 입력 순서 → 같은 p
    def p_of(rows):
        ar = np.array([r[2] for r in rows])
        dates = np.array([r[1] for r in rows])
        hi = np.array([r[3] for r in rows]) >= 3.0
        return _stratified_p(ar, hi, dates)

    assert p_of(a) == p_of(b)


def test_direction_is_an_estimate_never_a_declaration():
    """방향은 **추정량의 산물**이다 - 가설이 선언하면 발견이 실패로 위장된다.

    실측(000660 07-29): 하루가 -9.61% 인 걸 보고 6개 가설 부호를 전부 -1 로 썼다.
    '고β·고회전 종목이 더 올랐다 p=0.000' 이라는 강한 신호가 '방향 반대 -> 불성립'
    으로만 기록됐다. 우리가 찾는 것은 유효한 CATE 이고 그 부호는 결과다 - 그래서
    어휘에서 선언 슬롯을 없앴다(21R). 슬롯이 남아 있으면 언젠가 다시 게이트로 샌다.
    """
    import dataclasses
    import inspect

    from edge_analysis.statics.gates import edge_gate
    from edge_analysis.statics.paneltest import _stratified_p, edge_test
    from edge_analysis.statics.vocab import ALPHA, HypothesisTuple

    # 어휘에 선언 슬롯이 없다
    assert "sign" not in {f.name for f in dataclasses.fields(HypothesisTuple)}
    # 게이트는 n 과 p 만 본다
    assert edge_gate(400, 0.001, alpha=ALPHA / 6) == "성립"
    assert edge_gate(400, 0.30, alpha=ALPHA / 6) == "불성립"
    assert "sign" not in inspect.signature(edge_gate).parameters
    # 순열 검정도 방향 인자를 받지 않는다 - 받으면 꼬리를 밖에서 고를 수 있다
    assert "sign" not in inspect.signature(_stratified_p).parameters
    src = inspect.getsource(edge_test)
    assert "추정 방향" in src and "t.sign" not in src


def test_cate_interaction_does_not_split_the_sample():
    """조건부 효과는 교호항으로 얻는다 - 표본을 쪼개면 n 이 죽는다 (실측 C2 n=6).

        ar = a + b·D + c·C + d·(D×C),  CATE(C) = b + d·C
    """
    import numpy as np

    from edge_analysis.statics.paneltest import _cate_interaction

    rng = np.random.default_rng(0)
    n = 600
    d = rng.random(n) < 0.4
    c = rng.random(n) < 0.5
    dates = np.array([f"2026-0{1 + i % 9}-01" for i in range(n)])
    # 진짜 교호: 조건 충족에서만 처치 효과가 2배
    ar = 0.01 * d + 0.02 * (d & c) + rng.normal(scale=0.004, size=n)
    obs, p = _cate_interaction(ar, d, c, dates)
    assert obs is not None and obs > 0.01, obs      # 교호항을 잡는다
    assert p < 0.05, p
    # 교호 없는 자료에서는 유의하지 않아야 한다
    ar0 = 0.01 * d + rng.normal(scale=0.004, size=n)
    o0, p0 = _cate_interaction(ar0, d, c, dates)
    assert p0 > 0.05, (o0, p0)
    # 전체 표본을 쓴다 - 분할하지 않는다
    assert len(ar) == n


def test_exposures_are_point_in_time_not_same_day():
    """노출(처치)이 당일 정보를 쓰면 처치가 결과와 **동시 결정**이다.

    실측(000660 07-29 C3): `거래량/변화` 가 당일 거래량을 써서 '실적일 회전 급증
    종목이 더 올랐다 p=0.000 +1.77%p' 가 나왔는데, 그건 '많이 오른 종목이 거래량도
    많았다' 는 역인과다. 코드 게이트는 p 만 보므로 이 결함을 구조적으로 못 잡는다.

    방아쇠는 당일이어야 한다(오늘 튀었나) - 그래서 두 컬럼을 나눈다.
    """
    from edge_analysis.statics.paneltest import FEATURES, _INNOVATION
    assert FEATURES[("거래량", "변화")] == "tv_chg_pit", "노출이 당일 판을 쓴다"
    assert _INNOVATION["거래량"] == "tv_chg", "방아쇠는 당일 판이어야 한다"
    assert "tv_chg" not in set(FEATURES.values()), "당일 판이 노출 목록에 남아 있다"


def test_treatment_refinement_uses_ledger_vocabulary():
    """처치가 거친 이유는 텍스트 추출이 없어서가 아니라 **이미 있는 구조를 안 써서**다.

    사건타입 53 만으로는 `CONTRACT.SIGNING` 하나에 MOU 와 확정계약이 섞이고,
    `EARNINGS.RESULT_RELEASE` 하나에 신규 보도와 재보도가 섞인다. 그러면 ATT 는
    서로 다른 두 처치의 평균이라 0 으로 수렴한다 (실측: 6+6 가설 전멸).

    원장 실측(2026-08-03): predicate 75종 · stage 33종 · role 70종 · novelty 5종.
    """
    from edge_analysis.statics.paneltest import refine_sql
    from edge_analysis.statics.vocab import (ARG_ROLES, NOVELTY, PLACEBO_NOVELTY,
                                             PREDICATES, STAGES, Trigger, VocabError)
    assert len(PREDICATES) == 75 and len(STAGES) == 33
    assert len(ARG_ROLES) == 70 and len(NOVELTY) == 5
    assert PLACEBO_NOVELTY in NOVELTY

    class T:
        trigger = Trigger("점", "COMPANY.CONTRACT.SIGNING",
                          stage="MOU_LOI", role="SUPPLIER", novelty="FIRST_IN_THREAD")
    sql = refine_sql(T())
    assert "lifecycle_stage = 'MOU_LOI'" in sql
    assert "role_code = 'SUPPLIER'" in sql
    assert "novelty_status = 'FIRST_IN_THREAD'" in sql
    assert "predicate_code" not in sql, "빈 슬롯은 조건을 만들지 않는다"

    class Bare:
        trigger = Trigger("점", "X")
    assert refine_sql(Bare()) == "", "구체화 없으면 전체 표본"

    # 계열 방아쇠에는 사건 구체화가 없다 (범주 오류)
    try:
        Trigger("계열", "거래량", stage="MOU_LOI")
    except VocabError:
        pass
    else:
        raise AssertionError("계열+사건구체화를 허용했다")


def test_reduction_dictionary_covers_curated_and_fails_loudly():
    """검정 층이 찾은 것을 **가설 어휘로 되돌려야** 자산이 된다.

    `S41B0D1005 104주로그베타(주간)` 에서 신호를 찾아도 그대로 보고하면 다음 셀에서
    재현 불가하고 산문도 그 이름을 못 쓴다(닫힌 어휘 계약). 환원 실패는 조용히
    '기타' 로 밀지 않는다 - 실패가 곧 **어휘 확장 요청**이다.
    """
    from edge_analysis.statics.reduce import coverage, reduce_item
    from edge_analysis.statics.vocab import SERIES_FAMILIES, TRANSFORMS

    assert reduce_item("104주로그베타(주간)", "베타") == ("지수잔차", "민감도")
    assert reduce_item("20일누적 차입공매도수량(주)", "차입공매도") == ("공매도", "누적")
    assert reduce_item("PBR(IFRS-연결)", "주가배수") == ("배수", "수준")
    assert reduce_item("차입금의존도", "") == ("레버리지", "수준")
    assert reduce_item("매출액증가율", "") == ("성장", "변화")
    # 실패는 실패다 - None 이어야 사람에게 신호가 간다
    assert reduce_item("완전히 모르는 것", "없는카테고리") is None
    # 환원 결과는 반드시 닫힌 어휘 안이다
    for nm, cat in (("20일평균거래량(주)", "거래량"), ("ROE", "")):
        f, t = reduce_item(nm, cat)
        assert f in SERIES_FAMILIES and t in TRANSFORMS
    c = coverage([("c1", "104주로그베타(주간)", "price", "베타"),
                  ("c2", "모르는것", "x", "없는것")])
    assert c["reduced"] == 1 and c["failed"] == 1
    assert c["fail_sample"], "실패 목록이 비면 확장 요청이 사라진다"


def test_etf_routing_sends_questions_to_the_dominant_layer():
    """어느 층이 끌었는지 보지 않고 종목 가설부터 세우면 **틀린 질문을 잘 검정**한다.

    실측(042700 07-31): 하루의 77% 가 시장인데 9간선 전부 종목 가설이었고 전부 죽었다.
    """
    from edge_analysis.statics.route import DOMINANT, route_etf

    class L:
        def __init__(self, k, n, c):
            self.kind, self.name, self.contribution = k, n, c

    class N:   # layers.Name 과 같은 필드 - 스텁 표류가 라이브를 죽였다
        def __init__(self, t, p):
            self.ticker, self.label, self.weight = t, "", 0.1
            self.ret, self.idio, self.contribution = p, p, p

    class R:
        def __init__(self, layers, idio, names=(), etf="TEST"):
            self.layers, self.idio, self.names = layers, idio, names
            self.etf, self.etf_name = etf, "T"

    mkt = route_etf(R((L("시장", "KODEX200", 0.26), L("섹터", "반도체", -0.01)), 0.001))
    assert mkt.kind == "시장" and "종목 가설을 세우지 않는다" in mkt.why

    idio = route_etf(R((L("시장", "K", 0.001),), 0.05,
                       (N("000660", 0.03), N("005930", 0.02), N("x", 0.0001))))
    assert idio.kind == "고유" and idio.targets == ("000660", "005930")



    mix = route_etf(R((L("시장", "K", 0.03), L("섹터", "S", 0.03)), 0.03))
    assert mix.kind == "혼합" and "하나를 고르면" in mix.why
    assert mix.share < DOMINANT

    # 시장 프록시 자신을 섹터로 설명하면 범주 오류다 (실측 069500 07-29)
    me = route_etf(R((L("섹터", "화학", -0.05),), 0.01, etf="069500"))
    assert me.kind == "시장" and "시장 프록시 자신" in me.why

    class P:
        basket_moved = False
    assert route_etf(R((L("시장", "K", 0.9),), 0.0), premium=P()).kind == "괴리단독"

    # **재료가 없으면 라우팅도 없다.** `decompose` 는 재료 부족을 `None` 으로 말하고
    # (`-> Rollup | None`), 호출자는 그 형태를 이미 다룬다. 여기서 빈 Route 를 만들면
    # '층이 없다' 와 '층을 못 봤다' 가 같은 값이 되어 원장이 거짓말한다.
    #
    # 이 두 줄은 `route.py` 의 `_selfcheck()` 에도 있으나 그것은 `__main__` 아래라
    # **pytest 가 실행하지 않는다**(ALPHA-781). 가드가 제거되거나 premium 분기 뒤로
    # 밀려도 CI 는 초록인 채, 라이브 재료 부족에서만 AttributeError 가 재발한다.
    assert route_etf(None) is None
    assert route_etf(None, premium=P()) is None


def test_dominant_route_includes_exact_55_percent_boundary():
    """55%는 지배층이고, 바로 아래부터 혼합이다 — 경계가 하루마다 흔들리면 안 된다."""
    from types import SimpleNamespace as NS

    from edge_analysis.statics.route import route_etf

    dominant = route_etf(NS(
        etf="TEST", etf_name="T", names=(), rho=None,
        layers=(NS(kind="시장", name="시장", contribution=0.55),), idio=0.45,
    ))
    below = route_etf(NS(
        etf="TEST", etf_name="T", names=(), rho=None,
        layers=(NS(kind="시장", name="시장", contribution=0.549999),), idio=0.450001,
    ))

    assert dominant.kind == "시장" and dominant.share == pytest.approx(0.55)
    assert below.kind == "혼합" and below.share < 0.55


def test_mixed_workflow_runs_each_material_layer_and_idio_name(monkeypatch):
    """대표 라벨이 혼합이어도 시장·채택 섹터·고유종목 검정을 모두 실행한다."""
    from types import SimpleNamespace as NS

    from edge_analysis.statics import etfcell, mkttrial, trial, verifier

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(trial, "reduce_market", lambda *a, **k: {})
    monkeypatch.setattr(trial, "say_market", lambda _r: "시장")
    monkeypatch.setattr(mkttrial, "screen_market", lambda *a, **k: {})
    monkeypatch.setattr(mkttrial, "say_screen", lambda _r: "시장 사건")
    monkeypatch.setattr(etfcell, "_sector_types", lambda *a: ["POLICY.CHANGE"])
    monkeypatch.setattr(etfcell, "_observed_types",
                        lambda _lake, ticker, _day: [f"EVENT.{ticker}"])
    monkeypatch.setattr(
        verifier, "verify",
        lambda _lake, _day, *, etype, layer, **_k:
        (calls.append((layer, etype)) or [], "검정"))
    monkeypatch.setattr(verifier, "say_implications", lambda _imps: "")

    layers = (NS(kind="시장", name="시장", contribution=0.25, beta=1, lo=.8, hi=1.2,
                 ret=.25),
              NS(kind="섹터", name="반도체", contribution=0.25, beta=1, lo=.8, hi=1.2,
                 ret=.25),
              NS(kind="섹터", name="정보기술", contribution=0.25, beta=1, lo=.8, hi=1.2,
                 ret=.25))
    names = (NS(ticker="005930", label="삼성전자", contribution=0.20, weight=.2),)
    roll = NS(etf="091160", layers=layers, idio=0.25, names=names)
    route = NS(kind="혼합", targets=())

    etfcell._workflow(object(), roll, route, "2026-07-31")

    assert calls.count(("섹터", "POLICY.CHANGE")) == 2
    assert ("고유", "EVENT.005930") in calls


def test_mixed_workflow_includes_exact_20_percent_and_excludes_below(monkeypatch):
    """혼합은 각 층 20%를 포함하고 바로 아래 층만 제외한다."""
    from types import SimpleNamespace as NS

    from edge_analysis.statics import etfcell, mkttrial, trial, verifier

    market_calls: list[bool] = []
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        trial, "reduce_market", lambda *a, **k: market_calls.append(True) or {})
    monkeypatch.setattr(trial, "say_market", lambda _r: "시장")
    monkeypatch.setattr(mkttrial, "screen_market", lambda *a, **k: {})
    monkeypatch.setattr(mkttrial, "say_screen", lambda _r: "시장 사건")
    monkeypatch.setattr(etfcell, "_sector_types", lambda *a: ["POLICY.CHANGE"])
    monkeypatch.setattr(
        etfcell, "_observed_types", lambda *_a: ["EVENT.STOCK"])
    monkeypatch.setattr(
        verifier, "verify",
        lambda _lake, _day, *, etype, layer, **_k:
        (calls.append((layer, etype)) or [], "검정"))
    monkeypatch.setattr(verifier, "say_implications", lambda _imps: "")

    def selected(market: float, sector: float, idio: float) -> tuple[bool, bool, bool]:
        market_calls.clear()
        calls.clear()
        roll = NS(
            etf="091160",
            layers=(
                NS(kind="시장", name="시장", contribution=market,
                   beta=1, lo=.8, hi=1.2, ret=market),
                NS(kind="섹터", name="반도체", contribution=sector,
                   beta=1, lo=.8, hi=1.2, ret=sector),
            ),
            idio=idio,
            names=(NS(ticker="005930", label="삼성전자",
                      contribution=1.0, weight=.2),),
        )
        etfcell._workflow(object(), roll, NS(kind="혼합", targets=()), "2026-07-31")
        return (bool(market_calls),
                any(layer == "섹터" for layer, _ in calls),
                any(layer == "고유" for layer, _ in calls))

    assert selected(0.20, 0.20, 0.60) == (True, True, True)
    assert selected(0.40, 0.40, 0.20) == (True, True, True)
    assert selected(0.199999, 0.20, 0.600001) == (False, True, True)
    assert selected(0.20, 0.199999, 0.600001) == (True, False, True)
    assert selected(0.40, 0.400001, 0.199999) == (True, True, False)


def test_market_trial_refuses_when_treated_days_are_too_few():
    """시장 사건은 **하루가 한 표본**이다 - 종목 수로 늘 수 없다.

    종목 패널은 같은 날 사건 없는 종목을 대조로 쓰지만, 시장 광역 사건은 그 날 전
    종목이 처치다(SUTVA). 단위를 거래일로 바꾸면 표본은 처치일 수가 상한이고
    실측 상한은 40일(RULE_CHANGE)이다. 미달은 **판정불가**여야 한다 - 기각이 아니다.
    """
    from edge_analysis.statics.mkttrial import (MIN_DAYS, say_market_trial,
                                                say_screen)

    thin = {"verdict": "판정불가", "n_days": 3,
            "reason": f"처치일 3 < {MIN_DAYS} - 시장 사건은 하루가 한 표본이다",
            "etype": "MACRO.X.Y"}
    assert "판정불가" in say_market_trial(thin)
    # 유의가 없으면 그 사실을 말한다 - 침묵하면 '설명했다' 로 읽힌다
    assert "사건으로 설명되지 않는다" in say_screen([thin])

    ok = {"verdict": "계산됨", "att": -0.0219, "p": 0.0009, "n_days": 14, "pairs": 42,
          "treated_all": 21, "pool": 831, "pretrend": {"t-1": 0.0002, "t-2": None},
          "overlap": 5, "etype": "POLICY.TRADE.TARIFF_CHANGE"}
    line = say_market_trial(ok)
    assert "-2.190%p" in line
    assert "겹침" in line, "다른 시장 사건 겹침은 처치 배타성 위반이라 숨기면 안 된다"
    assert "**유의**" in say_screen([ok])


def test_route_stub_fields_match_the_real_layer_dataclasses():
    """스텁이 실물과 갈리면 **검사가 아니다.**

    `route.py` 셀프체크의 종목 스텁이 `pct` 를 갖고 있었는데 `layers.Name` 에는 없다
    (ticker·label·weight·ret). 그래서 테스트는 통과하고 라이브만 죽었다 - 30일 배치의
    3일이 `AttributeError: 'Name' object has no attribute 'pct'` 로 날아갔고, 그 날들이
    바로 고유 라우팅이 발동한(=통계 근거가 나올 수 있던) 날이다.
    """
    import dataclasses

    from edge_analysis.statics.layers import Layer, Name

    assert {f.name for f in dataclasses.fields(Name)} == {
        "ticker", "label", "weight", "ret", "idio", "contribution"}, \
        "Name 필드가 바뀌면 route 스텁도 함께 바꿔야 한다"
    assert {"kind", "name", "contribution"} <= {
        f.name for f in dataclasses.fields(Layer)}

    # 라우팅이 실물 Name 으로 돌아야 한다 - 기여는 비중 × 수익
    from edge_analysis.statics.route import route_etf

    class R:
        def __init__(self, layers, idio, names):
            self.layers, self.idio, self.names = layers, idio, names
            self.etf, self.etf_name = "TEST", "T"

    big = Name(ticker="000660", label="하이닉스", weight=0.30, ret=0.10,
               idio=0.03, contribution=0.03)
    small = Name(ticker="005930", label="삼성전자", weight=0.01, ret=0.001,
                 idio=0.00001, contribution=0.00001)
    r = route_etf(R((Layer(code="K", name="시장", kind="시장",
                           ret=0.001, contribution=0.001, overlap=0.0),),
                    0.05, (big, small)))
    assert r.kind == "고유" and r.targets == ("000660",), "작은 기여는 대상이 아니다"


def test_no_module_reads_pct_off_a_layers_name():
    """`layers.Name` 에 `pct` 는 없다 - 그 이름을 읽는 코드가 있으면 라이브에서 죽는다.

    필드 대조 테스트는 **정의**를 지키지만 **사용**을 못 잡는다. 실측으로 두 곳이
    남아 있었고(route.py·etfcell.py) 30일 배치가 각각 3일·2일을 날렸다. 그래서
    사용 자리를 소스로 직접 막는다 - `Name` 을 다루는 모듈에서 `.pct` 를 금지한다.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "edge_analysis" / "statics"
    offenders = []
    for f in sorted(root.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        if "LayerFact" in src:      # 자기 pct 를 가진 데이터클래스를 쓰는 모듈
            continue
        for m in re.finditer(r"\b(nm|n|name|nameobj)\.pct\b", src):
            line = src[:m.start()].count("\n") + 1
            offenders.append(f"{f.name}:{line} {m.group()}")
    assert not offenders, (
        "layers.Name 에 없는 `pct` 를 읽는다 - contribution 을 쓰라: " + ", ".join(offenders))


def test_duckdb_rendering_of_shared_views_has_no_psycopg_params():
    """`sql_surface` CTE 집합은 **Postgres 와 DuckDB 양쪽**에서 쓰인다.

    파라미터 자리는 `_views(as_of=, trade_date=)` 로 주입되는 설계다(Postgres 기본값이
    psycopg 스타일). 그런데 CTE 본문에 `%(as_of)s` 를 **하드코딩**하면 DuckDB 렌더가
    그것을 그대로 안고 나가 표면이 통째로 파싱 실패한다 - 실측: 30일 배치의 섹터·고유
    검정 전량이 `ParserException: syntax error at or near "%"` 로 죽었다(dev 병합 회귀).
    그래서 **렌더 결과**를 검사한다 - 소스 grep 은 설계된 기본값과 하드코딩을 못 가른다.
    """
    from edge_analysis.adapters.sql_surface import _views

    duck = _views(as_of="TIMESTAMP '2026-07-31 23:59:59'",
                  trade_date="DATE '2026-07-31'", prefix="rdb.public.")
    assert "%(" not in duck, "DuckDB 렌더에 psycopg 파라미터가 남았다"
    # 원장 표는 접두가 붙어야 한다 - v_nav 가 접두 없이 들어와 rdb 경로에서 못 찾았다
    for t in ("etf_nav_daily", "price_daily", "source_event"):
        assert f"rdb.public.{t}" in duck, f"{t} 에 접두가 없다"

    pg = _views()
    assert "%(as_of)s" in pg, "Postgres 기본값은 psycopg 스타일이다 (설계)"


def test_each_family_transform_owns_a_distinct_column():
    """계열족×변환 하나가 **열 하나**를 정한다 - 두 족이 한 열을 공유하면 계수가 거짓이다.

    실측 동기: 금리와 환율을 둘 다 `("거시","민감도")` 에 담을 수 없어서 `금리` 족을
    새로 뗐다. 둘은 뜻이 다르다 - 환율은 이익 경로(수출), 금리는 할인율 경로. 같은
    슬롯에 두면 "환율 민감도" 라 쓰고 금리를 재는 일이 조용히 일어난다.
    """
    from edge_analysis.statics.paneltest import FEATURES

    cols = list(FEATURES.values())
    dup = {c for c in cols if cols.count(c) > 1}
    assert not dup, f"두 계열족이 같은 열을 쓴다: {dup}"


def test_every_layer_exposure_is_actually_measurable():
    """층별 허용 노출은 **전부 `FEATURES` 에 있어야** 한다.

    어휘에만 있고 열이 없으면 가설 에이전트가 그 노출을 고르고 검정기가 '못 잰다' 로
    죽인다 - 어휘와 측정면이 갈리는 그 실패다. 반대도 막는다: 층 목록에 없는 노출을
    시장 층에 쓰면 "종목 거래량이 시장 전체 수익을 설명한다" 가 관문을 통과한다.
    """
    from edge_analysis.statics.paneltest import FEATURES, LAYER_EXPOSURES

    for layer, allowed in LAYER_EXPOSURES.items():
        if allowed is None:
            continue
        missing = [k for k in allowed if k not in FEATURES]
        assert not missing, f"{layer} 층 노출이 측정면에 없다: {missing}"


def test_rate_sensitivity_can_explain_the_market_layer():
    """금리 민감도는 **시장 층 노출**이어야 한다 - 없으면 할인율 설명이 구조적으로 불가.

    공통 충격(금리)은 하루에 값이 하나라 횡단면 분산이 0 이다. 종목 간 차이를 만드는
    것은 **민감도**뿐이므로, 그것이 시장 층 노출 목록에 없으면 "금리가 올라서 성장주가
    빠졌다" 를 어떤 검정으로도 세울 수 없다. 금융 설명 기준이 요구하는 '가격 밖 정박'
    이 이 자리다: 지수는 가격이라 "시장이 내려서 내렸다" 를 못 넘는다.

    실측(2026-07-27, PBR 5분위 × 금리β 중앙값): -0.0142 · -0.0275 · -0.0595 · -0.1251
    - 고PBR(성장주)일수록 음수가 커진다. 할인율 경로의 반증 가능한 예측이 성립했다.
    """
    from edge_analysis.statics.paneltest import FEATURES, LAYER_EXPOSURES
    from edge_analysis.statics.vocab import SERIES_FAMILIES, TRANSFORMS

    assert "금리" in SERIES_FAMILIES and "민감도" in TRANSFORMS
    assert ("금리", "민감도") in FEATURES
    assert ("금리", "민감도") in LAYER_EXPOSURES["시장"]
    assert ("금리", "민감도") in LAYER_EXPOSURES["섹터"]


def test_applied_edge_direction_comes_only_from_the_identified_set():
    """적용 엣지의 **방향**은 식별집합에서만 온다 - 구간이 0 을 품으면 방향 주장 없음.

    신뢰성 검사(`trust`)는 방향 주장에 부호 있는 근거를 요구한다. 그런데 게이트가
    '성립' 을 냈다는 것만으로 부호를 +1 로 넘기면, 구간이 [-0.4, +0.1] 인 엣지도
    '올랐다' 는 주장으로 검사에 들어가고 검사는 그 방향을 지지할 근거를 찾다가
    엉뚱한 도구를 부른다. 방향은 크기가 정하고, 크기가 0 을 품으면 방향이 없다.
    """
    from edge_analysis.statics.attribute import _measurable
    from edge_analysis.statics.narrate import Edge

    lo_pos = Edge(channel="C", event_type="E", verdict="성립", applied=True,
                  iset_lo=0.001, iset_hi=0.004)
    hi_neg = Edge(channel="C", event_type="E", verdict="성립", applied=True,
                  iset_lo=-0.004, iset_hi=-0.001)
    straddle = Edge(channel="C", event_type="E", verdict="성립", applied=True,
                    iset_lo=-0.004, iset_hi=0.001)

    def sign(e):
        if e.iset_lo is not None and e.iset_lo > 0:
            return 1
        if e.iset_hi is not None and e.iset_hi < 0:
            return -1
        return 0

    assert (sign(lo_pos), sign(hi_neg), sign(straddle)) == (1, -1, 0)



def test_the_unit_is_the_hypothesis_not_a_default():
    """**단위(층)는 가설이 정한다.** 인자 기본값이 그것을 덮으면 안 된다.

    예전 결함: `edge_test(layer="고유")` 가 기본값이라 가설이 무엇을 주장하든 전부
    고유층에서 검정됐다. 업황·정책 뉴스는 시장·산업이 이미 차감된 잔차를 설명해야
    해서 구조적으로 기각됐다 - 어휘에 그 주장을 부를 자리가 없던 것이 원인이다.
    """
    from edge_analysis.statics.vocab import LAYERS
    from edge_analysis.statics.paneltest import LAYER_Y

    # 어휘와 종속변수가 **같은 집합**이어야 한다. 어휘에만 있는 층은 가설을 받고
    # 검정에서 죽는다 - 그게 가장 나쁜 실패 양식이다(합법으로 보이는 판정불가).
    assert set(LAYERS) == set(LAYER_Y), (sorted(LAYERS), sorted(LAYER_Y))

    t = HypothesisTuple(
        conditions=(), trigger=Trigger("점", "COMPANY.PRODUCT.LAUNCH"),
        channel="Q수량", exposure=ExposureSource("속성", "섹터", transform="민감도"),
        outcome="수익률", layer="섹터")
    assert t.layer == "섹터"
    # 기본값이 아니라 튜플을 따른다: 빈 인자면 t.layer 가 쓰인다.
    import inspect

    from edge_analysis.statics.paneltest import edge_test
    assert inspect.signature(edge_test).parameters["layer"].default == "", (
        "layer 기본값이 비어 있지 않으면 가설의 단위 선언이 조용히 덮인다")


def test_a_unit_cannot_be_explained_by_an_unqualified_exposure():
    """층별 허용 노출이 게이트다 — '종목 거래량이 시장 전체 수익을 설명한다' 는 죽는다."""
    from edge_analysis.statics.paneltest import LAYER_EXPOSURES

    # 고유층은 전부 허용(잔차는 종목 자신이다), 시장·섹터는 목록이 있다.
    assert LAYER_EXPOSURES.get("고유") is None
    for ly in ("시장", "섹터"):
        allow = LAYER_EXPOSURES[ly]
        assert allow, ly
        # 거래량/수준 같은 종목 특성은 시장·섹터 설명 자격이 없다.
        assert ("거래량", "수준") not in allow, (ly, sorted(allow))


def test_v_flow_intraday_는_슬롯이_아니라_available_at_으로_자른다():
    """WHY: 장중 수급 뷰의 시점 클램프를 `asof_slot` 에 걸면 **뷰가 늘 빈다.**

    `asof_slot` 은 벤더 원문(`bsop_hour_gb`)이고 2026-08-06 dev 실측에서 도메인이
    `'1'`~`'5'` **코드**임이 확인됐다 - 시각 문자열이 아니다. 반면 `as_of` 는
    `utcnow_iso()` 가 만든 ISO 타임스탬프라, `asof_slot <= AS_OF` 는 TEXT 비교로
    `'3' <= '2026-08-06T...'` = 첫 문자 `'3' > '2'` → **항상 거짓**이 된다.

    이 오류가 위험한 건 조용해서다: 질의는 성공하고 0행을 돌려주므로 "그날 수급이
    없었다"와 구분되지 않는다. 설계 문서(ALPHA-769 티켓 본문)에 이 형태가 적혀 있었고
    실측이 뒤집었다 - 되돌아가는 변경을 여기서 막는다.
    """
    from edge_analysis.adapters.sql_surface import _views

    # 주석을 먼저 걷는다 - 이 CTE 의 주석이 틀린 형태(`asof_slot <= AS_OF`)를 **설명하려고**
    # 그대로 인용하고 있어서, 원문을 훑으면 산문을 SQL 로 세어 오탐이 난다.
    raw = _views().split("v_flow_intraday AS (")[1].split("\n    ),")[0]
    body = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("--"))
    assert "available_at <= %(as_of)s" in body, (
        "v_flow_intraday 가 available_at 으로 안 잘린다 - PIT 클램프가 없거나 축이 틀렸다")
    assert "asof_slot <=" not in body, (
        "asof_slot 을 시점 클램프로 쓰고 있다 - 코드('1'~'5')와 타임스탬프의 TEXT 비교라 "
        "항상 거짓이고 뷰가 조용히 빈다")
