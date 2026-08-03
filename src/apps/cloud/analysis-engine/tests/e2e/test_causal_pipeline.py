"""인과 경로 E2E — **P0–P9 전 단계를 실제 DB·실제 LLM 없이 통과시킨다.**

실물 경계(Postgres·DeepSeek·S3)만 스텁으로 갈고 질문→지문→가설→그래프→식별→판별→검정→
예산→민감도→대조→처분→누적을 전부 실제 코드로 돌린다. 여기서 지키려는 것은 I/O 가 아니라
**폐쇄 셋과 상한 하나**다:

    회계 폐쇄   귀속의 합이 `Question.budget` 을 넘으면 그래프가 틀렸다
    교란 폐쇄   `assignment="chosen"` 이면 코드가 U 를 심고 모델이 지울 수 없다
    처분 폐쇄   검토한 후보에 침묵이 없다 - 기여 / 비기여 / 미결 셋 중 하나
    주장 상한   미소거 U 가 하나라도 남으면 고객 문장에 "확인됐습니다" 가 못 나간다

마지막 줄이 이 파일의 존재 이유다. `test_the_confirmed_phrase_needs_every_latent_cleared`
가 소거/미소거 두 세계를 **같은 셀에서** 돌려 그 차이가 고객 문장까지 닿는지 본다.

비용 순서와 무날조도 그대로 지킨다: 비중 0 이면 LLM 은 호출되지 않고, 고객 문장의 모든
퍼센트는 스텁이 준 수치에서 유도된 것이어야 한다 - 실험판 날조의 회귀 감시다.
"""
from __future__ import annotations

import json
import re
from datetime import date
from types import SimpleNamespace

import numpy as np
import pytest

from edge_analysis.adapters.llm import analyze
from edge_analysis.adapters.sql_surface import SqlLedger
from edge_analysis.causal import p2_hypotheses as p2
from edge_analysis.causal import p3_graph as p3
from edge_analysis.causal import p5_discriminate as p5
from edge_analysis.causal import p8_findings as p8
from edge_analysis.causal import p9_registry as p9
from edge_analysis.causal.contracts import DOMAIN_SAY
from edge_analysis.causal.run import N_HYPOTHESES, explain
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

# 후보 1 - 산술을 통과하고 가설로 선다. 기업이 고른 사건이므로 배정은 `chosen` 이다.
EVENT_ID = "evt_1"
EVENT_TYPE = "COMPANY.CAPITAL.DIVIDEND_DECISION"
EVENT_DATE = "2026-07-15"
CAUSE_LABEL = "삼성전자 분기 배당 확대 결정"
# 후보 2 - 비중이 없어 **LLM 전에** 죽는다. 죽은 것도 원장에 남아야 한다(처분 폐쇄).
ZERO_EVENT_ID = "evt_2"
ZERO_EVENT_TYPE = "COMPANY.EQUITY.TREASURY_DISPOSAL"
ZERO_EVENT_DATE = "2026-07-14"
ZERO_LABEL = "테스트 소재 자사주 처분 결정"
ZERO_INSTRUMENT = "inst_Z1"
# 후보 목록 **밖의** 원인. P2 는 목록 안에서 고를 의무가 없다 - 그 자유를 여기서 쓴다.
REBAL_TYPE = "MARKET.INDEX.CONSTITUENT_WEIGHT_CHANGE"
REBAL_LABEL = "패시브 비중 상향"
# 사건창 안에 겹친 타 공시. P7 스크린이 이걸 잡아야 한다.
CONFLICT_TYPE = "COMPANY.GOVERNANCE.SHAREHOLDER_MEETING"
CONFLICT_DATE = date(2026, 7, 14)
CONFOUNDED = "inst_T1"

# 스텁이 공급하는 **유일한 수치 원천.** 고객 문장의 퍼센트는 전부 여기서 유도돼야 한다.
OBSERVED = 0.0500        # ETF 당일 등락 (decomposition.proxy_ret)
RESIDUAL = 0.0421        # ETF 당일 시장대비 초과수익 (cd.ar 이 돌려주는 값 = 설명 예산)
EFFECT = 0.0300          # 처치군에만 심은 효과. **예산(잔차×1.15) 안에 든다**
NOISE_SD = 0.0120        # 대조군은 잡음만
SHARE = 0.2400           # ETF 내 처치 종목 비중
ABS_MAX = 0.3930         # 타입 과거 |초과수익| 최대 (산술 게이트의 상한)
CONTRIBUTORS = [("삼성전자", 0.0312), ("SK하이닉스", 0.0089)]

_TREATED_IDS = ("inst_T1", "inst_T2", "inst_T3", "inst_T4")
_CONTROL_IDS = tuple(f"inst_C{i:02d}" for i in range(20))
_EVENT_DATES = (date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15))

# 술어. 처치는 사건 기반, 대조는 종목 속성 기반이다.
REBAL_WHERE = (f"event_type_code = '{REBAL_TYPE}' AND role_code = 'ISSUER'"
               " AND lifecycle_stage = 'CONFIRMED'")
DIVIDEND_WHERE = (f"event_type_code = '{EVENT_TYPE}' AND role_code = 'ISSUER'"
                  " AND lifecycle_stage = 'CONFIRMED'")
CONTROL_WHERE = "listing_market = 'KOSPI' AND industry_name = 'Semiconductors'"


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
                 residual: float | None = RESIDUAL, seed: int = 7) -> None:
        rng = np.random.default_rng(seed)
        self.treated = [(i, d) for d in _EVENT_DATES for i in _TREATED_IDS]
        # 대조군은 거래일까지 펼친다 - P1 의 피어 동조 축과 P6 의 결과 산포가 셀 당일
        # 관측을 요구한다. 검정 코호트는 처치 날짜로 다시 좁히므로 대비는 그대로다.
        self.control = [(i, d) for d in (*_EVENT_DATES, TRADE_DATE) for i in _CONTROL_IDS]
        # 사건창 안에 겹친 타 공시 한 건. 스크린이 이 기업을 빼야 한다.
        self.conflicts = [(CONFOUNDED, CONFLICT_DATE)]
        self._share = share
        self._ar: dict = {}
        if residual is not None:
            self._ar[(ETF_INSTRUMENT, TRADE_DATE)] = residual
        for p in self.treated:
            self._ar[p] = float(rng.normal(0.0, NOISE_SD)) + effect
        for p in self.control:
            self._ar[p] = float(rng.normal(0.0, NOISE_SD))
        pairs = self.treated + self.control
        self._mom = {p: float(rng.normal(0.0, 0.008)) for p in pairs}
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
        rows = self.conflicts if "<>" in where else (
            self.treated if "event_type_code" in where else [])
        return [(i, d) for i, d in rows
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

    def flow(self, pairs, *, kind: str = "institution_total") -> np.ndarray:
        # 수급 결과 열. 값은 쓰지 않고 **표면이 있다는 사실**만 고정한다 - 없으면
        # sandbox.tools 가 AttributeError 로 죽어 검정이 통째로 사라진다.
        self.calls.append(f"flow:{kind}")
        return self._col(pairs, self._vol) * -1e9

    def ids(self, names) -> dict:
        self.calls.append("ids")
        return {str(n): f"inst_{n}" for n in (names or [])}

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

    # 층화·어휘 재료. 없으면 대조군 술어가 원장에 없는 산업명을 가리킨다(#403).
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


# --------------------------------------------------------------------------- #
# SQL 표면 스텁 — **질의 문자열로 갈라 답한다.** 무엇을 물었는지가 계약이다
# --------------------------------------------------------------------------- #
# 뷰 이름은 **실재하는 것만** 쓴다 - `adapters.sql_surface` 의 표면에 없는 이름을 대본에
# 박으면 스텁은 받아주고 실물은 거부해서, 통과하는 테스트가 못 도는 질의를 지킨다.
Q_P2 = "SELECT count(*) AS n FROM v_instrument WHERE ticker = '005930'"
Q_P3 = "SELECT count(*) AS n FROM v_instrument WHERE listing_market = 'KOSPI'"
Q_P5 = "SELECT count(*) AS n FROM v_daily WHERE trade_date = '2026-07-16'"
Q_DISC = "SELECT count(*) AS n FROM v_daily WHERE instrument_id = 'inst_T1'"


class FakeSql:
    """`adapters.sql_surface.SqlSurface` 계약의 스텁. **원장은 진짜를 쓴다.**

    P5 의 `executable` 은 자기 신고가 아니라 질의가 실제로 낸 행에서 온다. 그래서 이
    스텁이 0행을 내면 U 는 미소거로 남고 주장 상한이 내려간다 - 표면 유무가 결론을
    바꾸는 경로가 실제로 배선돼 있는지를 이 스텁 하나로 흔든다.
    """

    def __init__(self) -> None:
        self.ledger = SqlLedger()

    def schema(self) -> str:
        return ("v_event · v_cohort · v_instrument · v_daily · v_hold · v_flow · "
                "v_liquidity (시점 클램프는 뷰 안에 있다)")

    def query(self, sql: str, *, limit: int = 500) -> list[dict]:
        rows = self._rows(sql)
        self.ledger.record(sql, len(rows))
        return rows

    def ask(self, sql: str, *, show: int = 20) -> str:
        rows = self.query(sql)
        return "0행." if not rows else f"{len(rows)}행. 첫 행: {rows[0]}"

    @staticmethod
    def _rows(sql: str) -> list[dict]:
        if "v_event" in sql:                                  # P1 공개 시각 되읽기
            return [{"source_event_id": EVENT_ID,
                     "opened_at": f"{EVENT_DATE}T08:30:00+09:00"}]
        if "v_cohort" in sql and "instrument_id" in sql:       # P7 충돌 사건 명명
            return [{"instrument_id": CONFOUNDED, "trade_date": CONFLICT_DATE.isoformat(),
                     "event_type_code": CONFLICT_TYPE, "source_event_id": "evt_c1"}]
        if "v_cohort" in sql:                                  # P7 처치 사실 조회
            return [{"event_type_code": EVENT_TYPE, "lifecycle_stage": "CONFIRMED",
                     "role_code": "ISSUER"}]
        return [{"n": 1}]                                      # P2·P3·P5 자유 질의


# --------------------------------------------------------------------------- #
# 대본 — 모델이 낼 수 있는 것만 낸다(수치 없음)
# --------------------------------------------------------------------------- #
# P2 세션 1. **후보 목록 밖의 원인이고 배정이 mechanical 이라 U 가 심기지 않는다** -
# 그래서 이 가설만이 `confirmed` 까지 갈 수 있고, 대조 쌍의 기준선이 된다.
H_REBAL = {"hypothesis": {
    "says": "지수 사업자 리뷰 결과가 확정되면서 패시브 자금이 정해진 날짜에 정해진 수량을 "
            "사야 했고, 그 매수가 당일 초과수익을 만들었다",
    "cause_label": REBAL_LABEL,
    "treatment": "REBAL@t-1", "outcome": "설명대상_잔차@t0", "assignment": "mechanical",
    # 역할·영역은 P2 파서가 **필수**로 받는다. 지수 규칙이 정해진 날에 정해진 수량을 사게
    # 만든 것이므로 연쇄를 시작한 촉발원이고, 원인은 공시의 내용이 아니라 추종 자금의
    # 강제 매수라서 영역은 information 이 아니라 flow 다.
    "role": "trigger", "domain": "flow",
    "nodes": {"REBAL@t-1": {"says": "지수 사업자가 확정한 비중 상향 결정",
                            "observed": "지수 사업자 리뷰 결과 공시"},
              "설명대상_잔차@t0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률"}},
    "edges": [{"from": "REBAL@t-1", "to": "설명대상_잔차@t0",
               "says": "확정된 비중 상향이 당일 초과수익을 만든다",
               "because": "패시브 추종 자금은 재량이 없어 확정일에 사야 한다"}],
    "predicts": ["리뷰 확정 다음 거래일에 대상 종목의 거래대금이 평소보다 크다"],
    "denies": ["대상이 아닌 종목도 같은 폭으로 오른다"],
    "events": []}}

