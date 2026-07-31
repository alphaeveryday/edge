"""간선 하나의 검정 — **모델은 숫자를 쓰지 않는다. 코드가 계산해서 `R` 에 담는다.**

실험판 `experiments/storm/src/storm/verify.py` 를 클라우드 표면으로 이식한 것이다.
제안 에이전트와 다른 세션이고, 보는 것도 다르다:

  · **그래프를 안 보여준다.** 보면 자기 검정이 쉬워지도록 구조를 재해석한다(스펙 쇼핑).
  · **조정집합은 코드가 그래프에서 유도해 내려준다.** 물어보지 않는다
    (실측: 모델의 뒷문 정답률 78%, 코드는 구성상 100%).
  · **결론 JSON 에 수치가 없다.** 값은 전부 샌드박스의 `R` 에서 읽는다. 모델이
    타이핑할 자리가 없으면 날조할 자리도 없다.
  · 세 번째 출력 `impossible` 을 허용한다 — 기술된 대로 검정 불가면 **데이터 요청**으로
    돌린다. 침묵이 아니라 산출물이다. 무엇이 없어서 못 했는지가 다음 수집 의제가 된다.

`claims` 가 `null_kind` 를 정한다. 이게 이 모듈의 두 번째 축이다: 셀은 큰 특이수익으로
**선정됐으므로** "이 날이 특별한가"(`null_kind="date"`)는 거의 자동으로 유의하다(선택
순환). 그래서 귀속 주장(L4)에는 date 를 쓸 수 없다 — G6 가 집행한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from ..observability import log
from . import sandbox as SB
from .engine import NMIN, EdgeDesign, identify

MAX_TURNS = 6

# 주장 층위(`engine.CLAIMS`)마다 쓸 수 있는 귀무. **제안이 층위를 선언하고 허용 귀무는
# 코드가 내려준다** - 둘을 같은 자리에서 고르게 하면 검정이 쉬운 쪽으로 흐른다.
NULL_OK = {
    "L2": {"date", "time", "entity", "label"},   # 우연보다 큰가
    "L3": {"label", "entity"},                   # 부호가 이 방향인가
    "L4": {"label"},                             # 이 **원인**에 돌린다
    "L4e": {"entity"},                           # 이 **종목**에 돌린다 (다른 estimand)
}
CLAIM_SAY = {
    "L2": "우연보다 큰가",
    "L3": "부호가 이 방향인가",
    "L4": "이 원인에 귀속되나",
    "L4e": "이 종목에 귀속되나",
}

# 부호를 주장하는 층위. **양측 p 는 "달랐다"의 p 이므로 방향을 세우지 못한다.**
# L4·L4e 를 넣지 않는 이유: 귀속의 부호와 크기는 예산 정합(`chain.budget`)이 잔차와
# 대조해 따로 본다. 같은 것을 두 자리에서 강제하면 정상 설계가 이중으로 걸린다.
DIRECTIONAL = ("L3",)

TOOLS = """검정 도구. **전략은 없다 - 네가 만든다.** 시점(as_of)은 코드가 이미 박아 뒀다.

  ══ 비교군 만들기. 술어가 곧 설계다 ══
  cohort(where, w0=None, w1=None) -> [(instrument_id, date), ...]
      **사건 기반.** 순수 WHERE 조건만. 쓸 수 있는 컬럼:
        instrument_id · trade_date · event_type_code · predicate_code · role_code
        · lifecycle_stage · sector_name · industry_name · market_cap
        · listing_market · ticker
      PIT(available_at <= as_of)는 **코드가 강제**한다 - 술어에 available_at 금지.
      세미콜론·주석·집합연산 금지. w0/w1 로 창을 넓혀라(기본 W0~W1, 상한 TRADE_DATE).
      예) cohort("event_type_code LIKE '%ANALYST%'", w0='2025-07-01')
  universe(where, dates, exclude=None) -> [(instrument_id, date), ...]
      **금융상품 기반 × 날짜.** 대조군. 술어 컬럼: instrument_id · sector_name
      · industry_name · market_cap · listing_market · ticker. 거래 기록 있는 쌍만.
      exclude 에 처치 쌍을 주면 뺀다 - 대조에 처치가 섞이면 효과가 희석된다.

  ── 정렬된 열. pairs = [(instrument_id, date), ...] · **입력 순서 그대로 반환** ──
  ar(pairs)                  -> np.array  당일 초과수익(횡단면 평균 대비). 없으면 nan
  mom(pairs, days=20, lag=1) -> np.array  사건 전 누적 초과수익
  vol(pairs, days=20, lag=1) -> np.array  사건 전 수익률 표준편차
  weight(units=None)         -> dict      이 ETF 내 비중. 키: share · members · n_hold
  prior(event_type_code, need=None) -> dict
      타입 분포 사실. 키: n · up_ratio · abs_q50 · abs_q75 · abs_q90 · abs_max
      · effective_n (need 를 주면 n_at_least · freq_at_least)

  ── 산문 ──
  docs(query, domain=None, k=4) -> [{domain, ticker, ord, text}, ...]
      정기보고서 「사업의 내용」 원문 검색. **표에 없는 것을 여기서 찾는다** -
      공급사 구성·원재료·고객·계약 관행·경쟁 구도. 코호트 술어를 쓰기 전에 그 술어가
      가리키는 관계가 실제로 어떻게 생겼는지 읽어라. domain 은 'Technology/Semiconductors'
      처럼 섹터/산업이고, 생략하면 전 도메인에서 찾는다.
      **수치를 여기서 읽어 R 에 옮기지 마라** - 원장을 지나지 않은 수는 G4 가 죽인다.

  ── 검정 ──
  permute(x, strata=None, n=1000, seed=0) -> [{"x": 배열}, ...]
      처치 라벨 순열. **대조를 무엇 안에서 골랐으면 strata 도 그것이어야 한다.**
      날짜 안에서 골랐으면 strata=날짜배열. 아니면 귀무 분산이 층 효과로 부푼다
      (실측 자유순열 sd 0.0088 vs 날짜내 0.0077).
  placebo(stat, obs, nulls, null_kind=...) -> {obs, p, n_null, null_sd, ...}
      stat: (세계) -> 수 | None · obs: 관측 세계 · nulls: **주장이 거짓인 세계들**
      재표집을 어떻게 만들었는지가 곧 식별전략이다.
  fit(y, on) -> 계수 · predict(coef, on) -> 예측 · residualize(y, on) -> 창내 잔차
      **창내 잔차합은 구조적으로 0** 이다. 누적 초과수익을 residualize 로 재지 마라.

  이름으로 있는 것: np · dt · TRADE_DATE · W0 · W1 · ETF
  import 는 numpy·math·statistics·itertools·functools·collections·datetime·random 만.
  파일·네트워크·os 는 닫혀 있다. `__` 로 시작하는 속성은 쓸 수 없다."""

SYSTEM = """너는 인과 간선 **하나**의 효과를 추정한다. 파이썬을 직접 쓴다.

