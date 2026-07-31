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
from edge_analysis.config import PipelineError
from edge_analysis.causal import agents
from edge_analysis.causal.run import explain
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

    # 층화·어휘 재료. `adapters/llm.py` 가 브리프에 실어 준다 - 없으면 모델이
    # industry_name 값을 한국어로 추측하고 대조군이 0건이 된다(#403).
    def industry_map(self, trade_date: date) -> dict[str, str]:
        self.calls.append("industry_map")
        return {i: "Semiconductors" for i in _TREATED_IDS + _CONTROL_IDS}

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
    """두 에이전트 스텁. **어느 프롬프트로 불렸는지가 계약이다.**

    제안(`agents.SYSTEM`)과 검정(`verify.SYSTEM` 을 포맷한 것)은 다른 세션이다. 검정
    세션에는 파이썬 코드를 돌려주고, 그 코드는 **실제로 샌드박스에서 실행된다** -
    여기서 잡고 싶은 것은 "모델이 수치를 만들 자리가 없다"는 계약이므로, 코드가 도구를
    타고 스텁 데이터에 닿는 경로 전체가 진짜여야 한다.
    """

    def __init__(self, proposal: dict | list[dict],
                 verify_turns: list[dict] | None = None) -> None:
        # 제안을 **여러 개** 줄 수 있다 - 조회 왕복(lookups)처럼 같은 프롬프트로 두 번
        # 이상 묻는 경로가 있고, 그때 무엇을 두 번째로 냈는지가 계약이다.
        self._proposals = list(proposal) if isinstance(proposal, list) else [proposal]
        self._verify = list(verify_turns if verify_turns is not None else VERIFY_TURNS)
        self.calls = 0
        self.proposals = 0
        self.verifies = 0
        self.briefs: list[str] = []
        self.verify_prompts: list[str] = []
        self._script: list[dict] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        if system == agents.SYSTEM:
            self.proposals += 1
            self.briefs.append(user)
            self._script = list(self._verify)      # 간선마다 대본을 처음부터
            i = min(self.proposals - 1, len(self._proposals) - 1)
            return self._proposals[i]
        # 검정 세션. 다른 프롬프트가 오면 경로가 갈렸다는 뜻이다.
        assert "R = {" in system and "간선" in system, "제안·검정 어느 프롬프트도 아니다"
        self.verifies += 1
        self.verify_prompts.append(system)
        return self._script.pop(0) if self._script else {"thought": "끝", "done": True}


# 검정 에이전트가 쓸 코드. **하네스가 실행한다** - 도구 이름·반환 모양이 틀리면 여기서 깨진다.
VERIFY_CODE = """
t = cohort("event_type_code = 'COMPANY.CAPITAL.DIVIDEND_DECISION'", w0='2026-05-01')
days = sorted({d for _, d in t})
c = universe("industry_name = 'Semiconductors'", days, exclude=t)
pairs = list(t) + list(c)
y = ar(pairs)
x = np.array([1.0] * len(t) + [0.0] * len(c))
blocks = np.array([str(d)[:10] for _, d in pairs])
keep = np.isfinite(y)
x, y, blocks = x[keep], y[keep], blocks[keep]

def beta(world):
    design = np.column_stack([np.ones(len(world['x'])), world['x']])
    return float(np.linalg.lstsq(design, y, rcond=None)[0][1])

res = placebo(beta, {'x': x}, permute(x, strata=blocks, n=200), null_kind='label')
R = {'x': x, 'y': y, 'z': {}, 'unit': 'stock', 'effect': beta({'x': x}),
     'test': res, 'null_kind': 'label', 'strata': blocks,
     'units': sorted({i for i, _ in t})}
print('n', len(y), 'effect', R['effect'], 'p', res['p'])
"""

VERIFY_TURNS = [{"thought": "타입 전체에서 대비를 쌓고 날짜 안에서 섞는다",
                 "code": VERIFY_CODE},
                {"thought": "R 완성", "done": True}]

