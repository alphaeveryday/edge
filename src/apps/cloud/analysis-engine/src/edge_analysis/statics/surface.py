"""공유 도구 표면 — 가설·검정·신뢰성 세 에이전트가 **같은 목록**만 본다.

금융권 납품에서 깨지는 지점은 늘 같다: 산문이 근거를 넘어서고, 그 사실을 아무도
기계로 확인할 수 없다. 그래서 여기 두 가지를 강제한다.

1. **미등록 도구는 못 부른다.** 에이전트가 이름을 지어내면 즉사한다(`call`).
   자유서술로 "통계적으로 유의하다" 를 쓰는 길이 원천적으로 막힌다.
2. **못 쓰는 도구는 사유가 붙는다.** `needs` 의 표가 비어 있으면 `available` 에서
   빠지고 `catalog` 가 왜 빠졌는지 적는다 — 조용히 없으면 에이전트는 그 축을 못
   본다는 사실조차 모르고, 산문은 부재를 기각으로 위장한다.

세 에이전트의 차이는 **표면이 아니라 목적**이다: 가설은 튜플을 만들고, 검정은 판정을
내고, 신뢰성은 이미 쓴 주장에 근거가 붙는지 확인하고 부족하면 도구를 더 부른다.
같은 표면을 쓰므로 세 번째가 첫 번째의 접지를 넘어설 수 없다.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

# 레이크 표가 이만큼도 없으면 그 도구는 쓸 수 없다. 0 행 표를 '있다' 고 세면
# 도구가 빈 결과를 내고 에이전트는 그것을 '효과 없음' 으로 읽는다(부재≠기각).
MIN_ROWS = 1


@dataclass(frozen=True, slots=True)
class Need:
    """도구가 요구하는 **커버리지**. 표 이름만으로는 부족하다.

    실측이 그 부족을 보였다: `flow_detail` 이 `s3_investor_flow` 로 20일 누적을
    요구했는데 그 표는 **11거래일뿐**이었다. '표가 있다' 는 검사를 통과하고, 도구는
    매번 창 미충족으로 판정불가를 냈다 - 감사가 "이 20일 누적은 며칠짜리 표로 계산
    했나" 를 물으면 답이 없었다. 요구를 숫자로 적으면 그 질문에 코드가 답한다.

    `days` 는 그 표의 **서로 다른 날짜 수**다. 열 이름이 표마다 다르므로
    `date_col` 로 받는다 - 없으면 행 수만 본다.
    """

    table: str
    rows: int = MIN_ROWS
    days: int = 0
    date_col: str = "trade_date"

    def unmet(self, cov: dict[str, tuple[int, int]]) -> str:
        """충족하지 못한 사유. 빈 문자열이면 충족."""
        r, d = cov.get(self.table, (0, 0))
        if r < self.rows:
            return f"{self.table} {r}행 < 요구 {self.rows}행"
        if self.days and d < self.days:
            return f"{self.table} {d}일 < 요구 {self.days}일 (행은 {r}개 있다)"
        return ""


@dataclass(frozen=True, slots=True)
class Tool:
    """도구 하나. `what` 은 프롬프트에 **그대로** 들어가므로 한 문장으로 쓴다."""

    name: str
    what: str
    # `str` 은 "행 1개 이상" 의 줄임이다. 일수 요구가 있으면 `Need` 를 쓴다.
    needs: tuple[str | Need, ...]
    vocab: tuple[str, ...]          # 이 도구가 닫는 어휘 슬롯 (감사용)

    @property
    def wants(self) -> tuple[Need, ...]:
        """`needs` 를 전부 `Need` 로 정규화. 호출자가 두 형태를 신경 쓰지 않게."""
        return tuple(Need(n) if isinstance(n, str) else n for n in self.needs)
    fn: Callable[..., dict] = field(compare=False, repr=False)


TOOLS: dict[str, Tool] = {}


class SurfaceError(RuntimeError):
    """표면 위반 — 없는 도구를 부르거나, 못 쓰는 도구를 불렀다."""


def register(name: str, what: str, *, needs: tuple[str, ...] = (),
             vocab: tuple[str, ...] = ()) -> Callable:
    """도구 등록 데코레이터. 이름 중복은 즉사 — 두 도구가 한 이름이면 어느 쪽이
    돌았는지 사후에 못 가린다(재현성 상실)."""
    def deco(fn: Callable[..., dict]) -> Callable[..., dict]:
        if name in TOOLS:
            raise SurfaceError(f"도구 이름 중복: {name}")
        TOOLS[name] = Tool(name, what, needs, vocab, fn)
        return fn
    return deco


def _probe(lake, need: Need) -> tuple[int, int]:
    """(행 수, 서로 다른 날짜 수). 못 읽으면 (0, 0) — **예외를 삼키지 않고 0 으로 말한다**.

    날짜 열이 없는 표(예: 마스터)는 일수 0 이고, 그런 표에 일수를 요구하면 안 된다.
    """
    try:
        n = int(lake.sql(f"SELECT count(*) FROM {need.table}")[0][0])
    except Exception:                            # noqa: BLE001 - 부재는 0 행이다
        return 0, 0
    if not need.days:
        return n, 0
    try:
        d = int(lake.sql(f"SELECT count(DISTINCT {need.date_col}) "
                         f"FROM {need.table}")[0][0])
    except Exception:                            # noqa: BLE001 - 열 부재도 0 일이다
        return n, 0
    return n, d


def coverage(lake) -> dict[str, tuple[int, int]]:
    """요구된 표마다 (행, 일). 표당 최대 2질의 - 한 번 재서 돌려쓴다."""
    want: dict[str, Need] = {}
    for tool in TOOLS.values():
        for n in tool.wants:
            # 같은 표를 여러 도구가 요구하면 **가장 센 요구**로 잰다(일수 최대).
            cur = want.get(n.table)
            if cur is None or n.days > cur.days:
                want[n.table] = n
    return {t: _probe(lake, n) for t, n in sorted(want.items())}


def available(lake, cov: dict[str, tuple[int, int]] | None = None) -> list[Tool]:
    """지금 **실제로 부를 수 있는** 도구만. 커버리지가 요구에 못 미치면 빠진다."""
    c = cov if cov is not None else coverage(lake)
    return [t for t in TOOLS.values() if not any(n.unmet(c) for n in t.wants)]


def blocked(lake, cov: dict[str, tuple[int, int]] | None = None
            ) -> list[tuple[Tool, str]]:
    """못 부르는 도구와 **사유**. 이 목록이 비어 보이면 감사가 통과한 게 아니라
    감사를 안 한 것이다 — 부재는 이름과 요구 미달의 크기까지 드러나야 한다."""
    c = cov if cov is not None else coverage(lake)
    out = []
    for t in TOOLS.values():
        miss = [m for n in t.wants if (m := n.unmet(c))]
        if miss:
            out.append((t, "커버리지 미달: " + " · ".join(miss)))
    return out


def catalog(lake) -> str:
    """프롬프트용 표면 설명. **쓸 수 있는 것과 못 쓰는 이유를 같이** 준다.

    못 쓰는 목록을 감추면 에이전트는 그 축을 안 물어보고, 산문은 그 축이 검토된 것처럼
    읽힌다. 금융권 감사가 정확히 그 지점을 찍는다: '이건 왜 안 봤나'.
    """
    cov = coverage(lake)
    lines = ["-- 부를 수 있는 도구 --"]
    for t in available(lake, cov):
        lines.append(f"  {t.name}: {t.what}")
    bad = blocked(lake, cov)
    if bad:
        lines.append("-- 못 부르는 도구 (사유) --")
        for t, why in bad:
            lines.append(f"  {t.name}: {why} — 이 축은 **검토되지 않았다**"
                         " (효과 없음이 아니다)")
    return "\n".join(lines)


def call(lake, name: str, **kw) -> dict:
    """도구 호출. 미등록 이름과 데이터 부재를 **둘 다** 즉사로 만든다.

    미등록을 막는 이유: 에이전트가 이름을 지어내면 그건 도구가 아니라 자유서술이다.
    부재를 막는 이유: 빈 결과를 돌려주면 호출자가 그것을 '효과 없음' 으로 읽는다.
    """
    if name not in TOOLS:
        raise SurfaceError(
            f"등록되지 않은 도구: {name!r} — 쓸 수 있는 것은 "
            f"{sorted(t.name for t in available(lake))}")
    t = TOOLS[name]
    cov = coverage(lake)
    miss = [m for n in t.wants if (m := n.unmet(cov))]
    if miss:
        raise SurfaceError(f"{name}: 커버리지 미달로 호출 불가 — {miss}")
    # 호출자는 셀 문맥(day·instrument_id·etype…)을 통째로 넘긴다. 도구마다 필요한
    # 것만 골라 넘긴다 - `**kw` 를 모든 도구에 달게 하면 오타 인자가 조용히 먹히고
    # "왜 그 값이 안 먹었나" 를 사후에 못 가린다. 서명이 계약이다.
    sig = inspect.signature(t.fn).parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.values()):
        return t.fn(lake, **kw)
    ok = {k: v for k, v in kw.items() if k in sig}
    need = [k for k, p in sig.items()
            if k != "lake" and p.default is inspect.Parameter.empty and k not in ok]
    if need:
        raise SurfaceError(f"{name}: 필수 인자 부재 — {need}")
    return t.fn(lake, **ok)


def audit_vocab() -> dict[str, list[str]]:
    """어휘 감사 — 어느 계열족이 **어느 도구에도** 안 걸려 있는가.

    닫힌 어휘의 값은 유한성이고, 유한하니 전수 감사가 가능하다. 걸리지 않은 족은
    가설 에이전트가 고를 수 있는데 검정할 수 없는 슬롯이다 — 그게 판정불가의 절반이다.
    """
    from .vocab import SERIES_FAMILIES
    bound = {v for t in TOOLS.values() for v in t.vocab}
    return {"bound": sorted(bound),
            "unbound_family": sorted(f for f in SERIES_FAMILIES if f not in bound)}


# ── 기존 도구 등록 (세 에이전트가 공유하는 최소 표면) ────────────────────────
@register("edge_test", "튜플(사건타입×노출×취약성) 하나를 일간 패널로 검정해 판정·크기·"
                       "반사실을 낸다. 확증 경로.",
          needs=("layers_daily",), vocab=("가격잔차", "거래량", "주주", "신용", "공매도",
                                          "배수", "주식수", "수급", "지수잔차", "국면",
                                          "거시", "금리", "섹터", "레버리지", "수익성",
                                          "성장", "재무파생"))
def _edge_test(lake, *, tup, day: str, instrument_id: str = "",
               layer: str = "고유", m_tests: int = 1) -> dict:
    from .paneltest import edge_test
    r = edge_test(lake, tup, day, instrument_id, layer, m_tests)
    hi, lo = r.effect_high, r.effect_low
    return {"verdict": r.verdict, "n": r.n, "p": r.p, "reason": r.reason,
            "effect_high": hi, "effect_low": lo, "applied": r.applied,
            "counterfactual": r.counterfactual,
            # 상·하위 노출군 차이가 부호 있는 양이다(효과 방향).
            "signed": (hi - lo) if hi is not None and lo is not None else None}


@register("grid_screen", "그날 사건타입 × 측정가능 노출을 **전수** 훑어 후보를 순위로 낸다. "
                         "탐색 경로(p 양측) — 확증은 edge_test 가 한다.",
          needs=("layers_daily",))
def _grid(lake, *, day: str, types: list[str], max_rows: int = 6) -> dict:
    from .paneltest import grid_screen
    return {"verdict": "계산됨", "rows": grid_screen(lake, day, types, max_rows)}


@register("run_trial", "사건을 처치로 보고 매칭 대조군(위약)과 비교해 ATT 와 조절자 "
                       "교호를 낸다. RCT 근사 — 사전추세·균형까지 같이 낸다.",
          needs=("layers_daily",))
def _trial(lake, *, day: str, etype: str, layer: str = "고유",
           moderators: list[str] | None = None) -> dict:
    from .trial import run_trial
    r = run_trial(lake, day, etype=etype, layer=layer, moderators=moderators)
    # `signed` = 방향이 뜻을 갖는 양. ATT 는 처치효과라 부호가 뜻이다.
    return {**r, "signed": r.get("att")}


@register("macro_z", "그날 거시 계열(해외지수·환율·금리)의 발화 z 와 **무엇이 움직였는지** "
                     "이름을 낸다. 이름 없이 z 만 주면 검정 불가능한 문장이 된다.",
          needs=("s3_index_daily",), vocab=("거시", "금리"))
def _macro(lake, *, day: str) -> dict:
    from .paneltest import macro_z
    z, who = macro_z(lake, day)
    # `signed=None`: z 는 **절댓값 최대**라 방향이 없다. 이걸 명시하지 않으면
    # 호출자가 크기를 방향으로 읽는다(실측으로 그 버그가 났다).
    return {"verdict": "계산됨" if who else "판정불가", "signed": None,
            "z": z, "who": who, "reason": "" if who else "거시 계열 부재"}


@register("series_z", "그 종목 그날의 계열 혁신 z (가격잔차·거래량). 계열 방아쇠가 "
                      "발화했는지 정하는 단일 원천.",
          needs=("layers_daily",), vocab=("가격잔차", "거래량"))
def _series_z(lake, *, instrument_id: str, day: str) -> dict:
    from .paneltest import series_z
    zs = series_z(lake, instrument_id, day)
    # 계열족마다 z 가 따로라 '어느 것' 인지 호출자가 정해야 한다 - 하나로 접으면
    # 어느 계열이 방향을 준 건지 사후에 못 가린다. 그래서 signed 는 비운다.
    return {"verdict": "계산됨" if zs else "판정불가", "signed": None, "z": zs,
            "reason": "" if zs else "그날 계열 값 부재"}


# ── 추가 도구 등록 (import 부작용으로 TOOLS 에 들어간다) ──────────────────
#
# 파일 밑에 두는 이유: 각 모듈이 `from .surface import register` 로 이 모듈을 다시
# 부르므로, 위쪽 정의가 모두 끝난 뒤여야 순환 import 가 성립한다.
from . import (            # noqa: E402,F401 - 등록 부작용이 목적이다
    tool_baserate,
    tool_business,
    tool_flow,
    tool_peer,
    tool_stability,
)

__all__ = ["MIN_ROWS", "Need", "SurfaceError", "TOOLS", "Tool", "audit_vocab",
           "available", "blocked", "call", "catalog", "coverage", "register"]