# P2 세션 2. 기업이 고른 사건이므로 `chosen` 이고, 그 신고 하나로 코드가 U 를 심는다.
H_DIVIDEND = {"hypothesis": {
    "says": "이사회가 배당 총액을 늘리기로 하면서 주주환원 기대가 올라 당일 초과수익이 생겼다",
    "cause_label": CAUSE_LABEL,
    "treatment": "DIVIDEND@t-1", "outcome": "설명대상_잔차@t0", "assignment": "chosen",
    # 이사회 결정이 새 정보를 주어 기대를 바꾼 자리 - 촉발원이고 정보·기대 영역이다.
    "role": "trigger", "domain": "information",
    "nodes": {"DIVIDEND@t-1": {"says": "분기 배당 확대 결정 공시", "observed": "공시 원장",
                               "events": [EVENT_ID]},
              "설명대상_잔차@t0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률"}},
    "edges": [{"from": "DIVIDEND@t-1", "to": "설명대상_잔차@t0",
               "says": "배당 확대 결정이 당일 초과수익을 만들었다",
               "because": "주주환원 확대는 기대 현금흐름의 배분 경로를 직접 바꾼다"}],
    "predicts": ["같은 타입 사건에서 발행 주체의 초과수익이 참조집단보다 높다"],
    "distinguishes": ["리밸런스 경로면 거래대금이 먼저 튀지만 배당 경로는 거래대금 "
                      "없이 가격만 움직인다"],
    "denies": ["발행 주체가 아닌 참여자도 같은 폭으로 오른다"],
    "events": [EVENT_ID]}}

# 시간 역행: 원인이 결과보다 늦다. P3 의 구조 검사가 잡아야 한다.
H_BACKWARDS = {"hypothesis": {
    **H_DIVIDEND["hypothesis"],
    "treatment": "DIVIDEND@t+1",
    "nodes": {"DIVIDEND@t+1": {"says": "분기 배당 확대 결정 공시", "observed": "공시 원장",
                               "events": [EVENT_ID]},
              "설명대상_잔차@t0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률"}},
    "edges": [{"from": "DIVIDEND@t+1", "to": "설명대상_잔차@t0",
               "says": "배당 확대 결정이 당일 초과수익을 만들었다",
               "because": "주주환원 확대는 기대 현금흐름의 배분 경로를 직접 바꾼다"}]}}

H_NONE = {"none": "남은 가치채널은 앞선 세션이 이미 세웠다"}
P2_OK = [{"thought": "처치 종목이 원장에 있는지 본다", "sql": Q_P2}, H_REBAL, H_DIVIDEND, H_NONE]


def _graph(nodes: dict, edges: list) -> dict:
    return {"graph": {
        "nodes": nodes, "edges": edges,
        # **모델은 U 를 하나도 적지 않는다.** chosen 배정에 U 를 심는 것은 코드의 일이고,
        # 그 일이 실제로 일어나는지가 교란 폐쇄의 계약이다.
        "latents": [],
        "completeness": "두 처치와 결과 사이의 변수쌍을 전부 훑었다. 관측 가능한 공통원인은 "
                        "지수 리뷰 일정과 배당 결정 일정 둘뿐이고 서로 독립이다."}}


_NODES_OK = {
    "REBAL@t-1": {"says": "지수 사업자가 확정한 비중 상향 결정",
                  "observed": "지수 사업자 리뷰 결과 공시", "events": []},
    "DIVIDEND@t-1": {"says": "분기 배당 확대 결정 공시", "observed": "공시 원장",
                     "events": [EVENT_ID]},
    "설명대상_잔차@t0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률", "events": []},
}
_EDGES_OK = [
    {"from": "REBAL@t-1", "to": "설명대상_잔차@t0", "kind": "statistical",
     "says": "확정된 비중 상향이 당일 초과수익을 만든다",
     "because": "패시브 추종 자금은 재량이 없어 확정일에 사야 한다",
     "false_if": "대상이 아닌 종목이 같은 폭으로 움직였다면 죽는다",
     "exposure": REBAL_WHERE, "reference": CONTROL_WHERE},
    {"from": "DIVIDEND@t-1", "to": "설명대상_잔차@t0", "kind": "statistical",
     "says": "배당 확대 결정이 당일 초과수익을 만들었다",
     "because": "주주환원 확대는 기대 현금흐름의 배분 경로를 직접 바꾼다",
     "false_if": "발행 주체가 아닌 참여자도 같이 올랐다면 죽는다",
     "exposure": DIVIDEND_WHERE, "reference": CONTROL_WHERE},
]
GRAPH_OK = _graph(_NODES_OK, _EDGES_OK)
P3_OK = [{"thought": "대조군 술어가 원장에서 몇 건인지 본다", "sql": Q_P3}, GRAPH_OK]

_NODES_BACK = {"DIVIDEND@t+1": _NODES_OK["DIVIDEND@t-1"], "설명대상_잔차@t0": _NODES_OK["설명대상_잔차@t0"]}
P3_BACKWARDS = [_graph(_NODES_BACK, [{**_EDGES_OK[1], "from": "DIVIDEND@t+1"}])]

# P5. latent 하나(코드가 심은 U)와 가설쌍 하나를 처분한다.
_DISC_LATENT = {"thought": "선택 편의가 있으면 결정 이전에 이미 값이 움직여 있었을 것이다",
                "discriminator": {
                    "kind": "latent", "target": "U_DIVIDEND@t-1",
                    "observation": "결정 공시 이전 거래일의 발행 주체 초과수익",
                    "predicts": {"h2": "결정 이전은 평평하고 공시 당일에만 움직인다",
                                 "U_DIVIDEND@t-1": "결정 이전부터 같은 방향으로 움직여 있다"},
                    "sql": Q_DISC,
                    # WOE 를 안 적으면 0 dB 라 `common_prediction` 이 서고 U 가 미소거로
                    # 남는다. 사전 표류는 선택 편의 세계에서 훨씬 잘 예상되므로 10 dB 다.
                    "woe_db": 10,
                    "woe_because": "선택 편의가 있으면 결정 이전 며칠의 초과수익이 이미 같은 "
                                   "방향으로 쌓여 있다 - 공시가 원인인 세계에서 그 사전 "
                                   "표류는 우연에 기대야 한다"}}
_DISC_PAIR = {"thought": "두 세계는 지수 대상이 아닌 배당 공시 종목에서 갈린다",
              "discriminator": {
                  "kind": "pair", "target": "h1|h2",
                  "observation": "비중 상향 대상이 아닌 배당 공시 종목의 당일 초과수익",
                  "predicts": {"h1": "대상이 아니면 움직이지 않는다",
                               "h2": "배당만 있어도 움직인다"},
                  "sql": Q_DISC,
                  "woe_db": 7,
                  "woe_because": "지수 대상이 아닌 배당 공시 종목이 움직이면 수급 세계는 그것을 "
                                 "예상하지 못한다 - 정보 세계에서는 당연한 관측이다"}}
P5_OK = [{"thought": "거래일에 관측이 있는지 먼저 본다", "sql": Q_P5},
         _DISC_LATENT, _DISC_PAIR, {"thought": "남은 것이 없다", "done": True}]

# 검정 에이전트가 쓸 코드. **하네스가 실행한다** - 도구 이름·반환 모양이 틀리면 여기서 깨진다.
VERIFY_CODE = """
t = cohort("event_type_code = '{TYPE}'", w0='2026-05-01')
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
""".replace("{TYPE}", REBAL_TYPE)

VERIFY_TURNS = [{"thought": "타입 전체에서 대비를 쌓고 날짜 안에서 섞는다", "code": VERIFY_CODE},
                {"thought": "R 완성", "done": True}]

# 데이터가 없어 못 재는 간선. **기각이 아니라 요청이다.**
VERIFY_IMPOSSIBLE = [{"thought": "장중 체결 흐름이 없으면 이 경로를 못 가른다",
                      "impossible": "수급 경로를 분리할 체결 흐름 원장이 없다",
                      "need": "장중 체결 흐름(투자자별 순매수) 일별 원장",
                      "grain": "일별",
                      "unlocks": "수급 주도와 사건 주도를 같은 셀에서 가를 수 있다"}]

VERIFY_OK = {"REBAL@t-1→설명대상_잔차@t0": VERIFY_TURNS,
             "DIVIDEND@t-1→설명대상_잔차@t0": VERIFY_IMPOSSIBLE}
_VERIFY_UNSCRIPTED = [{"thought": "대본에 없는 간선이다",
                       "impossible": "이 간선을 잴 설계가 없다",
                       "need": "대본 밖 간선의 관측", "grain": "일별", "unlocks": ""}]


# --------------------------------------------------------------------------- #
# 클라이언트 스텁 — **어느 프롬프트로 불렸는지가 계약이다**
# --------------------------------------------------------------------------- #
_P5_HEAD = p5.SYSTEM.split("{brief}")[0]
_EDGE = re.compile(r"간선\s+(\S+)\s+→\s+(\S+)")


def _edge_tag(system: str) -> str:
    """검정 브리프 첫 줄에서 어느 간선인가. 간선마다 세션이 따로 열리므로 이게 대본 키다."""
    m = _EDGE.search(system)
    return f"{m.group(1)}→{m.group(2)}" if m else "?"


def _stage(system: str) -> str:
    """이 세션이 어느 단계인가. 각 모듈 SYSTEM 이 유일한 표지다.

    이전 구조에는 제안·검정 두 세션뿐이라 `system == agents.SYSTEM` 한 줄로 갈렸다.
    지금은 P2 가 n번, P3·P5 가 한 번씩, 검정이 간선마다 붙는다 - 갈림을 틀리면 대본이
    엉뚱한 단계로 흘러 테스트가 조용히 다른 것을 검사한다.
    """
    if system.startswith(p2.SYSTEM):
        return "p2"
    if system == p3.SYSTEM:
        return "p3"
    if system.startswith(_P5_HEAD):
        return "p5"
    if 'R = {"x"' in system:
        return "verify"
    raise AssertionError(f"P2·P3·P5·검정 어느 프롬프트도 아니다: {system[:80]!r}")


def _next(script: list) -> dict:
    """대본에서 하나. **마지막 항목은 소진되지 않는다** - 되먹임 루프가 같은 것을 다시 받아
    상한까지 돌아야 위반 3연속·none 3연속 같은 종결 조건이 실제로 검사된다."""
    return script.pop(0) if len(script) > 1 else script[0]