# 데이터가 없어 못 재는 간선. **기각이 아니라 요청이다.**
VERIFY_IMPOSSIBLE = [{"thought": "장중 체결 흐름이 없으면 이 경로를 못 가른다",
                      "impossible": "수급 경로를 분리할 체결 흐름 원장이 없다",
                      "need": "장중 체결 흐름(투자자별 순매수) 일별 원장",
                      "grain": "일별",
                      "unlocks": "수급 주도와 사건 주도를 같은 셀에서 가를 수 있다"}]


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
    """사슬. **노드 종별을 선언하지 않는다** - 자연어(`says`)와 관측 여부만 든다."""
    return {
        shock: {"says": f"{CAUSE_LABEL} (배당총액 30% 증액)",
                "observed": "공시 원장", "events": [EVENT_ID], "value": [0.30, 0.30]},
        "PAYOUT@t+0": {"says": "주주환원 기대 상향 - 기대 배당수익률 변화폭",
                       "observed": None},
        "AR@t+0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률"},
    }


def _edge(shock: str = "DIVIDEND@t+0") -> list[dict]:
    """항등식·탄력성 대신 **통계 간선 하나**로 끝나는 가장 짧은 사슬.

    사슬을 길게 쓰는 것은 `test_chain.py` 가 따로 본다. 여기서는 파이프라인이 새 계약을
    관통하는지만 본다 - 짧은 사슬도 유효한 산출이어야 한다.
    """
    return [{"from": shock, "to": "PAYOUT@t+0", "kind": "elasticity",
             "says": "배당 증액이 기대 배당수익률을 올린다",
             "because": "배당총액 증액은 분자에 직접 들어간다",
             "false_if": "발행주식수가 같은 비율로 늘었다면 상쇄된다",
             "effect": [0.8, 1.0], "source": "직전 사업연도 배당총액·시가총액(공시)",
             "invariant_to": ["배당총액을 총액으로 보나 주당으로 보나"]},
            {"from": "PAYOUT@t+0", "to": "AR@t+0", "kind": "statistical",
             "says": "주주환원 기대 상향이 당일 초과수익을 만들었다",
             "because": "배당 확대는 주주환원 기대를 직접 올린다",
             "false_if": "같은 날 지수 편입 변경이 있었다면 죽는다",
             "exposure": _TREATED_WHERE, "reference": _CONTROL_WHERE,
             "invariant_to": ["참조집단을 같은 산업으로 잡나 같은 시장으로 잡나",
                              "반응을 당일로 재나 다음 거래일로 재나"]}]


PROPOSAL_OK = {"target": "AR@t+0", "nodes": _nodes(), "edges": _edge(), "missing": []}
# 시간 역행: 원인이 결과보다 늦다. 구조 게이트(무료)가 추정 전에 잡아야 한다.
PROPOSAL_BACKWARDS = {"target": "AR@t+0", "nodes": _nodes("DIVIDEND@t+1"),
                      "edges": _edge("DIVIDEND@t+1"), "missing": []}
# 억지 설계는 UNCERTAIN 보다 나쁘다 - 못 찾으면 빈 목록.
PROPOSAL_EMPTY = {"target": "AR@t+0", "nodes": _nodes(), "edges": [],
                  "missing": ["장중 체결 흐름(수급) 원장"]}


def _candidates(share: float | None) -> list[dict]:
    return [{"event_type_code": EVENT_TYPE, "label": CAUSE_LABEL,
             "event_date": "2026-07-15", "ticker": "005930",
             "instrument_id": _TREATED_IDS[0], "share": share,
             "prior": FakeCausalData().prior(EVENT_TYPE)}]


def _explain(cd: FakeCausalData, client, *, candidates, sandbox: bool = True,
             docs=None) -> dict:
    return explain(cd, client, etf_name=ETF_NAME, etf_instrument_id=ETF_INSTRUMENT,
                   trade_date=TRADE_DATE, as_of=AS_OF, observed=OBSERVED,
                   route_code="CONCENTRATED", contributors=CONTRIBUTORS,
                   candidates=candidates, grounded={EVENT_ID}, sandbox=sandbox,
                   docs=docs)


