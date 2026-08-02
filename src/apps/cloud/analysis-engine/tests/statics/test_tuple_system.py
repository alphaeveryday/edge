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

    def __init__(self, n=400, effect=0.02, seed=1, today=(1.0, 1.0), today_n=0,
                 today_z=(3.0, 0.5)):
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
        self.today_z = [today_z]

    def sql(self, q):
        if "SELECT z_ar, z_tv_chg" in q:
            return self.today_z                     # 오늘 계열 혁신 z (발화 판정)
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


def test_relation_transmission_edge_tests_but_never_assigns():
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
        vulnerabilities=(), trigger=Trigger("점", "COMPANY.PRODUCT.LAUNCH"),
        channel="Q수량", exposure=ExposureSource("관계", "SAME_INDUSTRY", hops=1),
        from_role="SUPPLIER", to_role="ISSUER", outcome="수익률", sign=1)
    r = edge_test(RelLake(), t, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict == "성립" and r.p < 0.05
    assert r.assignable is False and not r.applies_today       # 엣지만, 몫 배정 금지
    t2 = HypothesisTuple(vulnerabilities=(), trigger=Trigger("점", "X"), channel="C원가",
                         exposure=ExposureSource("관계", "SUPPLIES_TO", hops=1),
                         from_role="a", to_role="b", outcome="수익률", sign=1)
    assert "못 잰다" in edge_test(RelLake(), t2, "2026-06-01").reason


def test_grid_screen_sweeps_all_measurable_and_labels_two_sided():
    from edge_analysis.statics.paneltest import grid_screen

    class GridLake:
        """4피처 격자 스텁: 첫 피처(cum20)에만 진짜 효과."""

        def __init__(self, n=300, seed=4):
            rng = np.random.default_rng(seed)
            f = rng.normal(size=(n, 4))
            hi = f[:, 0] >= np.quantile(f[:, 0], 0.8)
            ar = 0.02 * hi + rng.normal(scale=0.004, size=n)
            d = [f"2026-0{1 + i % 5}-01" for i in range(n)]
            self.rows = [(f"i{k}", d[k], float(ar[k]), *map(float, f[k])) for k in range(n)]

        def sql(self, q):
            return self.rows

    hits = grid_screen(GridLake(), "2026-06-01", ["COMPANY.PRODUCT.LAUNCH"])
    assert hits and hits[0]["exposure"] == "가격잔차/누적"       # 심은 효과가 1위
    assert hits[0]["p2"] < 0.05 and hits[0]["direction"] == "+"
    assert all(h["p2"] <= 1.0 for h in hits if "p2" in h)       # 양측 보정 상한


def test_sem_magnitude_attached_only_when_passing():
    r = edge_test(_Lake(), T, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict == "성립" and r.contribution is not None
    assert r.ci_lo <= r.contribution <= r.ci_hi        # 점추정이 구간 안
    r2 = edge_test(_Lake(effect=0.0), T, "2026-06-01", cell_instrument_id="i0")
    assert r2.verdict == "불성립" and r2.contribution is None   # 게이트 탈락 = 크기 없음


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
    # 6차 라이브 실측: 취약성=노출 같은 피처 → INUS 내용 0 + 표본 파괴.
    taut = _h()
    taut["vulnerabilities"] = [{"family": "가격잔차", "transform": "누적",
                                "comparator": ">=", "percentile": 0.9}]  # 노출과 동일
    ok = _h(channel="FX환", ident="MARKET_STRUCTURE.INDEX.INCLUSION")
    ask = lambda s, u: {"hypotheses": [taut, ok, _h(channel="K위험")]}   # noqa: E731
    valid, rejected = propose(ask, facts="f", event_types=ETYPES)
    assert any("동어반복" in r for r in rejected)
    assert all(not (t.exposure.ident == v.family and t.exposure.transform == v.transform)
               for t in valid for v in t.vulnerabilities)


def test_thin_inus_falls_back_to_moderator_mode_not_undetermined():
    # §14: 충족 클래스가 얇으면 조건화(표본 분할) 대신 매개변수화 - 엣지는 전체
    # 패널로 검정하고 취약성은 조절 대비로, 오늘 적용은 여전히 충족을 요구한다.
    thin = _tuple(vuln_family="거래량", vuln_tr="수준", pct=0.97)   # 충족 ~3%
    r = edge_test(_Lake(n=400), thin, "2026-06-01", cell_instrument_id="i0")
    assert r.mode == "조절자" and r.verdict in ("성립", "불성립")   # 판정불가 전멸 탈출
    assert r.n == 400                                              # 전체 패널 검정력
    assert "조절" in r.moderation
    # 오늘 적용은 INUS 그대로: p0.97 임계를 오늘 못 넘으면 부적용.
    low_today = edge_test(_Lake(n=400, today=(1.0, -9.9)), thin, "2026-06-01",
                          cell_instrument_id="i0")
    assert low_today.vuln_satisfied is False and not low_today.applies_today


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
            {"vulnerabilities": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "계열", "ident": "수급"},          # 미발화 - 날조
             "channel": "Q수량", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "from_role": "a", "to_role": "b", "outcome": "수익률", "sign": 1,
             "reduction_note": "x"},
            {"vulnerabilities": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "계열", "ident": "가격잔차"},      # 발화 - 유효
             "channel": "K위험", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "from_role": "a", "to_role": "b", "outcome": "수익률", "sign": -1,
             "reduction_note": "y"}]}
    valid, rejected = propose(ask, facts="f", event_types=[],
                              measurable=[("가격잔차", "누적")],
                              series_families=["가격잔차"])
    assert [t.trigger.ident for t in valid] == ["가격잔차"]
    assert any("미발화 계열 방아쇠 날조" in r for r in rejected)


