"""튜플 체계(가설·검정 에이전트)의 계약 — 감사 5라운드의 교훈이 전부 단언이다.

가설: 어휘 밖·접지 밖·채널 중복은 생성 시점에 죽고, 되물음은 사유를 싣는다.
검정: 표본은 튜플에서 유도되고(조건 = INUS 조건화), 부재는 판정불가+사유이며,
같은 입력은 같은 판정(결정론). 성립해도 오늘 조건 미충족이면 부적용.
반사실은 positivity 를 갖출 때만 채워진다.
"""
import numpy as np
import pytest

from edge_analysis.statics.hypothesize import propose
from edge_analysis.statics.paneltest import MIN_OPPOSITE, edge_test
from edge_analysis.statics.vocab import (ExposureSource, HypothesisTuple, VocabError,
                                         MIN_N, Trigger, Condition)

ETYPES = ["COMPANY.PRODUCT.LAUNCH", "MARKET_STRUCTURE.INDEX.INCLUSION"]


def _h(channel="Q수량", ident="COMPANY.PRODUCT.LAUNCH", **kw):
    base = {"conditions": [{"family": "수급", "transform": "누적",
                                 "comparator": ">=", "percentile": 0.9}],
            "trigger": {"kind": "점", "ident": ident},
            "channel": channel,
            "exposure": {"kind": "속성", "ident": "가격잔차", "transform": "누적"},
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
        conditions=(Condition(vuln_family, vuln_tr, ">=", pct),),
        trigger=Trigger(*trigger), channel="Q수량",
        exposure=ExposureSource("속성", "가격잔차", transform="누적"),  outcome="수익률", sign=sign)


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
                        exposure=ExposureSource("속성", "거시", transform="변화"),  outcome="수익률", sign=-1)
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
        conditions=(), trigger=Trigger("점", "COMPANY.PRODUCT.LAUNCH"),
        channel="Q수량", exposure=ExposureSource("관계", "SAME_INDUSTRY", hops=1),  outcome="수익률", sign=1)
    r = edge_test(RelLake(), t, "2026-06-01", cell_instrument_id="i0")
    assert r.verdict == "성립" and r.p < 0.05
    assert r.assignable is False and not r.applies_today       # 엣지만, 몫 배정 금지
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
        outcome="수익률", sign=1)
    r = edge_test(LinkLake(), t, "2026-06-01", cell_instrument_id="i0")
    assert "v_link" in seen["q"] and "SUPPLY_CHAIN" in seen["q"]
    assert "industry_name = ce.industry_name" not in seen["q"]   # 대리가 아니라 관계
    assert "l.link_date <= ev.d" in seen["q"]                    # 홉도 시점으로 잘린다
    assert r.verdict == "판정불가"                                # 스텁이라 표본 0


def test_grid_screen_sweeps_all_measurable_and_labels_two_sided():
    from edge_analysis.statics.paneltest import grid_screen

    class GridLake:
        """격자 스텁: 첫 피처(cum20)에만 진짜 효과. 폭은 FEATURES 를 따라간다 -
        피처가 늘 때마다 스텁을 손보지 않으려면 여기서 세야 한다."""

        def __init__(self, n=300, seed=4):
            from edge_analysis.statics.paneltest import FEATURES
            rng = np.random.default_rng(seed)
            f = rng.normal(size=(n, len(FEATURES)))
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
             "outcome": "수익률", "sign": 1,
             "reduction_note": "x"},
            {"conditions": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "계열", "ident": "가격잔차"},      # 발화 - 유효
             "channel": "K위험", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "outcome": "수익률", "sign": -1,
             "reduction_note": "y"}]}
    valid, rejected = propose(ask, facts="f", event_types=[],
                              measurable=[("가격잔차", "누적")],
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
             "outcome": "수익률", "sign": 1,
             "reduction_note": "x"},
            {"conditions": [{"family": "거래량", "transform": "수준",
                                  "comparator": ">=", "percentile": 0.9}],
             "trigger": {"kind": "점", "ident": "REAL.TYPE"},
             "channel": "K위험", "exposure": {"kind": "속성", "ident": "가격잔차",
                                              "transform": "누적"},
             "outcome": "수익률", "sign": -1,
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
    from edge_analysis.statics.fsm import EMIT, GROUND, SCREEN, Machine

    class FakeCat:
        def __init__(self): self.seen = []
        def call(self, name, arg=""):
            self.seen.append(name)
            return {"cell": "셀 X", "coverage": "바인딩 35/35", "vocab": "채널 8",
                    "events": "사건 없음: 장중 사건이 하나도 없다",
                    "news": "미도달: document 은 2026-07-08 부터 적재됐다",
                    "screen": "  T × 거래량/변화 n=91 p₂=0.000 방향+"}[name]

    m = Machine(FakeCat())
    assert m.state == GROUND                                   # SCOPE 는 접혔다
    assert "셀 X" in m.brief() and "바인딩" in m.brief()        # 브리핑은 묻지 않고 준다
    assert "screen" not in m.menu()                            # 미래 도구는 안 보인다
    out = m.observe("screen")                                  # 상태 밖 호출
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
    assert "→ EMIT" in m.observe("screen") and m.done
    assert m.stats()["calls"] == ["GROUND:vocab()", "GROUND:events()",
                                  "GROUND:news()", "SCREEN:screen()"]


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
    assert kinds == {"hive", "glob", "ice", "csv"}          # 네 형식 전부 다룬다
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
from edge_analysis.statics.hypothesize import screen_tuples
from edge_analysis.statics.paneltest import FEATURES
from edge_analysis.statics.vocab import CONDITION_KINDS, SERIES_FAMILIES

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
    code = "\n".join(l for l in fin.splitlines() if not l.strip().startswith("--"))
    assert "as_of" not in code          # 수집일로 자르면 선견이 샌다


def test_rolling_betas_are_nan_guarded():
    # regr_slope 는 x 분산이 0 이면 NULL 이 아니라 **NaN** 을 낸다(산업 표본 <5 ·
    # fx 결측). NaN 은 pctile 을 조용히 오염시켜 상·하위 분할을 어긋나게 만든다 -
    # 부재는 NULL 로 말해야 판정자가 '못 잰다'로 읽는다. 실측: beta_s 364 → 129.
    from edge_analysis.statics.paneltest import _base
    sql = _base("2026-06-01")
    for col in ("beta_m", "beta_s", "fx_beta"):
        blk = sql[:sql.index(f"AS {col}")]
        assert blk.rstrip().endswith("END"), f"{col} 이 isfinite 가드 밖에 있다"
    assert sql.count("isfinite(regr_slope") == 3