class FakeDocs:
    """도메인 문서 스텁. **질의를 그대로 기록한다** - 누가 무엇을 물었는지가 계약이다."""

    def __init__(self, hits: list[dict] | None = None) -> None:
        self.queries: list[str] = []
        self._hits = hits if hits is not None else [
            {"domain": "Technology/Semiconductors", "ticker": "000660", "ord": 22,
             "text": "웨이퍼는 일본·한국·독일·미국 주요 공급사로부터 300mm 완제품으로 "
                     "공급받으며 중장기 협력 관계를 맺는다"}]

    def search(self, query, *, domain=None, k=6):
        self.queries.append(query)
        return list(self._hits)


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
    assert client.proposals == 1                  # 제안은 1회. 나머지는 검정 세션이다
    assert client.verifies >= 1, "검정 에이전트가 불리지 않았다 - 샌드박스 경로가 죽었다"
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


def test_audit_block_carries_the_prose_the_ledger_and_the_agent_code():
    """감사 흔적이 없으면 **통과했다는 사실만 남고 통과의 증거가 사라진다.**

    사후에 "무엇을 무엇과 비교해서 이 p 가 나왔는가"를 재구성할 수 있어야 한다:
    설계(층화·조정집합)·산문(주장·메커니즘·반증조건)·원장(placebo 호출 전량)·코드.
    """
    raw = _explain(FakeCausalData(), FakeClient(PROPOSAL_OK), candidates=_candidates(SHARE))

    audit = raw["causal"]
    assert audit["status"] == "미확증(표본외 검정 없음)", "단일 패스에 확증이 있는 척했다"
    assert raw["confidence"] != "높음"
    proof = audit["proofs"][0]
    # 산문
    assert proof["say"] and proof["because"] and proof["false_if"]
    # 주장 층위와 허용 귀무 - 귀속(L4)에 date 귀무를 쓸 수 없다는 사실이 남는다
    assert proof["claims"] == "L4" and proof["null_ok"] == ["label"]
    assert proof["null_kind"] == "label"
    # 설계
    assert proof["strata_design"] == "date" and proof["strata_used"] is True
    assert proof["unit"] == "stock"
    # 원장과 코드 - 수치가 어디서 왔는지
    assert proof["n_placebo"] >= 1 and proof["ledger"][0]["testable"] is True
    assert proof["ledger"][0]["p"] == proof["p"]
    assert any("placebo(" in c for c in proof["code"])
    # 반증 표면은 그래프에서 열거된 것이다(손으로 쓰지 않는다)
    assert isinstance(audit["falsification_surface"], list)


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

    assert client.proposals == 1
    assert raw["causal"]["survived"] == []
    rejected = raw["causal"]["rejected"]
    assert [f["cause"] for f in rejected] == [CAUSE_LABEL]
    assert "우연과 구별되는 차이는 없었습니다" in rejected[0]["killed_by"]
    assert "우연과 구별되는 차이는 없었습니다" in raw["explain"]
    assert Explanation(raw).explanation_type == "UNCERTAIN"