def test_gate_is_two_sided_with_cell_bonferroni():
    # 학술 수리 ②③ (17차): 방향 채굴 보상(양측 p₂) + 셀 단위 FWER(α/m).
    from edge_analysis.statics.paneltest import _two_sided
    assert _two_sided(0.03) == 0.06 and abs(_two_sided(0.97) - 0.06) < 1e-12  # 대칭
    assert _two_sided(0.5) == 1.0
    # 강한 합성 효과는 α/3 에서도 성립 - 검정력이 죽지 않는다.
    r3 = edge_test(_Lake(effect=0.03), _tuple(vuln_family="거래량", vuln_tr="수준"),
                   "2026-06-01", m_tests=3)
    assert r3.verdict == "성립"
    # p₂=0.04 는 m=1 성립, m=3(α=0.0167) 불성립 - Bonferroni 가 실제로 문다.
    from edge_analysis.statics.gates import edge_gate
    assert edge_gate(400, 0.04) == "성립"
    assert edge_gate(400, 0.04, alpha=0.05 / 3) == "불성립"


def test_agent_decisions_are_traced_with_raw_submissions():
    # 18R: 거부 사유가 stdout 3건·60자로 잘려 사라지던 것을 trace 로 영속화.
    # collect_trace 밖이면 record 는 no-op - 라이브러리 경로는 영향 없다.
    from edge_analysis.observability import collect_trace
    from edge_analysis.statics.hypothesize import propose
    def ask(system, user):
        return {"hypotheses": [
            {"vulnerabilities": [], "trigger": {"kind": "점", "ident": "지어낸타입"},
             "channel": "Q수량", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "from_role": "a", "to_role": "b", "outcome": "수익률", "sign": 1,
             "reduction_note": "x"},
            {"vulnerabilities": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "점", "ident": "REAL.TYPE"},
             "channel": "K위험", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "from_role": "a", "to_role": "b", "outcome": "수익률", "sign": -1,
             "reduction_note": "y"}]}
    with collect_trace() as tr:
        valid, rejected = propose(ask, facts="f", event_types=["REAL.TYPE"],
                                  measurable=[("가격잔차", "누적")])
    kills = [e for e in tr if e["event"] == "tuple.rejected"]
    oks = [e for e in tr if e["event"] == "tuple.accepted"]
    assert len(valid) == 1 and len(kills) >= 1
    # 유효<2 라 되물음 1회 - trace 가 두 턴을 다 보여준다 (어느 턴이 뭘 냈는지가 감사다).
    assert {e["turn"] for e in oks} == {1, 2}
    assert kills[0]["raw"]["trigger"]["ident"] == "지어낸타입"   # 원문 보존
    assert "날조" in kills[0]["why"]                             # 사유 전문(무절단)
    assert oks[0]["reduction_note"] == "y"