그래프는 못 본다. 구조는 이미 정해졌고 조정집합도 내려왔다. 너는 추정만 한다.

**너는 숫자를 쓰지 않는다.** 결론 JSON 에 수치를 적을 자리가 없다.
모든 값은 코드가 계산해서 `R` 에 담는다:

```
R = {{"x": 배열, "y": 배열, "z": {{"이름": 배열}}, "unit": "stock|portfolio|cell|day",
     "effect": 수, "test": placebo(...) 가 돌려준 것, "null_kind": "...",
     "strata": 층 배열 또는 None, "units": [처치 instrument_id ...]}}
```

강제 조건 - 어기면 결론이 거부되고 다시 시킨다:
  · `len(x) == len(y)` — 안 맞으면 검정이 아니다. 스칼라를 N개 관측에 회귀할 수 없다
  · `y` 의 분산이 0 이면 검정이 아니다
  · 내려온 조정집합은 **전부** `z` 에 담아라
  · `placebo` 를 반드시 불러라. `null_kind` 는 **허용집합 안에서** 골라라
  · `unit` 은 결과 노드의 선언 단위와 같아야 한다
  · **귀무는 설계가 조건화한 것을 보존해야 한다.** 대조를 날짜·산업 안에서 골랐으면
    `permute(x, strata=날짜배열)` 로 층 안에서 섞고 `strata` 를 R 에 담아라.
    무층화(`strata: None`)를 고르려면 `strata_reason` 에 **왜 층 없이도 교환가능한지**
    적어라 - 설계가 층을 조건화했는데 자유순열로 섞으면 귀무 분산이 층 효과로 부푼다
  · `units` 는 선택이다. 담으면 코드가 ETF 비중을 붙여 설명 폭을 계산한다