# --------------------------------------------------------------------------- #
# 5b 못 잰 간선 — **기각이 아니라 데이터 요청이다**
# --------------------------------------------------------------------------- #
def test_untestable_edge_becomes_a_concrete_data_request():
    """데이터 부재는 침묵이 아니라 산출물이다.

    구조가 맞는데 잴 수 없는 간선은 무엇이 있어야 서는지 남긴다 - 그게 다음 수집
    의제가 된다. 지금 단계에서 중요한 것은 커버리지가 아니라 DAG 품질이다.
    """
    client = FakeClient(PROPOSAL_OK, verify_turns=VERIFY_IMPOSSIBLE)

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE))

    reqs = raw["causal"]["data_requests"]
    assert [q["need"] for q in reqs] == ["장중 체결 흐름(투자자별 순매수) 일별 원장"]
    assert reqs[0]["grain"] == "일별" and reqs[0]["unlocks"]
    assert reqs[0]["edge"] == "PAYOUT@t+0→AR@t+0"
    assert raw["causal"]["proofs"][0]["status"] == "불가"
    assert raw["causal"]["survived"] == []
    # 고객 문장도 "확인 안 됨"과 "자료가 없어 확인 못 함"을 구분한다.
    assert "자료가 없어 검정하지 못했습니다" in raw["causal"]["rejected"][0]["killed_by"]
    assert "확보하지 못한 자료" in raw["explain"]


def test_proposal_may_keep_an_edge_it_cannot_measure_yet():
    """저장소에 없는 것을 노드로 세워도 된다 - `needs` 가 요청 큐로 나간다."""
    edges = _edge()
    edges[-1] = {**edges[-1], "needs": "애널리스트 목표주가 시계열"}
    proposal = {"target": "AR@t+0", "nodes": _nodes(), "edges": edges, "missing": []}
    client = FakeClient(proposal)

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE))

    needs = [q["need"] for q in raw["causal"]["data_requests"]]
    assert "애널리스트 목표주가 시계열" in needs
    assert raw["causal"]["proofs"][0]["needs"] == "애널리스트 목표주가 시계열"
    # 요청을 남겼다고 검정을 건너뛰지는 않는다 - 세울 수 있는 데까지 민다.
    assert raw["causal"]["proofs"][0]["n"] > 0


# --------------------------------------------------------------------------- #
# 5c 도메인 문서 조회 — **모르는 것을 묻는 것과 구조를 틀리는 것은 다른 일이다**
# --------------------------------------------------------------------------- #
def test_the_proposal_can_ask_for_domain_knowledge_before_drawing_the_chain():
    """조회 요청은 유효한 산출이고, **시도 횟수를 쓰지 않는다.**

    사슬을 그리려면 산업 구조를 알아야 하는데, "먼저 그려라"는 요구는 추측으로 그리게
    만드는 요구다. 그래서 nodes 없이 lookups 만 낸 제안도 받고, 조회 결과를 붙여 다시
    묻는다. 그 왕복이 구조 위반 예산(2회)을 먹으면 모델이 묻기를 포기한다.
    """
    asked = {"lookups": ["반도체 원재료 공급사 구성과 계약 형태"]}
    client = FakeClient([asked, PROPOSAL_OK])       # 1턴 조회 요청 → 2턴 사슬
    docs = FakeDocs()

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE), docs=docs)

    assert docs.queries == ["반도체 원재료 공급사 구성과 계약 형태"]
    assert client.proposals == 2, "조회 후 다시 묻지 않았다"
    # 조회 결과가 되먹임으로 실제로 들어갔나 - 출처까지 붙어야 사후 확인이 된다.
    second = client.briefs[-1]
    assert "웨이퍼는 일본·한국·독일·미국" in second
    assert "000660#22" in second
    # 그리고 조회를 거쳐도 설명은 정상적으로 선다.
    assert [f["cause"] for f in raw["causal"]["survived"]] == [CAUSE_LABEL]


def test_without_a_document_store_the_proposal_proceeds_instead_of_stalling():
    """도메인 지식이 없다고 설명을 멈추지 않는다 - 저장소는 선택 의존이다."""
    client = FakeClient([{**PROPOSAL_OK, "lookups": ["알 수 없는 것"]}])

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE), docs=None)

    assert client.proposals == 1
    assert [f["cause"] for f in raw["causal"]["survived"]] == [CAUSE_LABEL]


