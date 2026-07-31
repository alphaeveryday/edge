"""인과 경로 E2E — **실제 DB·실제 LLM 없이 전 경로를 고정한다.**

실물 경계(Postgres·DeepSeek)만 스텁으로 대체하고 산술→제안→구조→식별→추정→적합→서술
전부를 실제 코드로 통과시킨다. 여기서 잡고 싶은 것은 I/O 가 아니라 **계약**이다:

    비용 순서    가장 싼 게이트가 가장 먼저 돈다. 비중 0 이면 LLM 은 **호출되지 않는다**
    무날조       고객 문장의 모든 퍼센트는 스텁이 준 수치에서 유도된 것이어야 한다
    PIT          as_of 없는 코호트 조회는 스텁이 assert 로 죽는다 - 강제를 테스트가 지킨다
    빈 손        모델이 간선 0개를 내면 UNCERTAIN. 억지 설명을 만들지 않는다

실험판에서 LLM 이 보고한 수치는 날조였고, 잔차·비중 계산은 707초짜리 검정 **뒤에** 돌았다.
그 두 가지가 회귀하면 여기서 깨져야 한다.
"""
from __future__ import annotations

import json
import re
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from edge_analysis.adapters.llm import analyze
from edge_analysis.causal import agents
from edge_analysis.causal.run import explain
from edge_analysis.config import PipelineError
from edge_analysis.domain.models import (
    Decomposition,
    EventContext,
    Explanation,
    Holding,
    Member,
    PriceTrigger,
)
from edge_analysis.pipeline import run

TRADE_DATE = date(2026, 7, 16)
AS_OF = "2026-07-16T15:40:00+09:00"
ETF_INSTRUMENT = "inst_ETF"
ETF_NAME = "테스트 반도체 ETF"
ETF_TICKER = "091160"
EVENT_ID = "evt_1"
EVENT_TYPE = "COMPANY.CAPITAL.DIVIDEND_DECISION"
CAUSE_LABEL = "삼성전자 분기 배당 확대 결정"

# 스텁이 공급하는 **유일한 수치 원천.** 고객 문장의 퍼센트는 전부 여기서 유도돼야 한다.
OBSERVED = 0.0500        # ETF 당일 등락 (decomposition.proxy_ret)
RESIDUAL = 0.0421        # ETF 당일 시장대비 초과수익 (cd.ar 이 돌려주는 값)
EFFECT = 0.0600          # 처치군에만 심은 효과
NOISE_SD = 0.0120        # 대조군은 잡음만
SHARE = 0.2400           # ETF 내 처치 종목 비중
ABS_MAX = 0.3930         # 타입 과거 |초과수익| 최대 (산술 게이트의 상한)
CONTRIBUTORS = [("삼성전자", 0.0312), ("SK하이닉스", 0.0089)]

_TREATED_IDS = ("inst_T1", "inst_T2", "inst_T3", "inst_T4")
_CONTROL_IDS = tuple(f"inst_C{i:02d}" for i in range(20))
_EVENT_DATES = (date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15))


