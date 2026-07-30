"""간선 추정과 게이트 — **모델은 설계를 선언하고, 코드가 실행한다.**

실험판(`experiments/storm`)의 검정 에이전트는 파이썬을 써서 샌드박스에서 `exec` 했다.
프로덕션에서는 그러지 않는다. 두 가지 이유다:

1. 클라우드 서비스에서 모델 생성 코드를 실행하는 건 다른 종류의 위험이다.
2. **필요가 없어졌다.** 처치·대조를 SQL 술어로 선언하면 나머지가 전부 코드에 고정된다 -
   결과는 초과수익, 조정집합은 그래프에서 유도, 통계량은 OLS 계수, 귀무는 층화 순열.
   그러면 실행 가능성이 **구성상** 보장된다.

그게 실험판 실패의 대부분을 문법적으로 없앤다. 실측된 실패와 그 처방:

    단위 불일치 (스칼라를 8관측에 회귀)   -> x·y·z 가 같은 pairs 에서 나온다
    검정력 0 (단일 셀 n=8)               -> scope=type 이면 코호트가 타입 전체다
    귀무 퇴화 (stat 이 순열을 안 읽음)     -> 순열을 코드가 적용한다
    손으로 쓴 p                           -> 모델이 수치를 쓸 자리가 없다
    층화 누락 (자유 순열)                 -> strata 를 선언하게 하고 코드가 만든다

잃는 것: 모델이 새 추정량을 발명할 수 없다. 실측상 모델은 그걸 잘 못 했고(단위 불일치·
퇴화 귀무·날조), 잘한 것은 **무엇을 무엇과 비교할지**였다. 그건 술어로 그대로 표현된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from ..config import PipelineError
from . import graph as G
from . import stats as S

# scope 가 최소 표본을 정한다. cell 은 원리적으로 작다 - 막지 않고 요구만 낮춘다.
NMIN = {"cell": 8, "type": 30}
# 층화 어휘. 대조군을 무엇 안에서 골랐으면 귀무도 그 안에서 섞어야 한다.
STRATA = ("date", "date_industry", "none")
N_NULL = 1000


@dataclass(frozen=True, slots=True)
class EdgeDesign:
    """간선 하나의 **조작적 정의**. 정의가 곧 설계다 - 문자열로 남아 감사·적층된다."""

    src: str                     # DAG 노드 id (원인)
    dst: str                     # DAG 노드 id (결과)
    treated: str                 # 처치 코호트 SQL 술어 (사건 기반)
    control: str                 # 대조 코호트 SQL 술어 (금융상품 기반)
    strata: str = "date"         # 귀무의 교환가능성. 설계가 조건화한 것을 보존한다
    scope: str = "type"          # cell | type
    because: str = ""            # 메커니즘. 반증층이 공격하는 표면
    false_if: str = ""           # 무엇이 보이면 이 간선이 죽나
    timing: str = "unscheduled"  # scheduled | unscheduled | price_responsive | n/a
    cause_label: str = ""        # 고객이 읽을 원인 이름


@dataclass(frozen=True, slots=True)
class EdgeResult:
    """추정 결과. **수치는 전부 코드가 만든 것이다.**"""

    design: EdgeDesign
    n: int = 0
    n_treated: int = 0
    effect: float | None = None
    p: float | None = None
    null_sd: float | None = None
    null_kind: str | None = None
    adjust: list[str] = field(default_factory=list)
    strategy: str = "adjustment"
    gate_fail: list[str] = field(default_factory=list)
    iv: list[str] = field(default_factory=list)
    treated_ids: list[str] = field(default_factory=list)   # 비중 계산의 입력

    @property
    def passed(self) -> bool:
        return not self.gate_fail and self.p is not None

    @property
    def significant(self) -> bool:
        return self.passed and self.p is not None and self.p < 0.05


def identify(nodes: dict, edges: list, src: str, dst: str) -> dict:
    """**식별 전략을 코드가 정한다.** 조정이 되면 조정, 안 되면 IV, 둘 다 없으면 불가.

    조정집합을 모델에게 묻지 않는다 - 실측 정답률 78% 인데 코드는 구성상 100% 다.
    """
    d, b = G.split(edges)
    pool = set(nodes)
    zs = G.admg_minimal_backdoor(d, b, src, dst, pool)
    if zs:
        return {"strategy": "adjustment", "adjust": zs[0], "alternatives": zs[1:], "iv": []}
    ivs = G.iv_candidates(d, b, src, dst, pool)
    return {"strategy": "iv" if ivs else "none", "adjust": [], "alternatives": [], "iv": ivs}


def _strata_key(strata: str, pairs, industry: dict | None) -> np.ndarray | None:
    if strata == "none":
        return None
    if strata == "date":
        return np.array([str(dt)[:10] for _, dt in pairs])
    if strata == "date_industry":
        ind = industry or {}
        return np.array([f"{str(dt)[:10]}|{ind.get(i, '?')}" for i, dt in pairs])
    raise PipelineError(f"strata={strata!r} 는 어휘 밖이다: {STRATA}")


def _beta(x: np.ndarray, y: np.ndarray, zs: list[np.ndarray]) -> float:
    A = np.column_stack([np.ones(len(x)), x, *zs]) if zs else np.column_stack([np.ones(len(x)), x])
    return float(np.linalg.lstsq(A, y, rcond=None)[0][1])


def estimate(cd, design: EdgeDesign, *, as_of: str, w0: date, w1: date,
             adjust: list[str], industry: dict | None = None,
             n_null: int = N_NULL, seed: int = 0) -> EdgeResult:
    """설계를 실행한다. 게이트는 실행 **후**에 본다 - 통과 못 하면 수치를 안 쓴다.

    `adjust` 는 `identify()` 가 정한 것이고 이름은 `MOM`·`VOL` 접두로 해석한다
    (그래프 노드 id 를 관측 열로 잇는 유일한 자리 - 여기서만 규약을 안다).
    """
    if design.strata not in STRATA:
        raise PipelineError(f"strata={design.strata!r} 는 어휘 밖이다: {STRATA}")

    treated = cd.cohort(design.treated, as_of=as_of, w0=w0, w1=w1)
    fail: list[str] = []
    if not treated:
        return EdgeResult(design=design, adjust=adjust, gate_fail=["처치 코호트가 비었다"])
    dates = sorted({dt for _, dt in treated})
    control = cd.universe(design.control, dates, exclude=treated)
    if not control:
        return EdgeResult(design=design, adjust=adjust, gate_fail=["대조 코호트가 비었다"])

    pairs = list(treated) + list(control)
    t_ids = sorted({i for i, _ in treated})
    x = np.array([1.0] * len(treated) + [0.0] * len(control))
    y = cd.ar(pairs)
    cols: dict[str, np.ndarray] = {}
    for name in adjust:
        upper = name.upper()
        if upper.startswith("VOL"):
            cols[name] = cd.vol(pairs)
        else:
            cols[name] = cd.mom(pairs)

    ok = np.isfinite(y) & np.isfinite(x)
    for v in cols.values():
        ok &= np.isfinite(v)
    pairs = [p for p, keep in zip(pairs, ok) if keep]
    x, y = x[ok], y[ok]
    zs = [v[ok] for v in cols.values()]

    n, n_t = len(y), int(x.sum())
    n_min = NMIN.get(design.scope, 8)
    if n < n_min:
        fail.append(f"표본 {n} < {n_min} (scope={design.scope})")
    if n_t < 2 or n_t == n:
        fail.append(f"처치 대비가 없다 (처치 {n_t} / 전체 {n})")
    if n and float(np.var(y)) == 0.0:
        fail.append("결과 분산이 0 이다")
    if fail:
        return EdgeResult(design=design, n=n, n_treated=n_t, adjust=adjust,
                          gate_fail=fail, treated_ids=t_ids)

    obs = _beta(x, y, zs)
    strata = _strata_key(design.strata, pairs, industry)
    nulls = S.permute(x, strata=strata, n=n_null, seed=seed)
    test = S.placebo(lambda w: _beta(w["x"], y, zs), {"x": x}, nulls, null_kind="label")
    if not test.get("testable"):
        return EdgeResult(design=design, n=n, n_treated=n_t, effect=obs, adjust=adjust,
                          treated_ids=t_ids,
                          gate_fail=[f"귀무 불가: {test.get('reason', '?')}"])
    return EdgeResult(design=design, n=n, n_treated=n_t, effect=obs, p=test["p"],
                      null_sd=test.get("null_sd"), null_kind=test.get("null_kind"),
                      adjust=adjust, treated_ids=t_ids)


def arithmetic_gate(residual: float, share: float | None, prior: dict) -> str | None:
    """**가장 싼 게이트. LLM 전에 돈다.**

    무게 없는 원인은 산술로 죽는다 - 필요 초과수익이 그 타입의 과거 최대를 넘으면
    통계를 볼 필요가 없다. 실측: 지명 2종 비중 5.20% 로 잔차 13.36% 를 설명하려면
    +257% 가 필요했고 그 타입의 과거 최대는 39.3% 였다.

    죽으면 사유(고객 문장에 쓸 수 있는 한 문장)를, 살면 None 을 돌려준다.
    """
    if not share:
        return "그 종목들은 이 ETF 에서 비중이 없어 이만한 움직임을 만들 수 없었습니다."
    need = abs(residual) / share
    mx = prior.get("abs_max")
    if mx and need > mx:
        return (f"그 종목들의 비중({share * 100:.2f}%)으로 이 움직임을 설명하려면 "
                f"{need * 100:.0f}% 의 초과수익이 필요한데, 같은 종류의 과거 사건에서 "
                f"관측된 최대는 {mx * 100:.1f}% 였습니다.")
    return None