def test_a_failing_document_store_does_not_break_the_explanation():
    """조회 실패는 설명 실패가 아니다 - 리전·자격증명 문제로 이쪽만 죽을 수 있다."""
    class Boom:
        def search(self, query, *, domain=None, k=6):
            raise RuntimeError("S3 접근 불가")

    client = FakeClient([{"lookups": ["무엇이든"]}, PROPOSAL_OK])

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE), docs=Boom())

    assert client.proposals == 2
    # 못 찾았다는 사실이 되먹임에 남아야 한다 - 그래야 모델이 추측하지 않는다.
    assert "못 찾았다" in client.briefs[-1]
    assert [f["cause"] for f in raw["causal"]["survived"]] == [CAUSE_LABEL]


def test_sandbox_off_falls_back_to_the_reduced_path_without_calling_the_verifier():
    """ops 킬스위치. 모델 코드를 실행하지 않고도 술어가 있는 간선은 검정된다."""
    cd, client = FakeCausalData(), FakeClient(PROPOSAL_OK)

    raw = _explain(cd, client, candidates=_candidates(SHARE), sandbox=False)

    assert client.verifies == 0, "샌드박스를 껐는데 검정 에이전트를 불렀다"
    survived = raw["causal"]["survived"]
    assert survived and survived[0]["p"] < 0.05
    assert raw["causal"]["proofs"][0]["turns"] == 0
    assert raw["causal"]["proofs"][0]["code"] == []


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


def _settings(*, causal: bool, sandbox: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=TRADE_DATE, request_id="req-causal-1", etf_ticker=ETF_TICKER,
        lake_bucket="test-lake",
        result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
        release_bundle_version="b1", causal_enabled=causal,
        causal_sandbox_enabled=sandbox,
        domain_docs_bucket="", domain_docs_profile="")


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
    assert client.proposals == 1 and client.verifies >= 1
    assert store.persisted.explanation_type == "EVENT_SUPPORTED"
    archived = [a for a in _archives(s3) if a.get("outcome") == "explained"]
    assert archived, [a.get("outcome") for a in _archives(s3)]
    # 런 아카이브는 DB 매핑이 버리는 감사 필드(잔차·설계·원장·기각 사유)를 보존한다.
    causal = archived[0]["explanation"]["causal"]
    assert causal["residual"] == RESIDUAL
    assert causal["proofs"][0]["ledger"], "아카이브에 원장이 안 남았다"


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


def test_sandbox_killswitch_reaches_the_verifier_through_run():
    """`CAUSAL_SANDBOX_ENABLED=false` 가 **실제로** 검정 세션을 끄는가.

    WHY: 킬스위치는 배선이 끝까지 닿아야 킬스위치다. settings 에만 있고 analyze 로
    안 넘어가면 ops 는 껐다고 믿는데 LLM 이 쓴 코드가 계속 실행된다 - 끈 줄 아는 스위치가
    가장 위험하다. 그래서 값이 아니라 **검정 세션 호출 수 0** 을 본다.
    """
    cd, s3 = FakeCausalData(), _FakeS3()
    store, client = _FakeStore(cd), FakeClient(PROPOSAL_OK)

    code = run(_settings(causal=True, sandbox=False), lake=_FakeLake(), store=store,
               client=client, s3=s3)

    assert code == 0
    assert client.proposals == 1, "제안은 그대로 돌아야 한다 - 샌드박스만 끈다"
    assert client.verifies == 0, "샌드박스를 껐는데 검정 세션이 불렸다"

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

    assert client.proposals == 1 and client.verifies >= 1
    assert ex.explanation_type == "EVENT_SUPPORTED"


# --------------------------------------------------------------------------- #
# 계약 위반 산출 — 되먹임 대상이지 런 사망 사유가 아니다 (ALPHA-633)
#
# 새 계약(kind=identity|elasticity|statistical, exposure/reference)으로 이식했다.
# 어휘는 갈렸지만 방어하는 결함은 같다: 계약 위반이 예외로 새면 AnalyzeOne 이 exit 1 이
# 되고 analyze 전량성공 게이트(ADR-0028)가 유니버스 전체 런을 FAILED 시킨다.
# --------------------------------------------------------------------------- #
# 통계 간선인데 exposure·needs 가 둘 다 없다 - 무엇을 잴지 안 적은 산출.
PROPOSAL_NO_EXPOSURE = {
    "target": "AR@t+0", "nodes": _nodes(),
    "edges": [_edge()[0], {k: v for k, v in _edge()[1].items() if k != "exposure"}],
    "missing": []}