class FakeClient:
    """P0–P9 의 네 세션을 대본으로 가른다.

    검정 세션에는 파이썬 코드를 돌려주고 그 코드는 **실제로 샌드박스에서 실행된다** -
    여기서 잡고 싶은 것은 "모델이 수치를 만들 자리가 없다"는 계약이므로, 코드가 도구를
    타고 스텁 데이터에 닿는 경로 전체가 진짜여야 한다.
    """

    def __init__(self, *, hyps: list | None = None, graph: list | None = None,
                 disc: list | None = None, verify: dict | None = None) -> None:
        self._hyps = list(P2_OK if hyps is None else hyps)
        self._graph = list(P3_OK if graph is None else graph)
        self._disc = list(P5_OK if disc is None else disc)
        self._verify = dict(VERIFY_OK if verify is None else verify)
        self._scripts: dict[str, list] = {}
        self.calls = 0
        self.p2 = self.p3 = self.p5 = self.verifies = 0
        # 조회 턴과 **그래프 제출**을 갈라 센다. 조회는 시도를 쓰지 않는 것이 P3 계약이라
        # 호출 수만 세면 "몇 번 만에 세웠나"를 못 본다.
        self.p3_graphs = 0
        self.p2_prompts: list[str] = []
        self.p3_prompts: list[str] = []
        self.p5_prompts: list[str] = []
        self.verify_prompts: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        stage = _stage(system)
        if stage == "p2":
            self.p2 += 1
            self.p2_prompts.append(user)
            return _next(self._hyps)
        if stage == "p3":
            self.p3 += 1
            self.p3_prompts.append(user)
            out = _next(self._graph)
            self.p3_graphs += "graph" in out
            return out
        if stage == "p5":
            self.p5 += 1
            self.p5_prompts.append(user)
            return _next(self._disc)
        self.verifies += 1
        self.verify_prompts.append(system)
        tag = _edge_tag(system)
        script = self._scripts.setdefault(
            tag, list(self._verify.get(tag, _VERIFY_UNSCRIPTED)))
        return _next(script)

    def prompt_for(self, tag: str) -> str:
        """간선 하나의 검정 브리프. 없으면 죽는다 - 빈 문자열은 감사가 아니다."""
        got = [s for s in self.verify_prompts if _edge_tag(s) == tag]
        assert got, f"{tag} 검정 세션이 열리지 않았다"
        return got[0]