`scope: type` 이면 이 셀이 아니라 **사건 타입 전체**에서 표본을 쌓아라. 한 셀에 갇히지
마라 - `cohort(..., w0='YYYY-MM-DD')` 로 창을 넓히면 같은 설계가 몇 백 번 쌓인다.

기술된 대로 검정이 **불가능하면 억지로 만들지 마라.** 무엇이 없어서 안 되는지 적어라 -
그게 데이터 수집 의제가 된다. 그것도 유효한 산출이다.

{tools}

{brief}

JSON 하나만:
  코드 실행: {{"thought": "...", "code": "..."}}
  끝:       {{"thought": "...", "done": true}}
  불가:     {{"thought": "...", "impossible": "무엇이 없어서 안 되는가",
             "need": "필요한 데이터 한 줄", "grain": "일별|분봉|단면|문서",
             "unlocks": "그게 있으면 무엇이 열리나"}}"""


@dataclass(frozen=True, slots=True)
class EdgeProof:
    """간선 하나의 검정 결과. **수치는 전부 원장에서 온 것이다.**"""

    design: EdgeDesign
    status: str = "게이트실패"          # 통과 | 게이트실패 | 불가
    n: int = 0
    effect: float | None = None
    p: float | None = None
    null_sd: float | None = None
    null_kind: str | None = None
    unit: str = ""
    strata_declared: bool = False
    strata_reason: str = ""              # 무층화를 골랐다면 그 사유. 감사 대상이다
    adjust: list[str] = field(default_factory=list)
    strategy: str = "adjustment"
    iv: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)      # 비중 계산의 입력
    gate_fail: list[str] = field(default_factory=list)
    data_request: dict[str, Any] | None = None
    turns: int = 0
    ledger: list[dict] = field(default_factory=list)
    perms: list[dict] = field(default_factory=list)   # 순열 호출 원장. G7b 의 근거
    spec_sensitive: bool = False          # 원장의 p 가 α 를 가로지른다 = 사양 의존
    code: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.status == "통과" and self.p is not None

    @property
    def significant(self) -> bool:
        return self.passed and self.p is not None and self.p < 0.05


def plan(nodes: dict, edges: list, design: EdgeDesign, *,
         prior: dict | None = None) -> dict:
    """간선의 검정 브리프를 **코드가** 만든다. 조정집합·모집단·허용 귀무 전부 여기서.

    배제제약 열거(도구변수 후보)도 그래프에서 알고리즘적이다 - 에이전트가 발명할 일이
    아니다. 조정으로 식별이 안 되면 그 사실과 대안을 **알려주고** 검정을 맡긴다.
    """
    a, b = design.src, design.dst
    ident = identify(nodes, edges, a, b)
    zs = ident["adjust"]
    claims = design.claims if design.claims in NULL_OK else "L2"
    keep = [n for n in [a, b, *zs] if n in nodes]
    return {
        "from": a, "to": b, "scope": design.scope, "claims": claims,
        "say": design.say, "because": design.because, "false_if": design.false_if,
        "timing": design.timing,
        "nodes": {n: nodes[n] for n in keep},
        "adjust": zs,
        "adjust_alt": ident["alternatives"],
        "identified_by_adjustment": ident["strategy"] == "adjustment",
        "strategy": ident["strategy"],
        "iv": ident["iv"],
        "n_min": NMIN.get(design.scope, 8),
        "null_ok": sorted(NULL_OK[claims]),
        "treated_hint": design.treated,
        "control_hint": design.control,
        "strata_hint": design.strata,
        "needs": design.needs,
        # **관측 단위는 코드가 안다.** 도구가 만드는 표본은 (instrument, date) 쌍이므로
        # 종목 단위다 - 모델의 노드 선언을 기다리면(새 계약에는 `unit` 칸이 없다) G1 단위
        # 검사가 조용히 꺼지고, `R['unit']='portfolio'` 로 셀 단위를 주장해도 통과한다.
        "unit_expected": "stock",
        "prior": prior or {},
    }


def brief(p: dict) -> str:
    L = [f"간선  {p['from']}  →  {p['to']}", ""]
    for n, m in p["nodes"].items():
        L.append(f"  [{n}]  kind={m.get('kind', '?')}  unit={m.get('unit', '?')}  "
                 f"{m.get('measure', '')}")
    L += ["",
          f"주장     : {p['say']}",
          f"메커니즘 : {p['because']}",
          f"반증조건 : {p['false_if']}",
          f"시점     : timing={p['timing']}",
          f"범위     : scope={p['scope']}  최소표본 n≥{p['n_min']}",
          f"주장층위 : {p['claims']} ({CLAIM_SAY.get(p['claims'], '')}) "
          f"→ 허용 null_kind = {p['null_ok']}",
          f"조정집합 : {p['adjust'] if p['adjust'] else '없음 (뒷문이 열려 있지 않다)'}"]
    if not p["identified_by_adjustment"]:
        L.append("           **조정으로는 식별 불가** - X <-> Y 미지의 공통원인이 있다.")
        if p["iv"]:
            L.append(f"           도구변수 후보 (배제제약 성립): {p['iv']}")
            L.append("           2단계로 추정해라. 1단계 Z→X, 2단계 예측값→Y.")
        else:
            L.append("           도구변수 후보 없음. 축약형·부분식별로 내려가거나 "
                     "impossible 을 내라.")
    if p["adjust_alt"]:
        L.append(f"           (동등 대안: {p['adjust_alt']})")
    if p["treated_hint"] or p["control_hint"]:
        L += ["", "제안자가 적어 둔 비교 후보 (참고. 더 나은 대비가 있으면 바꿔라)",
              f"  처치 후보: {p['treated_hint'] or '-'}",
              f"  대조 후보: {p['control_hint'] or '-'}",
              f"  층화 후보: {p['strata_hint'] or '-'}"]
    if p["needs"]:
        L += ["", f"제안자 메모 - 부족한 데이터: {p['needs']}",
              "그래도 세울 수 있는 데까지 밀어라. 정말 안 되면 impossible 로 요청을 남겨라."]
    pr = p["prior"]
    if pr.get("n"):
        L += ["", f"타입 모집단 {pr.get('type', '')}",
              f"  사건 {pr.get('events', pr.get('n'))} · 종목 {pr.get('instruments', 0)}"
              f" · 유효n≈{pr.get('effective_n', 0)} · {pr.get('first')}~{pr.get('last')}",
              f"  분포: 상승 {pr.get('up_ratio', 0) * 100:.0f}%"
              f" · |초과수익| 중위 {pr.get('abs_q50', 0) * 100:.1f}%"
              f" p90 {pr.get('abs_q90', 0) * 100:.1f}% 최대 {pr.get('abs_max', 0) * 100:.1f}%"]
    return "\n".join(L)


def _arr(v) -> np.ndarray | None:
    try:
        a = np.asarray(v, dtype=float).ravel()
        return a if a.size else None
    except Exception:  # noqa: BLE001 - 배열이 아닌 것은 게이트가 말한다
        return None


def gate(R: dict, led: SB.Ledger, p: dict) -> list[str]:
    """G1-G7. **전부 기계 검사다. 산문 판정은 하나도 없다.**"""
    bad: list[str] = []
    x, y = _arr(R.get("x")), _arr(R.get("y"))

    # G1 단위 일치 - 스칼라를 N관측에 회귀하는 일을 문법적으로 불가능하게 한다
    if x is None or y is None:
        bad.append("G1 x 또는 y 가 배열이 아니다. 검정이 아니다.")
    elif len(x) != len(y):
        bad.append(f"G1 len(x)={len(x)} != len(y)={len(y)}. 단위가 안 맞는다.")
    elif float(np.var(y)) == 0.0:
        bad.append("G1 y 의 분산이 0 이다. 잴 것이 없다.")

    n = len(y) if y is not None else 0
    # G2 검정력
    if n < p["n_min"]:
        bad.append(f"G2 n={n} < {p['n_min']} (scope={p['scope']}). "
                   + ("scope=type 이면 cohort 의 창을 넓혀 사건 타입 전체에서 쌓아라."
                      if p["scope"] == "type" else "표본이 원리적으로 작다."))
    # G3 귀무 존재
    if not led.calls:
        bad.append("G3 placebo 를 한 번도 안 불렀다. 귀무 없이는 검정이 아니다.")
    # G4 값이 원장 추적 가능 - 모델이 타이핑한 수치는 받지 않는다.
    #
    # **(p, null_kind) 쌍으로 맞춘다.** p 만 대조하면 구멍이 있다: 원장에 date 로 돌린
    # 호출이 있고 R 에 label 이라고 적으면 통과한다. p 는 같고 종류만 갈아 끼운 것이니
    # G6 가 뒤에서 봐도 소용없다 - G6 가 읽는 값이 R 의 자기 신고였기 때문이다.
    # 여기서 매칭된 원장 항목을 찾아 두고, G6·G7 이 그 항목을 읽는다.
    t = R.get("test")
    hit: dict | None = None
    if not isinstance(t, dict) or "p" not in t:
        bad.append("G4 test 가 placebo 결과가 아니다. R['test'] 에 placebo(...) 반환을 담아라.")
    else:
        same_p = [c for c in led.calls
                  if c.get("p") is not None and abs(c["p"] - t["p"]) < 1e-12]
        if not same_p:
            bad.append("G4 R['test'].p 가 원장에 없다. 손으로 쓴 값은 받지 않는다.")
        else:
            declared = R.get("null_kind") or t.get("null_kind")
            # **가장 최근 일치를 고른다.** 여러 턴이 같은 p 를 낼 수 있다(같은 표본·같은
            # 시드). 첫 일치를 고르면 이미 되먹임으로 폐기된 옛 턴의 호출에 결속되고,
            # G7b 가 그 턴의 순열을 보고 지금 R 을 기각한다 - 지금 보고된 p 는 마지막
            # 턴의 것이다.
            hit = next((c for c in reversed(same_p)
                        if c.get("null_kind") == declared), None)
            if hit is None:
                bad.append(
                    f"G4 p={t['p']:.6g} 는 원장에 있지만 그 호출의 null_kind 는 "
                    f"{sorted({str(c.get('null_kind')) for c in same_p})} 이고 선언은 "
                    f"{declared!r} 다. 종류를 갈아 끼울 수 없다 - 어느 귀무로 얻은 p 인지가 "
                    "주장의 자격을 정한다.")
                hit = same_p[-1]
    # G5 조정 실제 적용
    miss = [z for z in p["adjust"] if z not in (R.get("z") or {})]
    if miss:
        bad.append(f"G5 조정집합을 안 썼다: {miss}")
    else:
        for k, v in (R.get("z") or {}).items():
            zv = _arr(v)
            if zv is None or (y is not None and len(zv) != len(y)):
                bad.append(f"G5 z['{k}'] 길이가 y 와 다르다.")
                break
    # G6 귀무 종류가 주장과 정합 - 선택 순환 차단.
    # **원장에서 읽는다.** R 의 자기 신고를 읽으면 G4 가 잡은 불일치를 다시 놓친다.
    nk = (hit or {}).get("null_kind") if hit else (
        R.get("null_kind") or (led.calls[-1].get("null_kind") if led.calls else None))
    if nk not in p["null_ok"]:
        bad.append(f"G6 null_kind={nk!r} 는 {p['claims']} 주장에 못 쓴다. 허용: {p['null_ok']}."
                   + (" date 는 셀이 결과로 선택돼서 자동 유의해진다(선택 순환)."
                      if nk == "date" else ""))
    # G6b 방향 주장에는 단측 검정이 필요하다. **원장에 two_sided 가 남으므로 검사된다.**
    # 양측으로 재고 "올랐다"고 쓰면 그 p 는 "달랐다"의 p 다 - 부호는 검정되지 않았다.
    if hit and p["claims"] in DIRECTIONAL and hit.get("two_sided") is True:
        bad.append(f"G6b {p['claims']} 는 방향을 주장한다. 양측 검정의 p 는 "
                   "'달랐다'의 p 이고 부호를 세우지 못한다 - placebo(..., "
                   "two_sided=False) 로 다시 재라.")
    # G7 귀무의 교환가능성이 설계의 블로킹과 맞나
    if "strata" not in R:
        bad.append("G7 strata 를 선언하지 않았다. 층 안에서 섞었나(배열) 아닌가(None) "
                   "명시해라. 대조를 날짜·산업 안에서 골랐으면 층도 그것이어야 한다.")
    else:
        st = R.get("strata")
        if st is None:
            # **선언만으로는 부족하다.** 설계가 층을 조건화했다고 적어 놓고 자유순열로
            # 섞으면 귀무 분산이 층 효과로 부푼다(실측 sd 0.0088 vs 0.0077). 무층화를
            # 고르는 것은 되지만 이유가 남아야 한다 - 그 문장이 감사 대상이 된다.
            if p.get("strata_hint") in ("date", "date_industry") and not str(
                    R.get("strata_reason") or "").strip():
                bad.append(f"G7 설계는 strata={p['strata_hint']!r} 로 조건화했는데 "
                           "무층화(None)를 선언했다. 층 안에서 섞거나, 층 없이도 "
                           "교환가능한 이유를 R['strata_reason'] 에 적어라.")
        else:
            sv = np.asarray(st).ravel()
            if y is not None and len(sv) != len(y):
                bad.append(f"G7 strata 길이 {len(sv)} != y 길이 {len(y)}.")
            elif len(set(sv.tolist())) < 2:
                bad.append("G7 strata 가 단일 층이다 - 층화가 아니다.")

    # G7b 선언과 실행의 대조. **원장이 없으면 탐지 불가였던 자리다.**
    #
    # `R['strata']` 에 층 배열을 담아 놓고 `permute(x)` 를 층 없이 부르면, 선언은 층화고
    # 실행은 자유순열이다. 귀무 분산이 층 효과로 부풀어 p 가 보수적으로 나오거나(운이
    # 좋으면) 반대로 무의미해지는데, 어느 쪽이든 보고된 층화는 사실이 아니다.
    # G7 은 선언만 봤으므로 이 불일치를 통과시켰다.
    #
    # **G4 가 맞춘 호출의 순열을 본다 - 마지막 순열이 아니다.** 마지막만 보면 무층화로
    # 재서 p 를 얻고 뒤에 층화 permute 를 한 번 더 부르는 것으로 통과한다(더미 순열).
    # 그때 보고된 p 는 틀린 교환가능성에서 온 것인데 감사에는 층화로 남는다.
    used = _perm_of(hit, led)
    if used is not None:
        declared_strat = R.get("strata") is not None
        where = f"(원장 {len(led.perms)}회 중 #{used['n']})"
        if declared_strat and not used["stratified"]:
            bad.append("G7b R['strata'] 에 층을 담았는데 보고된 p 를 만든 permute 는 층 없이 "
                       f"불렸다 {where}. 선언과 실행이 다르다 - permute(x, strata=...) 로 "
                       "실제로 층 안에서 섞어라.")
        elif not declared_strat and used["stratified"]:
            bad.append(f"G7b 보고된 p 를 만든 permute 는 층 {used['n_strata']}개 안에서 "
                       f"섞었는데 R['strata'] 는 None 이다 {where}. 실제로 쓴 층을 R 에 "
                       "담아라 - 감사가 재구성돼야 한다.")
        elif y is not None and used["len_x"] not in (-1, len(y)):
            bad.append(f"G7b permute 에 넣은 x 길이 {used['len_x']} != y 길이 {len(y)} "
                       f"{where}. 귀무를 만든 표본과 검정한 표본이 다르다.")
    elif R.get("strata") is not None and led.calls:
        # 층화를 선언했는데 보고된 p 앞에 순열이 없다 - 그 층화는 실행되지 않았다.
        bad.append("G7b R['strata'] 에 층을 담았는데 보고된 p 를 만든 순열이 원장에 없다. "
                   "층화 귀무를 실제로 만들어라 - permute(x, strata=...).")

    # 단위 일치 - 노드 선언이 있으면 그것을, 없으면 **코드가 아는 단위**를 쓴다.
    du = (p["nodes"].get(p["to"]) or {}).get("unit") or p.get("unit_expected")
    if du and R.get("unit") and R["unit"] != du:
        bad.append(f"G1 unit={R['unit']!r} 인데 표본은 {du!r} 단위다 - 도구는 "
                   "(instrument, date) 쌍을 준다. 다른 단위를 주장하려면 그 표본을 "
                   "직접 만들고 무엇을 셌는지 R 에 적어라.")
    return bad


def _perm_of(hit: dict | None, led: SB.Ledger) -> dict | None:
    """G4 가 맞춘 placebo 호출의 **귀무를 만든 순열**. 없으면 None.

    원장은 `perms_at` 에 그 호출 직전까지의 순열 수를 남긴다 - 마지막 순열을 보는 것과
    다르다. 코드 턴이 무층화로 재서 p 를 얻은 뒤 층화 permute 를 한 번 더 부르면 마지막
    순열은 층화지만 보고된 p 는 무층화에서 왔다.

    `perms_at` 이 없는 원장(맞춘 호출이 없을 때)에는 마지막 순열로 떨어진다 - 결속할
    근거가 없을 때 검사를 아예 건너뛰면 옛 구멍이 되살아난다.
    """
    if not led.perms:
        return None
    at = (hit or {}).get("perms_at")
    if at is None:
        return led.perms[-1]
    return led.perms[at - 1] if at >= 1 else None



def _user(p: dict, trace: list, turn: int, force: bool = False) -> str:
    L: list[str] = []
    for c, o in trace:
        L += [f">>> {c[:900]}", o[:1600], ""]
    L.append(f"[{turn}/{MAX_TURNS}턴]")
    L.append("마지막이다. R 을 완성하고 done 을 내거나, 불가하면 impossible 로 "
             "필요한 데이터를 적어라." if force
             else "코드를 더 쓰거나(code), R 이 완성됐으면 done, 불가능하면 impossible.")
    return "\n".join(L) if L else "시작해라."


def verify(cd, client, design: EdgeDesign, p: dict, *, as_of: str, w0: date, w1: date,
           trade_date: date, etf_instrument_id: str = "", docs=None) -> EdgeProof:
    """간선 하나를 검정한다. 반환 수치는 **전부 `R` 과 원장에서 온 것이다.**"""
    tool_map, led = SB.tools(cd, as_of=as_of, w0=w0, w1=w1, trade_date=trade_date,
                            etf_instrument_id=etf_instrument_id, docs=docs)
    ns = SB.namespace(tool_map)
    system = SYSTEM.format(tools=TOOLS, brief=brief(p))
    trace: list[tuple[str, str]] = []
    # R 과 원장을 **같은 턴에 묶는다.** 상태가 턴 사이 유지되므로 앞 턴의 R 이 남아
    # 마지막 턴이 실패해도 게이트를 통과할 수 있다 - 실험판에서 실측으로 걸렸다.
    snap: dict = {}

    for turn in range(1, MAX_TURNS + 1):
        out = client.complete_json(system, _user(p, trace, turn, force=(turn == MAX_TURNS)))
        if out.get("impossible"):
            log("causal.verify_impossible", edge=f"{design.src}->{design.dst}", turn=turn)
            return _pack(design, {}, led, ["검정 불가"], turn, p,
                         status="불가",
                         request={"need": out.get("need") or out["impossible"],
                                  "grain": out.get("grain") or "미분류",
                                  "unlocks": out.get("unlocks") or "",
                                  "why": str(out["impossible"])[:400],
                                  "edge": f"{design.src}→{design.dst}"})
        if out.get("done"):
            bad = gate(snap, led, p)
            if bad and turn < MAX_TURNS:
                trace.append(("(완료 시도)", "**거부** - 게이트 실패:\n  " + "\n  ".join(bad)))
                continue
            return _pack(design, snap, led, bad, turn, p)
        code = (out.get("code") or "").strip()
        if not code:
            trace.append(("", "오류: code·done·impossible 중 하나를 내라."))
            continue
        led.codes.append(code)
        ns.pop("R", None)                     # 이 턴이 새로 만들어야 한다
        trace.append((code, SB.observe(code, ns)))
        if isinstance(ns.get("R"), dict):
            snap = dict(ns["R"])              # 이 턴의 R = 이 턴까지의 원장

    return _pack(design, snap, led, gate(snap, led, p), MAX_TURNS, p)


def _pack(design: EdgeDesign, R: dict, led: SB.Ledger, bad: list, turn: int, p: dict,
          *, status: str | None = None, request: dict | None = None) -> EdgeProof:
    y = _arr(R.get("y"))
    t = R.get("test") if isinstance(R.get("test"), dict) else {}
    units = [str(u) for u in (R.get("units") or [])]
    return EdgeProof(
        design=design,
        status=status or ("통과" if not bad else "게이트실패"),
        n=int(len(y)) if y is not None else 0,
        effect=_num(R.get("effect")),
        p=_num(t.get("p")) if not bad else None,     # 게이트 실패면 수치를 쓰지 않는다
        null_sd=_num(t.get("null_sd")),
        null_kind=R.get("null_kind") or t.get("null_kind"),
        unit=str(R.get("unit") or ""),
        strata_declared=("strata" in R and R.get("strata") is not None),
        strata_reason=str(R.get("strata_reason") or ""),
        adjust=list(p["adjust"]),
        strategy=p["strategy"],
        iv=list(p["iv"]),
        units=units,
        gate_fail=list(bad),
        data_request=request,
        turns=turn,
        ledger=list(led.calls),
        perms=list(led.perms),
        spec_sensitive=led.spec_sensitive(),
        code=list(led.codes),
    )


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def report(proofs: list[EdgeProof]) -> str:
    """사람이 읽는 한 장. 실패를 접어두지 않는다."""
    L = [f"{'간선':<44} {'상태':<10} {'n':>5} {'효과':>9} {'p':>7} {'귀무':<7} 턴",
         "-" * 96]
    for r in proofs:
        e = f"{r.design.src} → {r.design.dst}"
        eff = f"{r.effect:+.4f}" if isinstance(r.effect, float) else "-"
        pv = f"{r.p:.3f}" if isinstance(r.p, float) else "-"
        L.append(f"{e[:44]:<44} {r.status:<10} {r.n:>5} {eff:>9} {pv:>7} "
                 f"{str(r.null_kind or '-'):<7} {r.turns}")
        for b in r.gate_fail[:3]:
            L.append(f"      {b}")
        if r.data_request:
            L.append(f"      요청: {str(r.data_request.get('need'))[:100]}")
    return "\n".join(L)