def _day(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


# --------------------------------------------------------------------------- #
# 스텁 — 실물 경계(Postgres·LLM·S3)만 대체한다
# --------------------------------------------------------------------------- #
class FakeCausalData:
    """`adapters.causal_data.CausalData` 계약의 결정론 스텁.

    처치군에만 효과를 심고 대조군은 잡음만 준다 - 검정이 **효과를 찾는지**가 아니라
    효과가 없을 때 **찾지 않는지**까지 같은 스텁으로 고정하려고 effect 를 인자로 둔다.
    난수는 생성 시점에 한 번 뽑아 표에 넣는다(호출 순서가 값을 바꾸면 테스트가 흔들린다).
    """

    def __init__(self, *, effect: float = EFFECT, share: float | None = SHARE,
                 residual: float = RESIDUAL, seed: int = 7) -> None:
        rng = np.random.default_rng(seed)
        self.treated = [(i, d) for d in _EVENT_DATES for i in _TREATED_IDS]
        self.control = [(i, d) for d in _EVENT_DATES for i in _CONTROL_IDS]
        self._share = share
        self._ar = {(ETF_INSTRUMENT, TRADE_DATE): residual}
        for p in self.treated:
            self._ar[p] = float(rng.normal(0.0, NOISE_SD)) + effect
        for p in self.control:
            self._ar[p] = float(rng.normal(0.0, NOISE_SD))
        pairs = self.treated + self.control
        self._mom = {p: float(rng.normal(0.0, 0.02)) for p in pairs}
        self._vol = {p: float(abs(rng.normal(0.01, 0.003))) for p in pairs}
        # 어느 표면이 어떤 순서로 불렸나. 비용 순 게이트 계약의 증거다.
        self.calls: list[str] = []

    # ── 코호트 ──────────────────────────────────────────────────────────
    def cohort(self, where: str, *, as_of: str, w0=None, w1=None, limit: int = 20000):
        # PIT 는 협상 대상이 아니다. 한 단어를 빠뜨리면 미래를 보는데 그건 사후에
        # 탐지되지 않는다 - 결과가 그냥 좋아진다. 그래서 스텁이 먼저 죽는다.
        assert as_of, "as_of 없이 코호트를 만들 수 없다 (PIT)"
        assert "available_at" not in where and ";" not in where, f"술어가 오염됐다: {where}"
        self.calls.append("cohort")
        if "event_type_code" not in where:
            return []          # 사건 기반이 아닌 술어는 처치군을 만들 수 없다
        return [(i, d) for i, d in self.treated
                if (w0 is None or _day(w0) <= d <= _day(w1))]

    def universe(self, where: str, dates, *, exclude=None, limit: int = 80000):
        self.calls.append("universe")
        want = {_day(d) for d in dates}
        ex = {(i, str(d)[:10]) for i, d in (exclude or ())}
        return [(i, d) for i, d in self.control
                if d in want and (i, d.isoformat()) not in ex]

    # ── 정렬된 열 (입력 순서를 지킨다. 없으면 nan) ───────────────────────
    def _col(self, pairs, table: dict) -> np.ndarray:
        return np.array([table.get((str(i), _day(d)), np.nan) for i, d in pairs], dtype=float)

    def ar(self, pairs, **kw) -> np.ndarray:
        self.calls.append("ar")
        return self._col(pairs, self._ar)

    def mom(self, pairs, **kw) -> np.ndarray:
        self.calls.append("mom")
        return self._col(pairs, self._mom)

    def vol(self, pairs, **kw) -> np.ndarray:
        self.calls.append("vol")
        return self._col(pairs, self._vol)

    # ── 크기 정합 ───────────────────────────────────────────────────────
    def weight(self, etf_instrument_id: str, trade_date: date, units=None) -> dict:
        self.calls.append("weight")
        out: dict = {"n_hold": len(_TREATED_IDS) + len(_CONTROL_IDS), "total_raw": 1.0}
        if units is None:
            return out
        # 결측과 0 은 다르다 - share=None 을 그대로 흘린다.
        out["members"] = {u: (self._share or 0.0) / len(units) for u in units}
        out["share"] = self._share
        return out

    def required_effect(self, residual: float, share: float | None) -> float | None:
        return None if not share else residual / share

    # ── 타입 사전 (분포 사실. 검정이 아니다) ────────────────────────────
    def type_population(self, event_type_code: str) -> dict:
        return {"events": 240, "instruments": 96, "dates": 180,
                "first": "2024-01-02", "last": "2026-07-10", "effective_n": 96}

    def prior(self, event_type_code: str, *, need: float | None = None,
              min_cross: int = 50) -> dict:
        self.calls.append("prior")
        out = {"type": event_type_code, "n": 240, **self.type_population(event_type_code),
               "up_ratio": 0.58, "abs_q50": 0.021, "abs_q75": 0.035, "abs_q90": 0.062,
               "abs_max": ABS_MAX}
        if need is not None:
            out.update(need=abs(need), n_at_least=3, freq_at_least=3 / 240)
        return out


class FakeClient:
    """제안 LLM 스텁. **호출 횟수가 계약이다** - 산술로 죽은 셀은 여기 오지 않는다."""

    def __init__(self, proposal: dict) -> None:
        self._proposal = proposal
        self.calls = 0
        self.briefs: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        # 인과 경로는 설계만 묻는다. 다른 프롬프트가 오면 경로가 갈렸다는 뜻이다.
        assert system == agents.SYSTEM, "인과 경로가 아닌 프롬프트로 호출됐다"
        self.calls += 1
        self.briefs.append(user)
        return self._proposal


class LegacyClient:
    """이전 단일 프롬프트 경로용 스텁(causal_enabled=False 확인)."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.systems.append(system)
        return {"verdict": "시장·섹터 주도", "explain": "시장 전반이 밀어올렸습니다."}


# --------------------------------------------------------------------------- #
# 제안 픽스처 — 모델이 낼 수 있는 것만 낸다(수치 없음)
# --------------------------------------------------------------------------- #
_TREATED_WHERE = (f"event_type_code = '{EVENT_TYPE}' AND role_code = 'ISSUER'"
                  " AND lifecycle_stage = 'CONFIRMED'")
_CONTROL_WHERE = "listing_market = 'KOSPI' AND industry_name = 'Semiconductors'"


def _nodes(shock: str = "DIVIDEND@t+0") -> dict:
    return {
        shock: {"kind": "SHOCK", "unit": "stock", "measure": "배당 확대 결정 공시",
                "member_events": [EVENT_ID], "tau": "t+0"},
        "AR@t+0": {"kind": "TARGET", "unit": "stock", "measure": "당일 시장대비 초과수익"},
    }


def _edge(shock: str = "DIVIDEND@t+0") -> dict:
    return {"from": shock, "to": "AR@t+0", "kind": "directed",
            "cause_label": CAUSE_LABEL,
            "treated": _TREATED_WHERE, "control": _CONTROL_WHERE,
            "strata": "date", "scope": "type", "timing": "unscheduled",
            "because": "배당 확대는 주주환원 기대를 직접 올린다",
            "false_if": "같은 날 지수 편입 변경이 있었다면 죽는다"}


PROPOSAL_OK = {"nodes": _nodes(), "edges": [_edge()], "missing": []}
# 시간 역행: 원인이 결과보다 늦다. 구조 게이트(무료)가 추정 전에 잡아야 한다.
PROPOSAL_BACKWARDS = {"nodes": _nodes("DIVIDEND@t+1"), "edges": [_edge("DIVIDEND@t+1")],
                      "missing": []}
# 억지 설계는 UNCERTAIN 보다 나쁘다 - 못 찾으면 빈 목록.
PROPOSAL_EMPTY = {"nodes": _nodes(), "edges": [], "missing": ["장중 체결 흐름(수급) 원장"]}
# 계약 위반: treated 가 비었다. 2026-07-29·07-30 런을 죽인 실제 산출 모양이다
# (`간선 N 에 treated 가 없다`) - agents.parse 가 PipelineError 로 거부한다.
PROPOSAL_NO_TREATED = {"nodes": _nodes(), "edges": [{**_edge(), "treated": ""}], "missing": []}
# 형태 붕괴: 간선이 객체가 아니다. 정규화 안 하면 `e.get` 이 AttributeError 를 내는데,
# 그건 PipelineError 가 아니라 되먹임이 못 알아보고 런이 죽는다.
PROPOSAL_EDGE_NOT_OBJECT = {"nodes": _nodes(), "edges": [None], "missing": []}


class SequenceClient:
    """호출마다 다른 산출을 주는 제안 스텁.

    되먹임 재시도는 1·2회차 산출이 **달라야** 관찰된다 - FakeClient 는 같은 값을 반복해
    "재시도가 실제로 일어났는가"를 구별하지 못한다.
    """

    def __init__(self, *proposals: dict) -> None:
        self._proposals = list(proposals)
        self.calls = 0
        self.briefs: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        assert system == agents.SYSTEM, "인과 경로가 아닌 프롬프트로 호출됐다"
        out = self._proposals[min(self.calls, len(self._proposals) - 1)]
        self.calls += 1
        self.briefs.append(user)
        return out


def _candidates(share: float | None) -> list[dict]:
    return [{"event_type_code": EVENT_TYPE, "label": CAUSE_LABEL,
             "event_date": "2026-07-15", "ticker": "005930",
             "instrument_id": _TREATED_IDS[0], "share": share,
             "prior": FakeCausalData().prior(EVENT_TYPE)}]


def _explain(cd: FakeCausalData, client, *, candidates) -> dict:
    return explain(cd, client, etf_name=ETF_NAME, etf_instrument_id=ETF_INSTRUMENT,
                   trade_date=TRADE_DATE, as_of=AS_OF, observed=OBSERVED,
                   route_code="CONCENTRATED", contributors=CONTRIBUTORS,
                   candidates=candidates, grounded={EVENT_ID})


# --------------------------------------------------------------------------- #
# 수치 무날조 감사
# --------------------------------------------------------------------------- #
_PCT = re.compile(r"[+-]?\d+(?:\.\d+)?%")


def _pcts(text: str) -> set[str]:
    return {m.group() for m in _PCT.finditer(text)}


def _allowed(raw: dict) -> set[str]:
    """스텁이 준 수치에서 **유도 가능한** 퍼센트 표기 전부.

    narrate 의 서식(`{x*100:+.2f}%`·부호 없는 비중)을 그대로 재현한다. 본문에 이 집합
    밖의 퍼센트가 하나라도 있으면 어딘가에서 수치가 만들어진 것이다.
    """
    vals = [OBSERVED, RESIDUAL, *(c for _, c in CONTRIBUTORS)]
    for f in raw["causal"]["survived"]:
        vals += [v for v in (f["effect"], f["contribution"], f["share"]) if v is not None]
    out = set()
    for v in vals:
        out.add(f"{v * 100:+.2f}%")
        out.add(f"{v * 100:.2f}%")
    # 산술 게이트 문장이 쓰는 서식(필요 초과수익·타입 과거 최대)
    if SHARE:
        out.add(f"{abs(RESIDUAL) / SHARE * 100:.0f}%")
    out.add(f"{ABS_MAX * 100:.1f}%")
    return out


# --------------------------------------------------------------------------- #
# 1 정상 — 효과가 심긴 설계는 검정을 통과하고 원인으로 게시된다
# --------------------------------------------------------------------------- #
def test_planted_effect_survives_and_is_published_as_the_cause():
    cd, client = FakeCausalData(), FakeClient(PROPOSAL_OK)

    raw = _explain(cd, client, candidates=_candidates(SHARE))
    ex = Explanation(raw)

    assert ex.is_valid
    assert ex.explanation_type == "EVENT_SUPPORTED"
    assert client.calls == 1                      # 제안은 1회. 재시도는 클라이언트 소관
    survived = raw["causal"]["survived"]
    assert [f["cause"] for f in survived] == [CAUSE_LABEL]
    assert survived[0]["p"] < 0.05
    assert survived[0]["n"] == len(cd.treated) + len(cd.control)
    # 심은 효과를 되찾는다. 스텁이 준 것 말고 다른 수치가 나오면 계약이 깨진 것이다.
    assert survived[0]["effect"] == pytest.approx(EFFECT, abs=3 * NOISE_SD)
    assert survived[0]["share"] == SHARE
    assert survived[0]["contribution"] == pytest.approx(SHARE * survived[0]["effect"])
    # 본문은 관측·잔차를 그대로 말한다.
    body = raw["explain"]
    assert f"{OBSERVED * 100:+.2f}%" in body
    assert f"{RESIDUAL * 100:+.2f}%" in body
    assert "원인으로 확인됐습니다" in body


def test_published_body_invents_no_number():
    """본문의 모든 퍼센트가 스텁이 준 값에서 유도된 것이어야 한다.

    실험판 날조는 전부 **모델이 수치를 말할 자리**에서 났다. 자리를 없앤 것이 설계이고,
    이 테스트가 그 설계의 회귀 감시다.
    """
    raw = _explain(FakeCausalData(), FakeClient(PROPOSAL_OK), candidates=_candidates(SHARE))

    found = _pcts(raw["explain"])
    # 감사가 공허해지지 않게: 셀 수 있는 수치가 실제로 본문에 있어야 한다
    # (관측·잔차·기여 2건·비중·효과·설명폭).
    assert len(found) >= 6, found

    assert not found - _allowed(raw), f"원장에 없는 수치: {sorted(found - _allowed(raw))}"


def test_the_brief_carries_the_type_population_not_a_verdict():
    """프롬프트에는 분포 사실만 싣는다 - 모델이 수치를 물어볼 자리가 없어야 한다."""
    client = FakeClient(PROPOSAL_OK)

    _explain(FakeCausalData(), client, candidates=_candidates(SHARE))

    brief = client.briefs[0]
    assert "타입 모집단" in brief and "유효n≈96" in brief
    assert f"최대 {ABS_MAX * 100:.1f}%" in brief
    assert "p=" not in brief          # p값을 보여주면 모델이 그걸 베낀다


# --------------------------------------------------------------------------- #
# 2 산술 기각 — 가장 싼 게이트가 가장 먼저 돈다
# --------------------------------------------------------------------------- #
def test_zero_weight_candidate_dies_before_the_llm_is_ever_called():
    cd, client = FakeCausalData(share=0.0), FakeClient(PROPOSAL_OK)

    raw = _explain(cd, client, candidates=_candidates(0.0))

    assert client.calls == 0, "산술로 죽을 셀에 LLM 비용을 썼다"
    assert "cohort" not in cd.calls, "검정 표본을 뽑았다 - 순서가 거꾸로다"
    assert Explanation(raw).explanation_type == "UNCERTAIN"
    rejected = raw["causal"]["rejected"]
    assert [f["cause"] for f in rejected] == [CAUSE_LABEL]
    assert "비중이 없어" in rejected[0]["killed_by"]
    assert "확인되지 않았습니다" in raw["explain"]


def test_arithmetic_rejection_body_invents_no_number():
    raw = _explain(FakeCausalData(share=0.0), FakeClient(PROPOSAL_OK),
                   candidates=_candidates(0.0))

    assert not _pcts(raw["explain"]) - _allowed(raw)


def test_required_effect_beyond_the_type_maximum_dies_on_arithmetic():
    """비중이 있어도 필요 초과수익이 타입 과거 최대를 넘으면 통계를 볼 필요가 없다."""
    tiny = 0.052            # 실측 사례: 비중 5.20% 로 잔차를 설명하려면 +81% 가 필요했다
    client = FakeClient(PROPOSAL_OK)

    raw = _explain(FakeCausalData(share=tiny), client, candidates=_candidates(tiny))

    assert client.calls == 0
    killed = raw["causal"]["rejected"][0]["killed_by"]
    assert f"{abs(RESIDUAL) / tiny * 100:.0f}%" in killed
    assert f"{ABS_MAX * 100:.1f}%" in killed


# --------------------------------------------------------------------------- #
# 3 빈 제안 — 억지 설명을 만들지 않는다
# --------------------------------------------------------------------------- #
def test_empty_proposal_yields_uncertain_without_a_forced_story():
    cd, client = FakeCausalData(), FakeClient(PROPOSAL_EMPTY)

    raw = _explain(cd, client, candidates=_candidates(SHARE))

    assert client.calls == 1
    assert Explanation(raw).explanation_type == "UNCERTAIN"
    assert raw["confidence"] == "보류"
    assert raw["causal"]["survived"] == []
    assert "cohort" not in cd.calls, "간선이 없는데 표본을 뽑았다"
    assert "원인으로 확인됐습니다" not in raw["explain"]
    assert "확인되지 않았습니다" in raw["explain"]
    # 무엇이 없어서 못 했는지는 남긴다 - 조용히 넘어가지 않는다.
    assert "장중 체결 흐름(수급) 원장" in raw["causal"]["missing"]
    assert "장중 체결 흐름(수급) 원장" in raw["explain"]


# --------------------------------------------------------------------------- #
# 4 구조 위반 — 추정에 들어가지 않고, 사유를 돌려주고 한 번만 다시 묻는다
# --------------------------------------------------------------------------- #
def test_time_reversed_edge_is_rejected_before_estimation():
    """구조 검사는 무료다. 그래서 코호트 조회 **전에** 돌고, 위반은 되먹임으로 돌아간다.

    되먹임 상한은 1회(총 제안 2회). 같은 위반을 두 번 내면 억지로 밀지 않는다 -
    에이전트는 도구가 없어 자기 술어를 미리 검증할 수 없으므로, 사유를 주는 것이
    도구를 주는 것보다 싸다.
    """
    cd, client = FakeCausalData(), FakeClient(PROPOSAL_BACKWARDS)

    raw = _explain(cd, client, candidates=_candidates(SHARE))

    assert client.calls == 2, "구조 위반은 사유를 돌려주고 한 번 더 물어야 한다"
    violations = raw["causal"]["local_violations"]
    assert any("시간 역행" in v for v in violations), violations
    assert raw["causal"]["survived"] == []
    assert "cohort" not in cd.calls, "구조가 깨진 설계로 검정을 돌렸다"
    assert Explanation(raw).explanation_type == "UNCERTAIN"


# --------------------------------------------------------------------------- #
# 5 검정 실패 — 효과가 없으면 아무것도 살아남지 않는다
# --------------------------------------------------------------------------- #
def test_zero_effect_design_finds_nothing_and_says_so():
    cd, client = FakeCausalData(effect=0.0), FakeClient(PROPOSAL_OK)

    raw = _explain(cd, client, candidates=_candidates(SHARE))

    assert client.calls == 1
    assert raw["causal"]["survived"] == []
    rejected = raw["causal"]["rejected"]
    assert [f["cause"] for f in rejected] == [CAUSE_LABEL]
    assert "우연과 구별되는 차이는 없었습니다" in rejected[0]["killed_by"]
    assert "우연과 구별되는 차이는 없었습니다" in raw["explain"]
    assert Explanation(raw).explanation_type == "UNCERTAIN"


# --------------------------------------------------------------------------- #
# 6 PIT — as_of 없는 조회는 스텁이 죽인다
# --------------------------------------------------------------------------- #
def test_cohort_without_as_of_is_an_assertion_failure():
    """PIT 강제를 테스트가 지킨다 - 시점 절이 빠지면 결과가 조용히 좋아진다."""
    cd = FakeCausalData()

    with pytest.raises(AssertionError):
        cd.cohort(_TREATED_WHERE, as_of="")


# --------------------------------------------------------------------------- #
# 7 pipeline.run 스모크 — 주입만 바꿔 두 경로를 각각 태운다
# --------------------------------------------------------------------------- #
_TRIGGER = PriceTrigger("pmt_1", OBSERVED, "abs", abs_gate=True, rel_gate=False)
_EVENT = EventContext(source_event_id=EVENT_ID, event_type_code=EVENT_TYPE,
                      available_at="2026-07-15T08:30:00+09:00",
                      entity_id=_TREATED_IDS[0], ticker="005930", thread_id="thr_1",
                      novelty_status="NEW", title="분기 배당 확대 결정")


def _settings(*, causal: bool) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=TRADE_DATE, request_id="req-causal-1", etf_ticker=ETF_TICKER,
        lake_bucket="test-lake",
        result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
        release_bundle_version="b1", causal_enabled=causal)


class _FakeLake:
    def load_holdings(self, etf_id, market, trade_date):
        return [Holding("005930", "삼성전자", 1.0)], "2026-07-15"

    def load_returns(self, market, trade_date):
        return {"005930": OBSERVED}


class _FakeStore:
    """트리거·전제는 있는 날. `causal_data()` 가 스텁을 돌려준다."""

    def __init__(self, cd: FakeCausalData) -> None:
        self._cd = cd
        self.calls: list[str] = []
        self.persisted: Explanation | None = None

    def load_entity_index(self):
        return {"005930": _TREATED_IDS[0]}

    def resolve_etf_instrument(self, ticker):
        return (ETF_INSTRUMENT, ETF_NAME)

    def fetch_price_trigger(self, etf_instrument_id, trade_date):
        return _TRIGGER

    def persist_observation_route(self, trigger_id, decomp, route_code, event_search,
                                 entity_index):
        return {"trigger_id": trigger_id, "obs_id": "cob_1", "route_id": "rte_1"}

    def fetch_event_contexts(self, trade_date, tickers):
        return [_EVENT]

    def explanation_prerequisites(self, settings, etf_instrument_id):
        return {"profile": True, "route": "rte_1", "bundle": "b1"}

    def persist_explanation(self, settings, etf_instrument_id, explanation, **kwargs):
        self.calls.append("persist_explanation")
        self.persisted = explanation
        return {"persisted": "rds", "explanation_result_id": "res_1", "run_id": "run_1"}

    def causal_data(self):
        self.calls.append("causal_data")
        return self._cd


class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


def _archives(s3: _FakeS3) -> list[dict]:
    return [json.loads(p["Body"].decode("utf-8")) for p in s3.puts]


def test_run_takes_the_causal_route_and_persists_the_explanation():
    cd, s3 = FakeCausalData(), _FakeS3()
    store, client = _FakeStore(cd), FakeClient(PROPOSAL_OK)

    code = run(_settings(causal=True), lake=_FakeLake(), store=store, client=client, s3=s3)

    assert code == 0
    assert "causal_data" in store.calls and "persist_explanation" in store.calls
    assert client.calls == 1
    assert store.persisted.explanation_type == "EVENT_SUPPORTED"
    archived = [a for a in _archives(s3) if a.get("outcome") == "explained"]
    assert archived, [a.get("outcome") for a in _archives(s3)]
    # 런 아카이브는 DB 매핑이 버리는 감사 필드(잔차·기각 사유)를 보존한다.
    assert archived[0]["explanation"]["causal"]["residual"] == RESIDUAL


def test_run_falls_back_to_the_prompt_route_when_causal_is_disabled():
    """산업분류 백필 전에는 ops 가 이전 경로를 고를 수 있어야 한다(조용히 빈 설명 대신)."""
    cd, s3 = FakeCausalData(), _FakeS3()
    store = _FakeStore(cd)
    client = LegacyClient()

    code = run(_settings(causal=False), lake=_FakeLake(), store=store, client=client, s3=s3)

    assert code == 0
    assert "causal_data" not in store.calls
    assert cd.calls == [], "인과 경로가 꺼졌는데 저장소를 두드렸다"
    assert client.systems and agents.SYSTEM not in client.systems
    assert store.persisted.explanation_type == "MIXED"


def test_analyze_signature_still_routes_by_the_causal_argument():
    """`analyze` 시그니처는 고정이다 - causal 인자 하나가 경로를 정한다.

    후보의 비중은 **분해 결과에서** 온다 - 구성종목 비중이 없으면 산술 게이트가 먼저
    죽이므로(그게 계약이다) 여기서는 무게 있는 구성종목을 준다.
    """
    member = Member("005930", "삼성전자", SHARE, OBSERVED, SHARE * OBSERVED, 1)
    decomp = Decomposition(members=[member], proxy_ret=OBSERVED, covered_weight=SHARE,
                           total_weight=1.0, coverage=SHARE, top1=SHARE * OBSERVED,
                           top3=SHARE * OBSERVED, advancing=1, total_priced=1,
                           n_constituents=1)
    client = FakeClient(PROPOSAL_OK)

    ex = analyze(client, etf_ticker=ETF_TICKER, etf_name=ETF_NAME,
                 name_by_ticker={"005930": "삼성전자"}, trade_date=TRADE_DATE,
                 decomp=decomp, gate=_TRIGGER, route_code="CONCENTRATED",
                 events=[_EVENT], causal=FakeCausalData(),
                 etf_instrument_id=ETF_INSTRUMENT)

    assert client.calls == 1
    assert ex.explanation_type == "EVENT_SUPPORTED"


# --------------------------------------------------------------------------- #
# 계약 위반 산출 — 되먹임 대상이지 런 사망 사유가 아니다 (ALPHA-633)
# --------------------------------------------------------------------------- #
def test_broken_proposal_is_fed_back_and_retried():
    """1회차가 계약을 어기면 사유를 되먹여 다시 묻는다 - 지금까지는 재시도조차 없었다.

    WHY: 2026-07-30 스케줄 런(`etf-daily-2026-07-30T15-40`)이 `간선 8 에 treated 가 없다`
    로 **1회차에서** 죽었다. AnalyzeOne 하나가 예외로 끝나면 analyze 전량성공
    게이트(ADR-0028)가 유니버스 33종 런을 통째로 FAILED 시킨다. 구조 위반·빈 코호트는
    되먹여 다시 묻는데 계약 위반만 죽이던 비대칭을 여기서 고정한다.
    """
    cd = FakeCausalData()
    client = SequenceClient(PROPOSAL_NO_TREATED, PROPOSAL_OK)

    raw = _explain(cd, client, candidates=_candidates(SHARE))

    assert client.calls == 2, "계약 위반이 되먹임 재질의를 못 만들었다"
    assert "treated" in client.briefs[1], "2회차 프롬프트에 위반 사유가 안 실렸다"
    # 2회차가 성사되면 결과는 정상 경로와 같아야 한다 - 강등이 아니라 회복이다.
    ex = Explanation(raw)
    assert ex.explanation_type == "EVENT_SUPPORTED"
    assert [f["cause"] for f in raw["causal"]["survived"]] == [CAUSE_LABEL]


def test_two_broken_proposals_degrade_instead_of_killing_the_run():
    """2회차도 계약을 어기면 **예외 없이** 강등된다 - 구조 위반 2연속과 같은 결말.

    WHY: 억지 설명을 만들지 않되 런은 살려야 한다. 여기서 예외가 새어 나가면 그 한 종목이
    유니버스 전체를 FAILED 로 만든다(2026-07-29·07-30 런이 그랬다).
    """
    cd = FakeCausalData()
    client = SequenceClient(PROPOSAL_NO_TREATED, PROPOSAL_NO_TREATED)

    raw = _explain(cd, client, candidates=_candidates(SHARE))   # 예외가 나면 여기서 깨진다

    assert client.calls == 2
    assert Explanation(raw).explanation_type == "UNCERTAIN"
    # 왜 설계를 못 세웠는지가 남아야 한다 - 건수만 남기면 사후에 원인을 못 가린다.
    assert any("treated" in v for v in raw["causal"]["local_violations"])


def test_transport_error_still_fails_loud():
    """LLM 전송·API 오류는 여전히 전파된다 - 계약 위반만 되먹임으로 돌린다.

    WHY: `adapters/llm.py` 의 DeepSeek 클라이언트는 402·타임아웃·응답 붕괴를 재시도 소진
    후 **PipelineError** 로 올린다 - 계약 위반과 **같은 타입**이다. 그래서 propose 까지
    한 try 로 감싸면 소스가 죽은 것을 모델이 계약을 어긴 것으로 오인해 UNCERTAIN 설명과
    함께 런이 초록으로 끝난다. 2026-07-27 에 tag_news 가 402 를 삼켜 940/940 전건 실패에도
    exit 0 이었던 그 사고다(ALPHA-589). 운영과 같은 타입으로 던져야 이 가드가 실물이다.
    """
    class _DeadClient:
        calls = 0

        def complete_json(self, system: str, user: str) -> dict:
            # adapters/llm.py:76 이 실제로 내는 것과 같은 타입·같은 모양이다.
            raise PipelineError("DeepSeek call failed after retries: HTTP Error 402")

    with pytest.raises(PipelineError, match="402"):
        _explain(FakeCausalData(), _DeadClient(), candidates=_candidates(SHARE))


def test_malformed_edge_shape_is_also_fed_back_not_raised():
    """형태가 무너진 산출도 되먹임 대상이다 - 타입이 갈리면 되먹임이 못 알아본다.

    WHY: `agents.parse` 가 결측 필드만 PipelineError 로 정규화하고 `edges: [null]` 같은
    형태 붕괴는 `AttributeError` 로 흘리면, run.explain 의 `except PipelineError` 를
    그대로 지나쳐 AnalyzeOne 이 죽고 유니버스 전체 런이 FAILED 된다. 게이트가 거르는
    모든 위반은 **한 타입으로** 나와야 호출부가 다룰 수 있다.
    """
    cd = FakeCausalData()
    client = SequenceClient(PROPOSAL_EDGE_NOT_OBJECT, PROPOSAL_OK)

    raw = _explain(cd, client, candidates=_candidates(SHARE))   # AttributeError 면 여기서 깨진다

    assert client.calls == 2, "형태 붕괴가 되먹임 재질의를 못 만들었다"
    assert Explanation(raw).explanation_type == "EVENT_SUPPORTED"