# 형태 붕괴: 간선이 객체가 아니다. 타입이 갈리면 되먹임이 알아보지 못한다.
PROPOSAL_EDGE_NOT_OBJECT = {"target": "AR@t+0", "nodes": _nodes(),
                            "edges": [None], "missing": []}


def test_broken_proposal_is_fed_back_and_retried():
    """1회차가 계약을 어기면 사유를 되먹여 다시 묻는다.

    WHY: 2026-07-30 스케줄 런이 간선 하나의 필드 결측으로 **1회차에서** 죽어 유니버스
    33종을 통째로 FAILED 시켰다. 구조 위반·빈 코호트는 되먹여 다시 묻는데 계약 위반만
    죽이던 비대칭을 여기서 고정한다.
    """
    client = FakeClient([PROPOSAL_NO_EXPOSURE, PROPOSAL_OK])

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE))

    assert client.proposals == 2, "계약 위반이 되먹임 재질의를 못 만들었다"
    assert "exposure" in client.briefs[1], "2회차 프롬프트에 위반 사유가 안 실렸다"
    # 2회차가 성사되면 결과는 정상 경로와 같아야 한다 - 강등이 아니라 회복이다.
    assert Explanation(raw).explanation_type == "EVENT_SUPPORTED"


def test_two_broken_proposals_degrade_instead_of_killing_the_run():
    """2회차도 계약을 어기면 **예외 없이** 강등된다 - 구조 위반 2연속과 같은 결말."""
    client = FakeClient([PROPOSAL_NO_EXPOSURE, PROPOSAL_NO_EXPOSURE])

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE))

    assert client.proposals == 2
    assert Explanation(raw).explanation_type == "UNCERTAIN"
    # 왜 설계를 못 세웠는지가 남아야 한다 - 건수만 남기면 사후에 원인을 못 가린다.
    assert any("exposure" in v for v in raw["causal"]["local_violations"])


def test_transport_error_still_fails_loud():
    """LLM 전송·API 오류는 여전히 전파된다 - 계약 위반만 되먹임으로 돌린다.

    WHY: DeepSeek 클라이언트는 402·타임아웃·응답 붕괴를 재시도 소진 후 **PipelineError**
    로 올린다 - 계약 위반과 같은 타입이다. propose 까지 한 try 로 감싸면 소스가 죽은 것을
    계약 위반으로 오인해 UNCERTAIN 설명과 함께 런이 초록으로 끝난다(ALPHA-589).
    """
    class _DeadClient:
        def complete_json(self, system: str, user: str) -> dict:
            raise PipelineError("DeepSeek call failed after retries: HTTP Error 402")

    with pytest.raises(PipelineError, match="402"):
        _explain(FakeCausalData(), _DeadClient(), candidates=_candidates(SHARE))


def test_malformed_edge_shape_is_also_fed_back_not_raised():
    """형태가 무너진 산출도 되먹임 대상이다 - 타입이 갈리면 되먹임이 못 알아본다."""
    client = FakeClient([PROPOSAL_EDGE_NOT_OBJECT, PROPOSAL_OK])

    raw = _explain(FakeCausalData(), client, candidates=_candidates(SHARE))

    assert client.proposals == 2, "형태 붕괴가 되먹임 재질의를 못 만들었다"
    assert Explanation(raw).explanation_type == "EVENT_SUPPORTED"