class LegacyClient:
    """이전 단일 프롬프트 경로용 스텁(causal_enabled=False 확인)."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete_json(self, system: str, user: str) -> dict:
        self.systems.append(system)
        return {"verdict": "시장·섹터 주도", "explain": "시장 전반이 밀어올렸습니다."}


# --------------------------------------------------------------------------- #
# 호출 헬퍼
# --------------------------------------------------------------------------- #
def _prior(event_type_code: str) -> dict:
    return FakeCausalData().prior(event_type_code)


def _candidates(share: float | None = SHARE) -> list[dict]:
    """후보 둘. **두 번째는 비중이 없어 산술로 죽는다** - 죽은 것도 원장에 남아야 한다."""
    return [
        {"event_id": EVENT_ID, "available_at": f"{EVENT_DATE}T08:30:00+09:00",
         "event_type_code": EVENT_TYPE, "label": CAUSE_LABEL, "event_date": EVENT_DATE,
         "ticker": "005930", "instrument_id": _TREATED_IDS[0], "share": share,
         "prior": _prior(EVENT_TYPE)},
        {"event_id": ZERO_EVENT_ID, "available_at": f"{ZERO_EVENT_DATE}T09:10:00+09:00",
         "event_type_code": ZERO_EVENT_TYPE, "label": ZERO_LABEL,
         "event_date": ZERO_EVENT_DATE, "ticker": "091990",
         "instrument_id": ZERO_INSTRUMENT, "share": 0.0, "prior": _prior(ZERO_EVENT_TYPE)},
    ]


def _explain(cd: FakeCausalData, client, *, candidates=None, sandbox: bool = True,
             sql=None, registry_root=None) -> dict:
    return explain(cd, client, etf_name=ETF_NAME, etf_instrument_id=ETF_INSTRUMENT,
                   trade_date=TRADE_DATE, as_of=AS_OF, observed=OBSERVED,
                   route_code="CONCENTRATED", contributors=CONTRIBUTORS,
                   candidates=_candidates() if candidates is None else candidates,
                   grounded={EVENT_ID, ZERO_EVENT_ID}, sandbox=sandbox,
                   sql=sql, registry_root=registry_root)


def _names(raw: dict) -> list[str]:
    return [d["candidate"] for d in raw["causal"]["dispositions"]]


def _by_name(raw: dict, needle: str) -> dict:
    got = [d for d in raw["causal"]["dispositions"] if needle in d["candidate"]]
    assert got, f"{needle!r} 가 처분 원장에 없다: {_names(raw)}"
    return got[0]


# --------------------------------------------------------------------------- #
# 수치 무날조 감사
# --------------------------------------------------------------------------- #
_PCT = re.compile(r"[+-]?\d+(?:\.\d+)?%")


def _pcts(text: str) -> set[str]:
    return {m.group() for m in _PCT.finditer(text)}


def _allowed(raw: dict) -> set[str]:
    """스텁이 준 수치에서 **유도 가능한** 퍼센트 표기 전부.

    `p8_findings` 의 서식(`{x*100:+.2f}%`·잔차 대비 몫 `:.0%`·산술 게이트의 `:.0f`/`:.1f`)을
    그대로 재현한다. 본문에 이 집합 밖의 퍼센트가 하나라도 있으면 어딘가에서 수치가
    만들어진 것이다 - 실험판 날조는 전부 모델이 수치를 말할 자리에서 났다.
    """
    c = raw["causal"]
    vals = [OBSERVED, c["residual"], c["unexplained"], *(v for _, v in CONTRIBUTORS)]
    for d in c["dispositions"]:
        ev = d.get("evidence") or {}
        vals += [v for v in (d.get("share"), d.get("contribution"), ev.get("effect"))
                 if v is not None]
        vals += list(ev.get("bounds") or ())
    out: set[str] = set()
    for v in vals:
        out.add(f"{v * 100:+.2f}%")
        out.add(f"{v * 100:.2f}%")
    if c["residual"]:
        out.add(f"{abs(c['unexplained'] / c['residual']):.0%}")
    # 산술 게이트 문장의 서식(비중·필요 초과수익·타입 과거 최대)
    for cand in c.get("screened") or ():
        share = cand.get("share")
        if share:
            out.add(f"{share * 100:.2f}%")
            out.add(f"{abs(c['residual']) / share * 100:.0f}%")
    out.add(f"{ABS_MAX * 100:.1f}%")
    return out


# --------------------------------------------------------------------------- #
# P0 질문 — 반사실을 문장으로 못 쓰면 그래프를 그릴 자격이 없다
# --------------------------------------------------------------------------- #
def test_the_counterfactual_is_a_sentence_and_the_budget_comes_from_the_residual():
    """예산이 관측 등락이면 설명해야 할 폭이 부풀고 산술 게이트가 헐거워진다.

    그리고 개입이 문장으로 안 적히면 P3 가 무엇을 교란으로 봐야 하는지 정할 수 없다 -
    "공시가 없던 세계"와 "이사회가 다른 결정을 한 세계"는 교란 구조가 다르다.
    """
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    q = raw["causal"]["question"]
    # 질문은 산술 게이트보다 **먼저** 고정된다 - 그래서 검토 대상 전건에 반사실이 붙는다.
    # 하나만 골라 적으면 그 선택이 어디서 왔는지가 사라지고, 고르는 일은 P8 의 몫이다.
    assert q["intervention"].count("공시가 발생하지 않은 세계") == len(_candidates())
    assert CAUSE_LABEL in q["intervention"] and ZERO_LABEL in q["intervention"]
    assert "다른 기업이고" in q["intervention"], "정의 불가한 반사실과의 구분이 사라졌다"
    assert q["residual"] == RESIDUAL != q["observed"], "관측 등락을 예산으로 썼다"
    assert q["budget"] == abs(RESIDUAL)
    assert q["answer_form"].startswith("구간과 상한")


def test_a_missing_residual_is_recorded_instead_of_stopping_the_explanation():
    """잔차 조회 실패는 설명 실패가 아니다 - 예산이 헐거워졌다는 사실만 남기고 계속한다."""
    raw = _explain(FakeCausalData(residual=None), FakeClient(), sql=FakeSql())

    assert raw["causal"]["residual"] == OBSERVED, "잔차가 없는데 조용히 다른 값을 썼다"
    assert any("초과수익" in m for m in raw["causal"]["missing"])
    assert "확인에 필요했지만 확보하지 못한 자료" in raw["explain"]


# --------------------------------------------------------------------------- #
# P1 지문 — 가설 이전에, LLM 이전에 관측 자신을 잰다
# --------------------------------------------------------------------------- #
def test_the_fingerprint_kills_and_its_blind_spots_both_reach_the_hypothesis_prompt():
    """지문은 후보를 주지 않는다 - **후보를 죽일 재료**를 준다. 그게 P2 에 실려야 값이 있다.

    못 잰 축을 함께 싣는 것이 두 번째 계약이다. 부재를 각주로 밀면 읽히지 않고, 없는
    데이터를 전제한 가설이 그 틈으로 들어온다.
    """
    client = FakeClient()

    raw = _explain(FakeCausalData(), client, sql=FakeSql())

    head = client.p2_prompts[0]
    assert "관측 지문" in head
    assert "이 지문이 이미 배제한 것:" in head
    assert "등락 대부분이 상위 소수 종목에서 나왔다" in head, "집중도 축의 kills 가 안 실렸다"
    assert "측정 불가 - 분봉·틱 없음" in head, "측정 불가 축이 프롬프트에서 침묵했다"
    # 그리고 그 침묵 금지는 산출물까지 이어진다.
    axes = {a["name"]: a for a in raw["causal"]["fingerprint"]}
    assert axes["shape"]["available"] is True and axes["shape"]["kills"]
    assert axes["intraday_timing"]["available"] is False
    assert axes["intraday_timing"]["missing_input"]


def test_the_event_timing_axis_rereads_the_publication_time_when_sql_is_there():
    """후보 dict 의 `event_date` 는 잘린 값이다 - 장 전후를 가르려면 원장을 되읽어야 한다."""
    with_sql = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())
    without = _explain(FakeCausalData(), FakeClient())

    def _axis(raw):
        return next(a for a in raw["causal"]["fingerprint"] if a["name"] == "event_timing")

    assert _axis(with_sql)["value"]["source"] == "v_event"
    assert _axis(without)["value"]["source"] == "candidate"
    assert "날짜 해상도다" in _axis(without)["says"], "정밀도 손실이 조용히 넘어갔다"


# --------------------------------------------------------------------------- #
# P2 가설 — 세션을 n번 따로 돌린다. 한 세션에 n개를 시키면 첫 번째의 변주가 나온다
# --------------------------------------------------------------------------- #
def test_later_sessions_see_the_earlier_predictions_but_not_the_narrative():
    """문맥을 끊지 않으면 Chamberlin 이 말한 분산이 생기지 않는다.

    완전 독립도 아니다. 앞선 세션의 **채널 이름과 `predicts` 만** 넘어간다 - 갈릴 재료는
    주고 베낄 재료는 막는 선이다. 이름만 넘기던 앞선 규약은 중복은 줄였지만 대립을 만들지
    못했다(2026-07-30 실측: 채널은 셋 다 달랐는데 두 가설의 예측이 같아 어떤 관측으로도
    갈리지 않았다). 그 선이 실제로 지켜지는지 여기서 고정한다.
    """
    client = FakeClient()

    raw = _explain(FakeCausalData(), client, sql=FakeSql())

    assert client.p2 == N_HYPOTHESES + 1, "세션 수가 N_HYPOTHESES 와 어긋난다(조회 1회 포함)"
    for idx in range(1, N_HYPOTHESES + 1):
        assert sum(1 for u in client.p2_prompts if f"[세션 {idx}]" in u) >= 1
    second = next(u for u in client.p2_prompts if "[세션 2]" in u)
    assert REBAL_LABEL in second, "앞선 채널 이름이 안 갔다 - 중복을 피할 수 없다"
    assert H_REBAL["hypothesis"]["predicts"][0] in second, "앞선 예측이 안 갔다 - 갈릴 수 없다"
    assert H_REBAL["hypothesis"]["says"] not in second, "앞 세션의 서사가 새어 나갔다"
    # 두 세션의 산출이 서로 다른 가설로 원장에 남는다.
    hids = {(d.get("evidence") or {}).get("hid") for d in raw["causal"]["dispositions"]}
    assert {"h1", "h2"} <= hids, hids


def test_a_lookup_does_not_burn_a_hypothesis_attempt():
    """모르는 것을 묻는 것과 가설을 틀리는 것은 다른 일이다 - 조회에 벌점을 주면 지어낸다."""
    sql = FakeSql()

    raw = _explain(FakeCausalData(), FakeClient(), sql=sql)

    assert Q_P2 in sql.ledger.queries
    assert {REBAL_LABEL, CAUSE_LABEL} <= set(_names(raw)), "조회를 쓴 세션이 가설을 못 냈다"


def test_a_candidate_that_never_becomes_a_hypothesis_still_gets_a_verdict():
    """억지 설계는 UNCERTAIN 보다 나쁘다. 그래도 **검토한 후보는 원장에 남아야 한다.**

    처분 폐쇄가 가장 조용히 새는 자리다. 산술로 죽은 후보는 사유가 있어 남기 쉽고, 가설이
    된 후보는 검정 결과가 있어 남기 쉽다. 사이에 낀 것 - 무게는 있는데 아무도 이야기를
    세우지 못한 후보 - 만 아무 산출도 없어서 목록에서 통째로 빠진다. 그러면 "원인 미확인"
    한 문장이 *찾아봤는데 아니었다* 와 *아예 안 봤다* 를 같은 말로 덮는다.

    실제로 배선에서 샜다: `run.explain` 이 P8 에 죽은 후보만 넘겨 살아남은 후보를
    처분할 기회 자체가 없었다. 그래서 여기서는 **셋을 한꺼번에** 고정한다 - 죽은 것은
    비기여로, 산 것은 미결로, 둘 다 사유를 달고 원장에 있어야 한다.
    """
    cd, client = FakeCausalData(), FakeClient(hyps=[H_NONE])

    raw = _explain(cd, client)

    assert client.p3 == 0 and client.p5 == 0 and client.verifies == 0
    # 검정은 한 판도 안 돈다. P7 의 오염 스크린은 그래도 도는데(후보는 살아 있으므로)
    # 그건 검정이 아니라 표본 정의라 여기서 세지 않는다.
    assert raw["causal"]["proofs"] == [], "가설이 없는데 간선을 추정했다"
    assert Explanation(raw).explanation_type == "UNCERTAIN"
    # PC 는 목록이다 (NTSB Writing Guide: "a listing of separate causal factors").
    # 빈 목록이 "아무것도 원인으로 세우지 못했다" 다 - None 은 이제 나오지 않는다.
    assert raw["causal"]["probable_cause"] == []
    assert "확인되지 않았습니다" in raw["explain"]

    alive, dead = _by_name(raw, CAUSE_LABEL), _by_name(raw, ZERO_LABEL)
    assert alive["verdict"] == "undetermined"
    assert alive["why"] == "산술 게이트는 통과했으나 가설로 서지 못했다"
    assert alive["share"] == SHARE, "무게가 있었다는 사실까지 남아야 다음 조사가 시작된다"
    assert dead["verdict"] == "not_contributing" and "비중이 없어" in dead["why"]
    # 그리고 그 구분이 고객 문장까지 간다 - 원장에만 남고 문장에서 사라지면 같은 실패다.
    assert "판단을 보류한 것" in raw["explain"] and CAUSE_LABEL in raw["explain"]


# --------------------------------------------------------------------------- #
# P3 그래프 — 교란 폐쇄. 모델이 U 를 안 적어도 코드가 심고, 모델은 지울 수 없다
# --------------------------------------------------------------------------- #
def test_a_chosen_assignment_compiles_a_latent_the_model_never_wrote():
    """기업이 고르는 사건은 좋은 사적 정보와 함께 온다 - 선택 편의는 예외가 아니라 기본값이다.

    그 기본값을 모델의 성실성에 맡기면 무교란이 기본이 된다. 그래서 배정 신고 하나로
    코드가 U 를 심는다. **모델 산출에는 latents 가 빈 목록이다** - 그런데도 나와야 한다.
    """
    assert GRAPH_OK["graph"]["latents"] == [], "픽스처가 U 를 적어 버리면 검사가 공허해진다"

    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    lat = raw["causal"]["graph"]["latents"]
    assert [u["uid"] for u in lat] == ["U_DIVIDEND@t-1"], lat
    assert lat[0]["source"] == "compiled"
    assert lat[0]["between"] == ["DIVIDEND@t-1", "설명대상_잔차@t0"]
    # mechanical 배정에는 심지 않는다 - 규칙이 아무 데나 U 를 뿌리면 상한이 무의미해진다.
    assert not any(u["between"][0] == "REBAL@t-1" for u in lat)


def test_a_structure_violation_is_fed_back_and_then_recorded_instead_of_being_hidden():
    """구조 검사는 무료다. 위반은 되먹임으로 돌아가고, 못 고치면 **위반을 단 채로** 남는다.

    위반을 지우고 통과시킨 그래프가 내는 `adjust=[]` 보다 그쪽이 정직하다 - 그 빈 집합은
    세계에 대한 진술이 아니라 검사를 껐다는 진술이기 때문이다.
    """
    client = FakeClient(hyps=[H_BACKWARDS, H_NONE], graph=P3_BACKWARDS)

    raw = _explain(FakeCausalData(), client, sql=FakeSql())

    assert client.p3_graphs == p3.MAX_TRIES, "구조 위반이 되먹임 재질의를 못 만들었다"
    violations = raw["causal"]["graph"]["violations"]
    assert any("시간 역행" in v for v in violations), violations
    assert raw["causal"]["ceiling"] == "undetermined"
    assert Explanation(raw).explanation_type == "UNCERTAIN"


def test_the_completeness_declaration_records_that_it_was_made_without_the_ledger():
    """조회 없이 한 완비 선언은 근거가 얇다 - 그 사실이 선언 자체에 붙어야 사후에 안 속는다."""
    without = _explain(FakeCausalData(), FakeClient())
    with_sql = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    assert "원장 조회 없이 선언" in without["causal"]["graph"]["completeness"]
    assert "원장 조회 없이 선언" not in with_sql["causal"]["graph"]["completeness"]


# --------------------------------------------------------------------------- #
# P4 식별 — 3값이다. `not_identified` 가 정상 종료다
# --------------------------------------------------------------------------- #
def test_the_compiled_latent_makes_the_edge_not_identified_and_the_verifier_sees_it():
    """빈 조정집합은 성공이 아니다. 그리고 그 판정이 **검정 세션까지** 가야 한다.

    검정 브리프가 "뒷문이 열려 있지 않다"고 말하던 시절, 그 문장은 세계가 아니라 제안자의
    지식 상태를 보고했다. 지금은 막고 있는 U 를 이름으로 적고 축약형·부분식별로 내려가라고
    말한다 - 그 차이를 프롬프트에서 직접 본다.
    """
    client = FakeClient()

    raw = _explain(FakeCausalData(), client, sql=FakeSql())

    ident = {i["edge"]: i for i in raw["causal"]["identification"]}
    assert ident["DIVIDEND@t-1->설명대상_잔차@t0"]["status"] == "not_identified"
    assert ident["DIVIDEND@t-1->설명대상_잔차@t0"]["blocked_by"], "무엇이 막는지 안 적었다"
    assert ident["REBAL@t-1->설명대상_잔차@t0"]["status"] == "identified"

    brief = client.prompt_for("DIVIDEND@t-1→설명대상_잔차@t0")
    assert "식별상태 : not_identified" in brief
    assert "막고 있는 미관측 공통원인" in brief
    assert "점식별 불가" in brief
    assert "뒷문이 열려 있지 않다" not in brief, "빈 조정집합을 세계에 대한 진술로 말했다"


def test_a_blocked_edge_carries_bounds_instead_of_dying():
    """점식별 실패는 답이 없다는 뜻이 아니다 - 유계 가정 아래 구간은 언제나 있고 폭이 정보다."""
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    blocked = next(i for i in raw["causal"]["identification"]
                   if i["status"] == "not_identified")
    assert blocked["bounds"] == [-2 * ABS_MAX, 2 * ABS_MAX], blocked["bounds"]
    assert "지지집합" in blocked["bounds_note"]


# --------------------------------------------------------------------------- #
# P5 판별 — ★ 이 파일의 핵심. 소거/미소거가 고객 문장을 가른다
# --------------------------------------------------------------------------- #
def test_the_confirmed_phrase_needs_every_latent_cleared():
    """**미소거 U 가 하나라도 있으면 "확인됐습니다" 는 못 나간다.**

    두 세계를 같은 셀에서 돌린다. 다른 것은 하나뿐이다 - 판별 검정을 실제로 돌릴 수 있는가.
    돌릴 수 없으면 그 U 는 통제되지 않은 것이고, 통제되지 않은 교란이 남은 채로 나가는
    "원인으로 확인됐습니다" 는 우리가 없애려는 바로 그 문장이다.

    상한을 문장 뒤 경고로 두면 고객은 첫 문장만 읽고 확인으로 받는다. 그래서 상한이 어느
    동사를 쓸지를 정하고, 어겨진 조합은 `narrate` 가 예외로 막는다.
    """
    cleared = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())
    uncleared = _explain(FakeCausalData(), FakeClient(), sql=None)

    # 소거된 세계: U 가 원장에서 비기여로 처분되고 확정 문구가 열린다.
    assert cleared["causal"]["uncleared_latents"] == []
    assert cleared["causal"]["ceiling"] == "confirmed", cleared["causal"]["ceiling_why"]
    assert "원인으로 확인됐습니다" in cleared["explain"]
    assert _by_name(cleared, "U_DIVIDEND@t-1")["verdict"] == "not_contributing"

    # 미소거 세계: 같은 검정·같은 효과인데 상한이 내려가고 문장이 바뀐다.
    assert [u["uid"] for u in uncleared["causal"]["uncleared_latents"]] == ["U_DIVIDEND@t-1"]
    assert uncleared["causal"]["ceiling"] == "mechanism_compatible"
    assert "원인으로 확인됐습니다" not in uncleared["explain"]
    assert "관측된 움직임과 양립합니다" in uncleared["explain"]
    assert "배제하지 못했습니다" in uncleared["explain"]
    assert _by_name(uncleared, "U_DIVIDEND@t-1")["verdict"] == "undetermined"

    # 그리고 두 세계의 차이는 상한과 문장뿐이다 - 원인 후보도 검정 결과도 같다.
    assert cleared["causal"]["probable_cause"] == uncleared["causal"]["probable_cause"]
    assert (_by_name(cleared, REBAL_LABEL)["evidence"]["p"]
            == _by_name(uncleared, REBAL_LABEL)["evidence"]["p"])


def test_the_narrator_refuses_to_ship_a_confirmed_phrase_under_a_lowered_ceiling(monkeypatch):
    """상한과 문장이 어긋나면 **예외로 막는다** - 규칙을 주석에만 적어두면 다음 사람이 지운다.

    위 대조 쌍은 지금의 문장 생성기가 규칙을 지킨다는 것을 보인다. 이 테스트가 지키는 것은
    다른 것이다: 누군가 문장을 손보다가 확정 문구를 되살렸을 때 **조용히 나가지 않는다.**
    공개 경로로는 그 상태에 닿을 수 없으므로(그게 설계다) 문장 생성기만 갈아 끼워
    마지막 관문이 실제로 살아 있는지 본다. 이 관문이 없으면 상한은 권고문이 된다.
    """
    real = p8._cause_sentence
    monkeypatch.setattr(p8, "_cause_sentence",
                        lambda d, f: f"{real(d, f)} 원인으로 {p8.CONFIRMED_PHRASE}.")

    with pytest.raises(PipelineError, match="확인 문구"):
        _explain(FakeCausalData(), FakeClient())      # 미소거 U 가 남는 세계


def test_a_discriminator_is_executable_only_when_the_query_actually_returns_rows():
    """모델의 자기 신고는 읽지 않는다. **질의가 낸 것**이 실행 가능성을 정한다."""
    with_sql = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())
    without = _explain(FakeCausalData(), FakeClient())

    hit = next(d for d in with_sql["causal"]["discriminators"] if d["kind"] == "latent")
    assert hit["executable"] is True and hit["sql"] == Q_DISC

    miss = next(d for d in without["causal"]["discriminators"] if d["kind"] == "latent")
    assert miss["executable"] is False
    assert p5.NO_SQL in miss["why_not"]
    # 못 돌린 것은 다음 수집 의제가 된다 - 고객 문장에도 그 자리가 남는다.
    assert "판별에 필요했지만 없는 것" in without["explain"]


# --------------------------------------------------------------------------- #
# P6 민감도 — 식별이 안 될 때 강도를 재는 유일한 값싼 축
# --------------------------------------------------------------------------- #
def test_every_edge_gets_a_sensitivity_row_even_when_the_e_value_cannot_be_computed():
    """미산출도 한 줄로 남아야 "P6 를 안 돌렸다"와 "못 냈다"가 구별된다."""
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    rows = {s["edge"]: s for s in raw["causal"]["sensitivities"]}
    assert set(rows) == {"REBAL@t-1->설명대상_잔차@t0", "DIVIDEND@t-1->설명대상_잔차@t0"}
    assert rows["REBAL@t-1->설명대상_잔차@t0"]["e_value"] > 1.0
    assert "위험비" in rows["REBAL@t-1->설명대상_잔차@t0"]["says"]
    # 검정이 불가였던 간선은 분자가 없다 - 수치를 지어내지 않고 사유를 적는다.
    assert rows["DIVIDEND@t-1->설명대상_잔차@t0"]["e_value"] == 1.0
    assert "미산출" in rows["DIVIDEND@t-1->설명대상_잔차@t0"]["says"]


# --------------------------------------------------------------------------- #
# P7 대조·스크린 — 죽을 조건을 적어 놓고 확인하지 않으면 그 문장은 장식이다
# --------------------------------------------------------------------------- #
def test_a_treated_firm_with_another_disclosure_in_the_window_is_screened_out():
    """사건창 안에 두 공시가 있으면 그 창의 초과수익은 둘의 합이다 - 표본에서 뺀다.

    검사 못 함과 오염 없음은 다른 상태다. 그래서 스크린은 `checked` 와 `n_dropped` 를
    따로 들고, 이름은 SQL 표면이 있을 때만 붙는다(판정은 언제나 코호트가 한다).
    """
    named = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())
    unnamed = _explain(FakeCausalData(), FakeClient())

    screen = named["causal"]["confounding_screen"]
    assert screen["checked"] is True and screen["n_dropped"] == 1
    dropped = screen["dropped"][0]
    assert dropped["instrument_id"] == CONFOUNDED
    assert dropped["conflicting_event_type"] == CONFLICT_TYPE
    assert dropped["conflict_date"] == CONFLICT_DATE.isoformat()

    # 표면이 없으면 **버려지는 표본 수는 같고** 이름만 미상이다 - 배선이 표본을 바꾸면
    # 그건 스크린이 아니라 잡음이다.
    bare = unnamed["causal"]["confounding_screen"]
    assert bare["checked"] is True and bare["n_dropped"] == screen["n_dropped"]
    assert bare["dropped"][0]["conflicting_event_type"] == "미상"


def test_negative_controls_stay_on_the_list_even_when_they_cannot_run():
    """빼면 "돌렸는데 조용했다"와 "못 돌렸다"가 같은 표현(부재)이 된다."""
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    controls = raw["causal"]["negative_controls"]
    assert len(controls) == 3, [c["name"] for c in controls]
    pre = next(c for c in controls if c["kind"] == "outcome")
    assert pre["executed"] is True and pre["passed"] is True, pre["says"]
    blocked = [c for c in controls if not c["executed"]]
    assert blocked and all("실행 불가" in c["says"] for c in blocked)


# --------------------------------------------------------------------------- #
# P8 처분 — 검토한 것은 반드시 셋 중 하나. 침묵은 판정이 아니다
# --------------------------------------------------------------------------- #
def test_every_reviewed_candidate_lands_in_the_ledger_with_a_verdict():
    """보고서에 없는 후보는 "검토했는데 아니었다"인지 "안 봤다"인지 구분되지 않는다.

    다섯 갈래가 전부 들어와야 한다: 산술로 죽은 것 · 각 가설 · 각 U · 지문의 측정 불가
    축 · 예산 미설명분. 하나라도 침묵하면 다음 조사가 어디서 시작할지 모른다.
    """
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    ds = raw["causal"]["dispositions"]
    names = _names(raw)
    assert all(d["why"] for d in ds), "판정만 있고 사유가 없는 행이 있다"
    assert all(d["verdict"] in ("contributing", "not_contributing", "undetermined")
               for d in ds)

    assert _by_name(raw, ZERO_LABEL)["verdict"] == "not_contributing"   # 산술로 죽은 것
    assert "비중이 없어" in _by_name(raw, ZERO_LABEL)["why"]
    assert _by_name(raw, REBAL_LABEL)["verdict"] == "contributing"      # 가설 (기여)
    assert _by_name(raw, CAUSE_LABEL)["verdict"] == "undetermined"      # 가설 (검정 불가)
    assert "검정 불가" in _by_name(raw, CAUSE_LABEL)["why"]
    assert _by_name(raw, "U_DIVIDEND@t-1")                              # U
    assert _by_name(raw, "미설명분")["verdict"] == "undetermined"        # 예산 미설명분

    # 지문의 **모든** 측정 불가 축이 남는다. 하나라도 빠지면 침묵이 통과한 것이다.
    mute = [a["name"] for a in raw["causal"]["fingerprint"] if not a["available"]]
    assert mute, "측정 불가 축이 하나도 없으면 이 검사가 공허하다"
    assert all(f"지문 {name}" in names for name in mute), (mute, names)


def test_the_budget_leaves_the_unexplained_share_in_the_customer_sentence():
    """"설명하지 못했다"가 일급 산출이다. 빼면 남은 문장이 잔차 전체를 설명한 듯 읽힌다."""
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    c = raw["causal"]
    assert c["over_budget"] is False
    assert c["budget"]["n_measured"] == 1 and c["budget"]["n_blocked"] == 1
    left = c["unexplained"]
    assert 0.0 < left < abs(RESIDUAL), left
    assert f"{left * 100:+.2f}%" in raw["explain"]
    assert "설명하지 못하고 남은 몫은" in raw["explain"]


def test_published_body_invents_no_number():
    """본문의 모든 퍼센트가 스텁이 준 값에서 유도된 것이어야 한다.

    실험판 날조는 전부 **모델이 수치를 말할 자리**에서 났다. 자리를 없앤 것이 설계이고,
    이 테스트가 그 설계의 회귀 감시다.
    """
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    found = _pcts(raw["explain"])
    # 감사가 공허해지지 않게: 셀 수 있는 수치가 실제로 본문에 있어야 한다
    # (관측·잔차·기여 2건·귀속 폭·미설명분·잔차 대비 몫).
    assert len(found) >= 6, found
    assert not found - _allowed(raw), f"원장에 없는 수치: {sorted(found - _allowed(raw))}"


def test_the_audit_block_keeps_the_prose_the_ledger_and_the_agent_code():
    """감사 흔적이 없으면 **통과했다는 사실만 남고 통과의 증거가 사라진다.**

    사후에 "무엇을 무엇과 비교해서 이 p 가 나왔는가"를 재구성할 수 있어야 한다:
    설계(층화·조정집합)·산문(주장·메커니즘·반증조건)·원장(placebo 호출 전량)·코드.
    """
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    proof = next(p for p in raw["causal"]["proofs"] if p["edge"] == "REBAL@t-1->설명대상_잔차@t0")
    assert proof["because"] and proof["false_if"]
    assert proof["unit"] == "stock" and proof["null_kind"] == "label"
    assert proof["strata_declared"] is True
    assert proof["ledger"] and proof["ledger"][0]["p"] == proof["p"]
    assert any("placebo(" in c for c in proof["code"])
    assert raw["confidence"] != "높음", "단일 패스에 표본외 확증이 있는 척했다"


def test_an_unmeasurable_edge_becomes_a_data_request_not_a_rejection():
    """데이터 부재는 침묵이 아니라 산출물이다 - 그게 다음 수집 의제가 된다."""
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    proof = next(p for p in raw["causal"]["proofs"] if p["edge"] == "DIVIDEND@t-1->설명대상_잔차@t0")
    assert proof["status"] == "불가"
    req = proof["data_request"]
    assert req["need"] == "장중 체결 흐름(투자자별 순매수) 일별 원장"
    assert req["grain"] == "일별" and req["unlocks"]
    assert req["edge"] == "DIVIDEND@t-1→설명대상_잔차@t0"
    # 기각이 아니라 미결이다 - "찾아봤는데 없다"와 "볼 자료가 없었다"는 다른 말이다.
    assert _by_name(raw, CAUSE_LABEL)["verdict"] == "undetermined"


def test_the_verify_brief_carries_the_type_population_not_a_verdict():
    """프롬프트에는 분포 사실만 싣는다 - 모델이 수치를 물어볼 자리가 없어야 한다."""
    client = FakeClient()

    _explain(FakeCausalData(), client, sql=FakeSql())

    brief = client.prompt_for("DIVIDEND@t-1→설명대상_잔차@t0")
    assert "타입 모집단" in brief and "유효n≈96" in brief
    assert f"최대 {ABS_MAX * 100:.1f}%" in brief
    assert "p=" not in brief          # p값을 보여주면 모델이 그걸 베낀다


# --------------------------------------------------------------------------- #
# 역할·영역·관계 — 원인 **하나를 고르는** 일이 아니라 인과 패키지를 재구성하는 일이다
#
# 셋 다 `narrate` 가 `raw` 에 싣지 않는 값을 하나씩 건드린다(그래프의 관계 목록·커버리지
# 원장). 그래서 관측 지점을 P8 경계로 내린다 - 앞 단계는 전부 진짜로 돌고 여기서 지나가는
# 값을 보기만 한다.
# --------------------------------------------------------------------------- #
def _spy_p8(monkeypatch) -> dict:
    """P8 이 **받은 그래프**와 **낸 원장**을 가로챈다. 스텁이 아니라 관측이다."""
    seen: dict = {}
    real = p8.dispose

    def spy(**kw):
        seen["graph"] = kw["graph"]
        seen["findings"] = real(**kw)
        return seen["findings"]

    monkeypatch.setattr(p8, "dispose", spy)
    return seen


# ── 증폭 ────────────────────────────────────────────────────────────────
# 두 경로의 몫을 함께 담을 만큼 넓은 예산. **관계 회계 사면이 아니라 예산 자체로** 통과
# 시켜서 이 테스트가 역할 축 하나만 검사하게 한다(사면은 아래 관계 테스트가 따로 잡는다).
WIDE_RESIDUAL = 0.0700
AMP_LABEL = "얕아진 유동성"
_AMP_SAYS = "얕은 호가가 같은 매수 규모를 더 큰 가격 변화로 바꾼다"
_AMP_WHY = "잔여 유동성이 적을수록 같은 체결이 호가를 더 멀리 밀어낸다"
_AMP_NODE = {"says": "직전 거래일까지 쌓인 Amihud 비유동성", "observed": "v_liquidity.illiq"}

H_AMPLIFY = {"hypothesis": {
    "says": "직전 거래일까지 호가가 얕아져 있어서 같은 크기의 매수가 평소보다 큰 가격 "
            "변화를 만들었다",
    "cause_label": AMP_LABEL,
    "treatment": "ILLIQ@t-1", "outcome": "설명대상_잔차@t0", "assignment": "natural",
    # 연쇄를 **시작한** 것이 아니라 결과를 **키운** 것이다. 몫이 커도 촉발원이 아니다.
    "role": "amplifier", "domain": "microstructure",
    "nodes": {"ILLIQ@t-1": _AMP_NODE,
              "설명대상_잔차@t0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률"}},
    "edges": [{"from": "ILLIQ@t-1", "to": "설명대상_잔차@t0", "says": _AMP_SAYS, "because": _AMP_WHY}],
    "predicts": ["직전 거래일 비유동성이 높았던 종목의 가격 반응이 더 크다"],
    "distinguishes": ["비유동성 경로면 유동성 상위 종목에서 효과가 사라진다"],
    "denies": ["유동성이 두터웠던 종목에서도 같은 폭의 반응이 난다"],
    "events": []}}

# 증폭 간선도 **같은 사건 모집단**에서 잰다 - 스텁 코호트는 사건 기반 술어 하나만 이해
# 하므로 두 간선의 효과 크기가 같아진다. 그게 오히려 이 테스트의 요점이다: 크기가 같아도
# 칸은 갈린다. 역할은 기여도와 다른 축이라는 것이 정확히 이 뜻이다.
_NODES_AMP = {**_NODES_OK, "ILLIQ@t-1": {**_AMP_NODE, "events": []}}
_EDGES_AMP = [*_EDGES_OK,
              {"from": "ILLIQ@t-1", "to": "설명대상_잔차@t0", "kind": "statistical",
               "says": _AMP_SAYS, "because": _AMP_WHY,
               "false_if": "유동성이 두터운 종목도 같은 폭으로 올랐다면 죽는다",
               "exposure": REBAL_WHERE, "reference": CONTROL_WHERE}]
P3_AMP = [_graph(_NODES_AMP, _EDGES_AMP)]
VERIFY_AMP = {"REBAL@t-1→설명대상_잔차@t0": VERIFY_TURNS,
              "ILLIQ@t-1→설명대상_잔차@t0": VERIFY_TURNS,
              "DIVIDEND@t-1→설명대상_잔차@t0": VERIFY_IMPOSSIBLE}


def test_an_amplifier_that_passes_its_test_lands_beside_the_cause_not_in_it():
    """**촉발원과 증폭은 같은 칸에 들어가지 않는다.**

    Flash Crash 보고서가 "대규모 매도가 원인" 으로 끝났다면 유동성 고갈과 거래정지가 같은
    목록에 들어가 서사가 무너졌을 것이다 (Kirilenko 의 HFT 판정이 "원인 아님, 증폭" 인
    것이 이 구분이다). 증폭을 원인 칸에 넣으면 개입 설계가 달라진다는 사실이 사라진다 -
    지수 규칙을 바꾸는 일과 호가를 두텁게 하는 일은 다른 일이다.

    가르는 축이 강도가 아니라는 것을 여기서 못 박는다: 두 간선은 **같은 표본에서 같은
    효과**를 낸다. 그런데도 하나는 probable_cause 고 하나는 contributing 이다.
    """
    client = FakeClient(hyps=[H_REBAL, H_DIVIDEND, H_AMPLIFY], graph=P3_AMP,
                        verify=VERIFY_AMP)

    raw = _explain(FakeCausalData(residual=WIDE_RESIDUAL), client, sql=FakeSql())

    amp, trigger = _by_name(raw, AMP_LABEL), _by_name(raw, REBAL_LABEL)
    # 증폭도 검정을 통과했다 - 못 재서 밀려난 것이 아니다.
    assert amp["verdict"] == "contributing" and amp["evidence"]["p"] < 0.05
    assert amp["evidence"]["effect"] == pytest.approx(trigger["evidence"]["effect"])
    # 그래도 원인 칸은 촉발원 몫이다.
    assert raw["causal"]["probable_cause"] == [REBAL_LABEL]
    # 그리고 증폭은 침묵하지 않는다 - 고객 문장에 자기 줄로 나간다.
    assert f"결과를 키운 것: {AMP_LABEL}." in raw["explain"]


# ── 커버리지 ────────────────────────────────────────────────────────────
# 가설이 전부 information 인 세계. 후보 목록이 공시·뉴스에서 오므로 **가만히 두면 이렇게
# 된다** - 그 편향이 산출물에서 보이는지가 계약이다.
P3_INFO_ONLY = [_graph({"DIVIDEND@t-1": _NODES_OK["DIVIDEND@t-1"],
                        "설명대상_잔차@t0": _NODES_OK["설명대상_잔차@t0"]}, [_EDGES_OK[1]])]


def test_hypotheses_that_all_come_from_one_domain_leave_the_other_domains_on_the_ledger(
        monkeypatch):
    """**열지 않은 영역에 침묵하지 않는다.**

    P2 에 골격을 주면 모델이 노드를 세우지 않고 칸을 채우므로 어휘는 계속 열어 둔다.
    닫는 것은 커버리지 보고 쪽이다 - 수급·유동성·제도·측정오류·무사건을 아예 안 봤다는
    사실이 원장에 남지 않으면, 뉴스만 뒤진 설명과 여덟 영역을 다 훑고 정보로 좁힌 설명이
    산출물에서 똑같이 생겼다. `not_considered` 가 곧 침묵이고 그 침묵이 이 실패의 지문이다.
    """
    seen = _spy_p8(monkeypatch)
    client = FakeClient(hyps=[H_DIVIDEND, H_NONE], graph=P3_INFO_ONLY)

    raw = _explain(FakeCausalData(), client, sql=FakeSql())

    f, graph = seen["findings"], seen["graph"]
    assert {h.domain for h in graph.hypotheses} == {"information"}, "전제가 안 섰다"

    unopened = f.unopened_domains()
    assert unopened, "한 영역만 열었는데 안 연 영역이 하나도 없다고 보고했다"
    assert {"flow", "no_event"} <= set(unopened), unopened
    # 원장은 **여덟 영역 전부**에 한 줄을 남긴다. 안 연 영역이 목록에서 빠지는 순간
    # "안 봤다"와 "볼 것이 없었다"가 같은 표현(부재)이 된다.
    assert {c.domain for c in f.coverage} == set(DOMAIN_SAY)
    opened = [c for c in f.coverage if c.status == "opened"]
    assert [(c.domain, c.hids) for c in opened] == [("information", ["h1"])]
    # 원장에 없는 것과 못 여는 것도 갈라 적는다 - 호가·깊이는 안 본 게 아니라 없다.
    assert next(c for c in f.coverage if c.domain == "microstructure").status == "unavailable"
    # 그리고 그 쏠림은 처분 하나하나에도 붙어 있다.
    assert {d.domain for d in f.all_dispositions if d.evidence.get("hid")} == {"information"}
    assert Explanation(raw).is_valid


# ── 관계 ────────────────────────────────────────────────────────────────
# 예측이 정면으로 충돌하는 두 촉발원. Platt strong inference 의 그림이고, Zaks 의 판정
# 으로는 `share` 가 **정의되지 않는** 쌍이다 - 그런데 예산 산술은 평탄하게 더한다.
PEER_TYPE = "MARKET.PEER.EARNINGS_SURPRISE"
PEER_WHERE = (f"event_type_code = '{PEER_TYPE}' AND role_code = 'ISSUER'"
              " AND lifecycle_stage = 'CONFIRMED'")
INDEX_LABEL = "지수 편입 규칙"
PEER_LABEL = "동종기업 실적 서프라이즈"
_ONLY_TARGETS = "지수 편입 대상 종목만 참조집단보다 높은 초과수익을 낸다"
_PEERS_TOO = "편입 대상이 아닌 동종기업도 같은 폭으로 오른다"

H_RIVAL_INDEX = {"hypothesis": {
    "says": "지수 리뷰가 확정되면서 패시브 자금이 편입 대상만 사야 했고 그 매수가 대상 "
            "종목의 초과수익을 만들었다",
    "cause_label": INDEX_LABEL,
    "treatment": "INDEX@t-1", "outcome": "설명대상_잔차@t0", "assignment": "mechanical",
    "role": "trigger", "domain": "flow",
    "nodes": {"INDEX@t-1": {"says": "지수 사업자가 확정한 편입 비중 상향",
                            "observed": "지수 사업자 리뷰 결과 공시"},
              "설명대상_잔차@t0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률"}},
    "edges": [{"from": "INDEX@t-1", "to": "설명대상_잔차@t0",
               "says": "확정된 편입이 대상 종목에만 초과수익을 만든다",
               "because": "패시브 추종 자금은 재량이 없어 확정일에 대상만 산다"}],
    "predicts": [_ONLY_TARGETS], "denies": [_PEERS_TOO], "events": []}}

H_RIVAL_PEER = {"hypothesis": {
    "says": "동종기업의 실적 서프라이즈가 같은 수요 사이클을 공유하는 섹터 전체의 기대를 "
            "올려 편입 여부와 무관하게 같은 폭이 나왔다",
    "cause_label": PEER_LABEL,
    "treatment": "PEER@t-1", "outcome": "설명대상_잔차@t0", "assignment": "natural",
    "role": "trigger", "domain": "common_shock",
    "nodes": {"PEER@t-1": {"says": "동종기업 실적 서프라이즈 공시", "observed": "공시 원장"},
              "설명대상_잔차@t0": {"says": "당일 시장대비 초과수익", "observed": "일간 수익률"}},
    "edges": [{"from": "PEER@t-1", "to": "설명대상_잔차@t0",
               "says": "동종기업 실적이 섹터 전체를 함께 올린다",
               "because": "같은 수요 사이클을 공유하므로 한 곳의 실적이 나머지 기대를 바꾼다"}],
    "predicts": [_PEERS_TOO], "denies": [_ONLY_TARGETS],
    "distinguishes": [_ONLY_TARGETS], "events": []}}

_NODES_RIVAL = {
    "INDEX@t-1": {"says": "지수 사업자가 확정한 편입 비중 상향",
                  "observed": "지수 사업자 리뷰 결과 공시", "events": []},
    "PEER@t-1": {"says": "동종기업 실적 서프라이즈 공시", "observed": "공시 원장", "events": []},
    "DIVIDEND@t-1": _NODES_OK["DIVIDEND@t-1"],
    "설명대상_잔차@t0": _NODES_OK["설명대상_잔차@t0"],
}
_EDGES_RIVAL = [
    {"from": "INDEX@t-1", "to": "설명대상_잔차@t0", "kind": "statistical",
     "says": "확정된 편입이 대상 종목에만 초과수익을 만든다",
     "because": "패시브 추종 자금은 재량이 없어 확정일에 대상만 산다",
     "false_if": _PEERS_TOO + "면 죽는다",
     "exposure": REBAL_WHERE, "reference": CONTROL_WHERE},
    {"from": "PEER@t-1", "to": "설명대상_잔차@t0", "kind": "statistical",
     "says": "동종기업 실적이 섹터 전체를 함께 올린다",
     "because": "같은 수요 사이클을 공유하므로 한 곳의 실적이 나머지 기대를 바꾼다",
     "false_if": _ONLY_TARGETS + "면 죽는다",
     "exposure": PEER_WHERE, "reference": CONTROL_WHERE},
    _EDGES_OK[1],
]
P3_RIVAL = [_graph(_NODES_RIVAL, _EDGES_RIVAL)]
# 술어만 갈아 끼운다. 스텁 코호트는 사건 기반 술어에 같은 표본을 돌려주므로 두 경쟁 간선의
# 몫이 같아지고, 그래서 합이 예산을 확실히 넘는다 - 사면이 없으면 둘 다 죽는 구성이다.
_VERIFY_PEER = [{"thought": "동종기업 타입에서 같은 대비를 쌓는다",
                 "code": VERIFY_CODE.replace(REBAL_TYPE, PEER_TYPE)},
                {"thought": "R 완성", "done": True}]
VERIFY_RIVAL = {"INDEX@t-1→설명대상_잔차@t0": VERIFY_TURNS,
                "PEER@t-1→설명대상_잔차@t0": _VERIFY_PEER,
                "DIVIDEND@t-1→설명대상_잔차@t0": VERIFY_IMPOSSIBLE}


def test_two_rival_hypotheses_are_judged_exclusive_and_that_forgives_the_budget(monkeypatch):
    """**정의되지 않은 합이 정상 그래프를 죽이지 않는다.**

    Zaks 2017: relative causal force 는 두 설명이 동시에·독립적으로 결과를 낼 수 있을
    때만 검정할 수 있다. 배타적인 두 가설의 몫을 더하는 것은 정의되지 않은 양을 더해 놓고
    그 합이 크다고 말하는 것이다. 그런데 예산 산술은 평탄하게 더하므로, 관계를 먼저
    판정하지 않으면 **`_verdict_of` 가 둘 다 undetermined 로 떨어뜨린다** - 산술이 그래프를
    죽인다.

    관계는 LLM 이 아니라 코드가 `predicts`/`denies` 에서 유도한다. 합산할지 말지를 정하는
    값이라 모델에게 물으면 원하는 결론으로 가는 손잡이가 되기 때문이다.
    """
    seen = _spy_p8(monkeypatch)
    client = FakeClient(hyps=[H_RIVAL_INDEX, H_RIVAL_PEER, H_DIVIDEND], graph=P3_RIVAL,
                        verify=VERIFY_RIVAL)

    raw = _explain(FakeCausalData(), client, sql=FakeSql())

    rel = seen["graph"].relation("h1", "h2")
    assert rel is not None and rel.kind == "mutually_exclusive", rel
    assert _PEERS_TOO in rel.because, rel.because
    assert seen["graph"].unjudged_pairs() == [], "미판정 쌍이 남으면 몫 배분을 못 믿는다"

    # 합은 실제로 예산을 넘었다. 넘은 채로 사면된 것이지 안 넘은 것이 아니다.
    f = seen["findings"]
    assert f.over_budget is False
    assert "관계 회계로 사면" in f.budget_note and "배타적이다" in f.budget_note
    # 그래서 둘 다 살아 있다 - 산술이 정상 그래프를 죽이지 않았다.
    assert {_by_name(raw, INDEX_LABEL)["verdict"],
            _by_name(raw, PEER_LABEL)["verdict"]} == {"contributing"}
    # 다만 배타적인 두 촉발원이 **동시에** 원인일 수는 없다. 약한 쪽은 한 칸 내려간다.
    assert len(raw["causal"]["probable_cause"]) == 1, raw["causal"]["probable_cause"]
    assert {d.candidate for d in f.contributing} >= (
        {INDEX_LABEL, PEER_LABEL} - set(raw["causal"]["probable_cause"]))


# --------------------------------------------------------------------------- #
# 산술 게이트 — 가장 싼 게이트가 가장 먼저, 가장 세게 돈다
# --------------------------------------------------------------------------- #
def test_zero_weight_candidates_die_before_the_llm_is_ever_called():
    cd, client = FakeCausalData(share=0.0), FakeClient()

    raw = _explain(cd, client, candidates=_candidates(0.0))

    assert client.calls == 0, "산술로 죽을 셀에 LLM 비용을 썼다"
    assert "cohort" not in cd.calls, "검정 표본을 뽑았다 - 순서가 거꾸로다"
    assert Explanation(raw).explanation_type == "UNCERTAIN"
    assert _by_name(raw, CAUSE_LABEL)["verdict"] == "not_contributing"
    assert "비중이 없어" in _by_name(raw, CAUSE_LABEL)["why"]
    assert "확인되지 않았습니다" in raw["explain"]


def test_arithmetic_rejection_body_invents_no_number():
    raw = _explain(FakeCausalData(share=0.0), FakeClient(), candidates=_candidates(0.0))

    assert not _pcts(raw["explain"]) - _allowed(raw)


def test_required_effect_beyond_the_type_maximum_dies_on_arithmetic():
    """비중이 있어도 필요 초과수익이 타입 과거 최대를 넘으면 통계를 볼 필요가 없다."""
    tiny = 0.052            # 실측 사례: 비중 5.20% 로 잔차를 설명하려면 세 자리가 필요했다
    client = FakeClient()

    raw = _explain(FakeCausalData(share=tiny), client, candidates=_candidates(tiny))

    assert client.calls == 0
    killed = _by_name(raw, CAUSE_LABEL)["why"]
    assert f"{abs(RESIDUAL) / tiny * 100:.0f}%" in killed
    assert f"{ABS_MAX * 100:.1f}%" in killed
    assert not _pcts(raw["explain"]) - _allowed(raw)


# --------------------------------------------------------------------------- #
# 검정 실행 — 효과가 없으면 아무것도 살아남지 않는다
# --------------------------------------------------------------------------- #
def test_zero_effect_design_finds_nothing_and_says_so():
    cd, client = FakeCausalData(effect=0.0), FakeClient()

    raw = _explain(cd, client, sql=FakeSql())

    assert client.verifies >= 1, "검정 세션이 열리지 않았다 - 샌드박스 경로가 죽었다"
    assert raw["causal"]["probable_cause"] == []
    assert _by_name(raw, REBAL_LABEL)["verdict"] == "not_contributing"
    assert "귀무와 구분되지 않았다" in _by_name(raw, REBAL_LABEL)["why"]
    assert Explanation(raw).explanation_type == "UNCERTAIN"
    assert "확인됐습니다" not in raw["explain"]


def test_the_planted_effect_is_recovered_and_published_as_the_cause():
    """심은 효과를 되찾는다. 스텁이 준 것 말고 다른 수치가 나오면 계약이 깨진 것이다."""
    cd, client = FakeCausalData(), FakeClient()

    raw = _explain(cd, client, sql=FakeSql())
    ex = Explanation(raw)

    assert ex.is_valid and ex.explanation_type == "EVENT_SUPPORTED"
    assert raw["causal"]["probable_cause"] == [REBAL_LABEL]
    cause = _by_name(raw, REBAL_LABEL)
    assert cause["evidence"]["p"] < 0.05
    assert cause["evidence"]["n"] == len(cd.treated) + len(_CONTROL_IDS) * len(_EVENT_DATES)
    assert cause["evidence"]["effect"] == pytest.approx(EFFECT, abs=3 * NOISE_SD)
    assert cause["contribution"] == pytest.approx(cause["evidence"]["effect"], abs=1e-9)
    body = raw["explain"]
    assert f"{OBSERVED * 100:+.2f}%" in body and f"{RESIDUAL * 100:+.2f}%" in body


def test_sandbox_off_falls_back_to_the_reduced_path_without_calling_the_verifier():
    """ops 킬스위치. 모델 코드를 실행하지 않고도 술어가 있는 간선은 검정된다."""
    cd, client = FakeCausalData(), FakeClient()

    raw = _explain(cd, client, sandbox=False, sql=FakeSql())

    assert client.verifies == 0, "샌드박스를 껐는데 검정 에이전트를 불렀다"
    assert client.p2 and client.p3 and client.p5, "샌드박스만 꺼야 하는데 앞단까지 껐다"
    proofs = {p["edge"]: p for p in raw["causal"]["proofs"]}
    assert proofs["REBAL@t-1->설명대상_잔차@t0"]["p"] < 0.05
    assert proofs["REBAL@t-1->설명대상_잔차@t0"]["turns"] == 0
    # 감사 블록은 빈 값을 키째로 뺀다 - 코드 한 줄도 안 돌았다는 뜻이다.
    assert proofs["REBAL@t-1->설명대상_잔차@t0"].get("code", []) == []
    assert proofs["REBAL@t-1->설명대상_잔차@t0"].get("ledger", []) == []
    # 식별이 안 서는 간선은 축약 경로가 **추정하지 않는다** - OLS 로 밀면 편향을 숨긴다.
    assert proofs["DIVIDEND@t-1->설명대상_잔차@t0"].get("p") is None
    assert any("식별 전략 없음" in g for g in proofs["DIVIDEND@t-1->설명대상_잔차@t0"]["gate_fail"])


def test_transport_error_still_fails_loud():
    """LLM 전송·API 오류는 여전히 전파된다 - 계약 위반만 되먹임으로 돌린다.

    WHY: DeepSeek 클라이언트는 402·타임아웃·응답 붕괴를 재시도 소진 후 **PipelineError**
    로 올린다. 그것을 되먹임으로 삼키면 소스가 죽은 것을 계약 위반으로 오인해 UNCERTAIN
    설명과 함께 런이 초록으로 끝난다(ALPHA-589).
    """
    class _DeadClient:
        def complete_json(self, system: str, user: str) -> dict:
            raise PipelineError("DeepSeek call failed after retries: HTTP Error 402")

    with pytest.raises(PipelineError, match="402"):
        _explain(FakeCausalData(), _DeadClient())


# --------------------------------------------------------------------------- #
# P9 누적 — 단일 사례는 반복으로만 검정력을 얻는다
# --------------------------------------------------------------------------- #
def test_the_registry_accumulates_invocations_across_runs(tmp_path):
    """하루짜리 그래프는 세계에 대한 진술이 아니다 - 같은 기제를 다시 소환했다는 기록만이
    track record 다. 덮어쓰면 이전 소환이 지워지고, 지워진 소환은 track record 가 아니다.

    표면 없는 세계로 돌리는 이유: 미소거 U 와 실행 못 한 판별자가 있어야 `amendment` 에
    행이 생긴다. 한 셀에서는 그냥 실패지만 세 번 반복되면 그건 실패가 아니라 골격에 없는
    구조를 가리킨다 - 침묵으로 사라지면 영원히 안 보인다.
    """
    for _ in range(2):
        _explain(FakeCausalData(), FakeClient(), registry_root=tmp_path)

    for kind in p9.KINDS:
        assert (tmp_path / f"{kind}.jsonl").exists(), kind
    mech = p9.latest(tmp_path, "mechanism")
    assert mech, "기제가 하나도 안 남았다"
    assert {m["n_invocations"] for m in mech.values()} == {2}
    assert {m["version"] for m in mech.values()} == {1}, "주장이 안 바뀌었는데 판이 올랐다"
    inst = p9.latest(tmp_path, "edge_instance")
    assert all(k.endswith(f"{ETF_INSTRUMENT}/{TRADE_DATE}") for k in inst), list(inst)
    amend = p9.latest(tmp_path, "amendment")
    assert amend["latent:U_DIVIDEND@t-1"]["seen_in_cells"] == 2, amend
    assert amend["latent:U_DIVIDEND@t-1"]["promote_candidate"] is False, "2회에 승격했다"


def test_without_a_registry_root_the_explanation_still_ships():
    """누적은 선택 의존이다 - 경로가 없다고 고객 산출을 막지 않는다."""
    raw = _explain(FakeCausalData(), FakeClient(), sql=FakeSql())

    assert Explanation(raw).is_valid


# --------------------------------------------------------------------------- #
# SQL 표면 — 있으면 세 단계가 묻고, 없으면 상한이 내려간다
# --------------------------------------------------------------------------- #
def test_the_sql_surface_is_used_by_p2_p3_and_p5_and_every_query_is_logged():
    """보고된 하나가 아니라 **시도 전부**가 남아야 사후에 무엇을 봤는지 재구성된다."""
    sql = FakeSql()

    raw = _explain(FakeCausalData(), FakeClient(), sql=sql)

    asked = sql.ledger.queries
    assert Q_P2 in asked and Q_P3 in asked and Q_P5 in asked
    assert Q_P3 in raw["causal"]["graph"]["queries"], "P3 가 던진 질의가 그래프에 안 남았다"
    assert Q_P5 in raw["causal"]["queries"] and Q_DISC in raw["causal"]["queries"]


def test_without_a_sql_surface_the_explanation_continues_with_a_lower_ceiling():
    """조회 표면이 없다고 멈추지 않는다. 대신 **조용히 나빠지지 않고** 산출물에 드러난다."""
    raw = _explain(FakeCausalData(), FakeClient())

    assert Explanation(raw).explanation_type == "EVENT_SUPPORTED"
    assert raw["causal"]["queries"] == []
    assert raw["causal"]["graph"].get("queries", []) == []
    assert raw["causal"]["ceiling"] == "mechanism_compatible"
    assert "확인됐습니다" not in raw["explain"]


# --------------------------------------------------------------------------- #
# PIT — as_of 없는 조회는 스텁이 죽인다
# --------------------------------------------------------------------------- #
def test_cohort_without_as_of_is_an_assertion_failure():
    """PIT 강제를 테스트가 지킨다 - 시점 절이 빠지면 결과가 조용히 좋아진다."""
    cd = FakeCausalData()

    with pytest.raises(AssertionError):
        cd.cohort(DIVIDEND_WHERE, as_of="")


# --------------------------------------------------------------------------- #
# pipeline.run 스모크 — 주입만 바꿔 두 경로를 각각 태운다
# --------------------------------------------------------------------------- #
_TRIGGER = PriceTrigger("pmt_1", OBSERVED, "abs", abs_gate=True, rel_gate=False)
_EVENT = EventContext(source_event_id=EVENT_ID, event_type_code=EVENT_TYPE,
                      available_at=f"{EVENT_DATE}T08:30:00+09:00",
                      entity_id=_TREATED_IDS[0], ticker="005930", thread_id="thr_1",
                      novelty_status="NEW", title="분기 배당 확대 결정")


def _settings(*, causal: bool, sandbox: bool = True, registry: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        trigger_id=None,
        trade_date=TRADE_DATE, request_id="req-causal-1", etf_ticker=ETF_TICKER,
        lake_bucket="test-lake",
        result_s3_prefix="s3://test-lake/operations_archive/etf_explanations/",
        release_bundle_version="b1", causal_enabled=causal,
        causal_sandbox_enabled=sandbox, causal_registry_root=registry,
        canonical_manifest="", canonical_database="", canonical_output="",
        domain_docs_bucket="", domain_docs_profile="")


class _FakeLake:
    def load_holdings(self, etf_id, market, trade_date):
        return [Holding("005930", "삼성전자", 1.0)], "2026-07-15"

    def load_returns(self, market, trade_date):
        return {"005930": OBSERVED}


class _FakeStore:
    """트리거·전제는 있는 날. `causal_data()` 와 `sql_surface()` 가 스텁을 돌려준다."""

    def __init__(self, cd: FakeCausalData, sql: FakeSql | None = None) -> None:
        self._cd = cd
        self._sql = sql
        self.calls: list[str] = []
        self.persisted: Explanation | None = None

    def load_entity_index(self):
        return {"005930": _TREATED_IDS[0]}

    def resolve_etf_instrument(self, ticker):
        return (ETF_INSTRUMENT, ETF_NAME)

    def fetch_price_trigger(self, etf_instrument_id, trade_date):
        return _TRIGGER

    def persist_observation_route(self, trigger_id, decomp, route_code, event_search,
                                  entity_index, *, minute=False):
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

    def sql_surface(self, *, as_of: str, trade_date: date):
        self.calls.append("sql_surface")
        assert as_of, "표면에 시점이 안 실렸다 - 클램프가 없다"
        return self._sql


class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)


def _archives(s3: _FakeS3) -> list[dict]:
    return [json.loads(p["Body"].decode("utf-8")) for p in s3.puts]


def test_run_takes_the_causal_route_and_persists_the_explanation():
    cd, s3 = FakeCausalData(), _FakeS3()
    store, client = _FakeStore(cd, FakeSql()), FakeClient()

    code = run(_settings(causal=True), lake=_FakeLake(), store=store, client=client, s3=s3)

    assert code == 0
    assert {"causal_data", "sql_surface", "persist_explanation"} <= set(store.calls)
    assert client.p3_graphs == 1 and client.verifies >= 1
    assert store.persisted.explanation_type == "EVENT_SUPPORTED"
    archived = [a for a in _archives(s3) if a.get("outcome") == "explained"]
    assert archived, [a.get("outcome") for a in _archives(s3)]
    # 런 아카이브는 DB 매핑이 버리는 감사 필드(잔차·처분·원장)를 보존한다.
    causal = archived[0]["explanation"]["causal"]
    assert causal["residual"] == RESIDUAL
    assert causal["dispositions"] and causal["proofs"][0]["ledger"]


def test_run_falls_back_to_the_prompt_route_when_causal_is_disabled():
    """산업분류 백필 전에는 ops 가 이전 경로를 고를 수 있어야 한다(조용히 빈 설명 대신)."""
    cd, s3 = FakeCausalData(), _FakeS3()
    store, client = _FakeStore(cd), LegacyClient()

    code = run(_settings(causal=False), lake=_FakeLake(), store=store, client=client, s3=s3)

    assert code == 0
    assert "causal_data" not in store.calls and "sql_surface" not in store.calls
    assert cd.calls == [], "인과 경로가 꺼졌는데 저장소를 두드렸다"
    assert client.systems and p2.SYSTEM not in client.systems
    assert store.persisted.explanation_type == "MIXED"


def test_sandbox_killswitch_reaches_the_verifier_through_run():
    """`CAUSAL_SANDBOX_ENABLED=false` 가 **실제로** 검정 세션을 끄는가.

    WHY: 킬스위치는 배선이 끝까지 닿아야 킬스위치다. settings 에만 있고 analyze 로
    안 넘어가면 ops 는 껐다고 믿는데 LLM 이 쓴 코드가 계속 실행된다 - 끈 줄 아는 스위치가
    가장 위험하다. 그래서 값이 아니라 **검정 세션 호출 수 0** 을 본다.
    """
    cd, s3 = FakeCausalData(), _FakeS3()
    store, client = _FakeStore(cd, FakeSql()), FakeClient()

    code = run(_settings(causal=True, sandbox=False), lake=_FakeLake(), store=store,
               client=client, s3=s3)

    assert code == 0
    assert client.p3_graphs == 1, "제안·그래프는 그대로 돌아야 한다 - 샌드박스만 끈다"
    assert client.verifies == 0, "샌드박스를 껐는데 검정 세션이 불렸다"


def test_run_wires_the_registry_root_from_settings(tmp_path):
    """레지스트리 경로가 settings 에만 있고 배선이 끊기면 누적이 통째로 사라진다."""
    cd, s3 = FakeCausalData(), _FakeS3()
    store, client = _FakeStore(cd, FakeSql()), FakeClient()

    code = run(_settings(causal=True, registry=str(tmp_path)), lake=_FakeLake(),
               store=store, client=client, s3=s3)

    assert code == 0
    assert p9.latest(tmp_path, "mechanism"), "settings 의 registry_root 가 안 닿았다"


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
    client = FakeClient()

    ex = analyze(client, etf_ticker=ETF_TICKER, etf_name=ETF_NAME,
                 name_by_ticker={"005930": "삼성전자"}, trade_date=TRADE_DATE,
                 decomp=decomp, gate=_TRIGGER, route_code="CONCENTRATED",
                 events=[_EVENT], causal=FakeCausalData(), causal_sql=FakeSql(),
                 etf_instrument_id=ETF_INSTRUMENT)

    assert client.p3_graphs == 1 and client.verifies >= 1
    assert ex.explanation_type == "EVENT_SUPPORTED"
