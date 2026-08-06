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
from functools import lru_cache
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


def _days_from_listing(lake, need: Need) -> int | None:
    """서로 다른 날짜 수를 **파일 목록에서** 센다. 못 세면 None (호출자가 훑는다).

    `LIMIT` 이 안 듣는 이유: `DISTINCT` 는 블로킹 연산자라 상한을 걸어도 전량을 먼저
    훑는다. 실측 - `s3_dg_market` 의 `count(DISTINCT trade_date) LIMIT 2` 가 173.6초
    였다. 답은 '366일' 인데 그걸 얻으려고 13.8GB 를 압축해제한다.

    그런데 `trade_date` 는 **하이브 파티션 키**다 - 값이 경로에 적혀 있다. 그러면
    파일 목록만 읽어도 답이 나온다. 데이터는 한 바이트도 안 읽는다.

    파티션 키가 아닌 열(예: RDB 표의 날짜 열)이면 경로에 없으므로 None 을 돌려주고
    호출자가 원래대로 훑는다 - 여기서 0 을 돌려주면 '날짜 없음' 이 되어 도구가
    부당하게 막힌다.
    """
    path = getattr(lake, "s3", {}).get(need.table)
    if not path:
        return None
    from .duck import LAKE
    key = need.date_col
    try:
        rows = lake.sql(
            f"SELECT count(DISTINCT regexp_extract(file, '{key}=([^/]+)', 1)) "
            f"FROM glob('{LAKE}{path}/**/*') WHERE file LIKE '%{key}=%'")
    except Exception:                            # noqa: BLE001 - 못 세면 훑는다
        return None
    n = int(rows[0][0]) if rows and rows[0][0] is not None else 0
    return n or None


def _probe(lake, need: Need) -> tuple[int, int]:
    """(행 수, 서로 다른 날짜 수) — **요구치에서 멈춘다**. 못 읽으면 (0, 0).

    왜 세지 않고 멈추는가: 게이트가 묻는 것은 통계가 아니라 **문턱**이다(`unmet` 은
    `>= need.rows` 와 `>= need.days` 만 본다). 그런데 정확히 세려다 라이브 게이트의
    92% 를 여기서 썼다 - 실측:

        count(*) FROM s3_dg_market            100.1초 · 13,797,643,403 B 수신
        count(DISTINCT trade_date) 같은 표     96.8초 · 13,797,643,403 B
        count(*) FROM s3_dg_consensus          17.7초 · 12,836,795,035 B
        dg 소요 356.4초 / 게이트 전체 386.6초

    2.68억 행이 gzip CSV 라 행그룹·열 프루닝이 없다 - `count(*)` 가 전량 압축해제다.
    13.8GB 를 노트북으로 끌어와서 얻는 답이 '366일' 이었다.

    상한을 걸면 숫자의 뜻이 바뀌지만 **판정은 안 바뀐다**. 그리고 `unmet` 의 문구는
    미달일 때만 찍히는데, 미달이면 상한에 닿지 못했다는 뜻이므로 그때 찍히는 숫자는
    여전히 실수다 - 거짓말이 되지 않는다.

    날짜 열이 없는 표(예: 마스터)는 일수 0 이고, 그런 표에 일수를 요구하면 안 된다.
    """
    try:
        n = int(lake.sql(f"SELECT count(*) FROM (SELECT 1 FROM {need.table} "
                         f"LIMIT {max(need.rows, 1)})")[0][0])
    except Exception:                            # noqa: BLE001 - 부재는 0 행이다
        return 0, 0
    if not need.days:
        return n, 0
    # **목록이 먼저다.** 하이브 파티션이면 데이터를 안 읽고 답이 나온다.
    listed = _days_from_listing(lake, need)
    if listed is not None:
        return n, listed
    try:
        d = int(lake.sql(f"SELECT count(*) FROM (SELECT DISTINCT {need.date_col} "
                         f"FROM {need.table} LIMIT {max(need.days, 1)})")[0][0])
    except Exception:                            # noqa: BLE001 - 열 부재도 0 일이다
        return n, 0
    return n, d


@lru_cache(maxsize=8)
def _coverage_cached(lake, sig: tuple) -> dict[str, tuple[int, int]]:
    """레이크·요구 조합당 한 번만 잰다.

    캐시가 없으면 도구 호출마다 커버리지를 다시 센다. 실측: `s3_dg_market` 은 2.68억
    행이라 `count(*)` + `count(DISTINCT trade_date)` 가 **48초**다. 주장 다섯 개를
    검사하면 4분이 커버리지 확인에만 간다 - 도구가 아니라 게이트가 병목이 된다.

    `sig` 는 요구 서명이다. 도구가 새로 등록되면 서명이 바뀌어 다시 잰다 - 등록 후에도
    옛 커버리지를 쓰면 새 도구가 '데이터 부재' 로 조용히 사라진다.
    """
    return {t: _probe(lake, n) for t, n in sig}


def coverage(lake) -> dict[str, tuple[int, int]]:
    """요구된 표마다 (행, 일). 표당 최대 2질의 - **레이크당 한 번만** 잰다."""
    want: dict[str, Need] = {}
    for tool in TOOLS.values():
        for n in tool.wants:
            # 같은 표를 여러 도구가 요구하면 **가장 센 요구**로 잰다(일수 최대).
            cur = want.get(n.table)
            if cur is None or n.days > cur.days:
                want[n.table] = n
    return _coverage_cached(lake, tuple(sorted(want.items())))


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
@register("edge_tests", "스키마로 고른 튜플 후보 전부를 한 호출로 일간 패널 검정한다. "
                        "중간 결과를 보고 후보를 바꾸지 못하며 α/m을 같은 목록에 적용한다.",
          needs=("layers_daily",), vocab=("가격잔차", "거래량", "주주", "신용", "공매도",
                                          "배수", "주식수", "수급", "지수잔차", "국면",
                                          "거시", "금리", "섹터", "레버리지", "수익성",
                                          "성장", "재무파생"))
def _edge_tests(lake, *, tuples, day: str, instrument_id: str = "") -> dict:
    from .paneltest import edge_tests
    rows = []
    for t, r in edge_tests(lake, tuples, day, instrument_id):
        hi, lo = r.effect_high, r.effect_low
        rows.append({"type": t.trigger.ident, "channel": t.channel,
                     "verdict": r.verdict, "n": r.n, "p": r.p, "reason": r.reason,
                     "effect_high": hi, "effect_low": lo, "applied": r.applied,
                     "counterfactual": r.counterfactual,
                     "signed": (hi - lo) if hi is not None and lo is not None else None})
    return {"verdict": "계산됨", "rows": rows}




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
from . import tool_baserate, tool_consensus, tool_dg, tool_fin, tool_business, tool_flow, tool_peer, tool_stability

__all__ = ["MIN_ROWS", "Need", "SurfaceError", "TOOLS", "Tool", "audit_vocab",
           "available", "blocked", "call", "catalog", "coverage", "register"]