@pytest.mark.parametrize("broken", [
    {"nodes": _nodes(), "edges": {}, "missing": []},                    # falsy 비목록
    {"nodes": _nodes(), "edges": _edge(), "missing": 0},                # falsy 비목록
    {"nodes": _nodes(), "edges": [None], "missing": []},                # 간선 형태 붕괴
    {"nodes": _nodes(), "edges": [{"to": "AR@t+0"}], "missing": []},    # from 결측
    {"nodes": _nodes(), "edges": [{**_edge()[1], "kind": "directed"}], "missing": []},
    {"nodes": _nodes(), "edges": [{**_edge()[1], "invariant_to": 1}], "missing": []},
    {"nodes": _nodes(), "edges": [{**_edge()[0], "effect": "많이"}], "missing": []},
    {"nodes": _nodes(), "edges": [{**_edge()[0], "kind": "identity", "formula": ""}],
     "missing": []},
    {"nodes": _nodes(), "edges": [{**_edge()[1], "says": "", "because": ""}], "missing": []},
    {"nodes": _nodes(), "edges": _edge(), "missing": [], "target": "없는노드@t+0"},
    {"nodes": _nodes(), "lookups": 3, "edges": _edge(), "missing": []},
])
def test_every_contract_violation_leaves_parse_as_pipeline_error(broken):
    """게이트가 거르는 **모든** 위반이 한 타입으로 나와야 호출부가 다룰 수 있다.

    WHY: 되먹임은 `except PipelineError` 하나로 받는다. 어떤 위반이 AttributeError·
    TypeError 로 새면 그 입력만 유니버스 전체 런을 죽인다. falsy 비목록(`edges: {}`)은
    특히 위험하다 - 예외조차 없이 "간선 없음"으로 접혀 계약 위반이 정상 산출로 집계된다.
    """
    with pytest.raises(PipelineError):
        agents.parse(broken)


def test_absent_optional_fields_still_parse():
    """위 가드가 **정상 산출까지** 거부하면 안 된다 - 선택 필드 부재는 계약 위반이 아니다."""
    minimal = {"nodes": _nodes(), "edges": [{
        "from": "PAYOUT@t+0", "to": "AR@t+0", "kind": "statistical",
        "says": "기대 상향이 당일 초과수익을 만들었다", "exposure": _TREATED_WHERE}]}

    prop = agents.parse(minimal)

    assert len(prop.designs) == 1
    assert prop.target == "AR@t+0"          # 없으면 유일 종점으로 떨어진다
    assert prop.designs[0].timing == "unscheduled"
    assert prop.missing == []


@pytest.mark.parametrize("field", ["says", "observed", "value", "events"])
@pytest.mark.parametrize("value", [1, "x", [], {}, [{"id": 1}], [1], None, True, 1.5])
def test_parse_survivors_never_explode_in_validate(field, value):
    """**parse 를 통과한 산출은 validate 에서 PipelineError 아닌 예외를 내지 않는다.**

    WHY: 되먹임은 `except PipelineError` 하나로 받는다. 게이트를 통과한 값이 하류에서 다시
    읽힐 때 TypeError·AttributeError·ValueError 로 새면 그 입력만 AnalyzeOne 을 죽이고
    전량성공 게이트(ADR-0028)가 유니버스 전체 런을 FAILED 시킨다. 케이스를 하나씩 늘리는
    대신 불변식으로 박는다 - 새 메타 필드를 graph 가 연산하기 시작하면 여기서 깨진다.
    """
    from edge_analysis.causal import graph as G

    nodes = _nodes()
    nodes["DIVIDEND@t+0"] = {**nodes["DIVIDEND@t+0"], field: value}
    out = {"target": "AR@t+0", "nodes": nodes, "edges": _edge(), "missing": []}
    try:
        prop = agents.parse(out)
    except PipelineError:
        return                              # 게이트가 잡았다 - 계약대로다

    violations = G.validate({"nodes": prop.nodes,
                             "structures": [{"id": "A", "edges": prop.edges}]},
                            grounded={EVENT_ID}, require_competing=False)
    assert isinstance(violations, list)     # 예외 없이 판정으로 끝나야 한다
