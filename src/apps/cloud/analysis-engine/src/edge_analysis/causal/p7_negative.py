"""P7 · 음성대조와 표본 오염 — **효과를 확인하는 장치가 아니라 설계 오류를 찾는 장치다.**

음성대조는 처치가 있어야 할 자리를 보지 않는다. 영향이 **없어야 하는** 자리를 본다
(Lipsitch 2010). 거기서 신호가 나오면 그 신호는 처치로 설명될 수 없고, 남는 설명은
하나다 - 설계가 사건이 아닌 무언가를 잡고 있다. 공통 원인이든 선택이든 창 설정이든,
어느 쪽이든 **본 검정의 수치를 그만큼 못 믿는다.** 그래서 이 단계의 산출은 효과에 대한
증거가 아니라 증거의 신뢰도에 대한 진술이다.

비대칭이 핵심이다. 조용한 대조는 아무것도 증명하지 않는다 - 검정력이 없어서 조용할 수도
있다. 시끄러운 대조만 정보다. 그래서 `passed=True` 는 P8 의 주장 상한을 올리지 못하고
`passed=False` 만 내린다.

**사전 추세가 여기서 가장 싼 반증이다.** 사건이 원인이면 사건 이전은 조용해야 한다.
이전이 이미 움직였다면 조기 반영이거나 선택 편의인데 관측만으로는 둘을 못 가른다 -
어느 쪽이든 "이 사건이 그 움직임을 만들었다"는 성립하지 않는다. 창은 조정변수 창
(t-20..t-1)과 겹치지 않게 뒤로 물린다. 겹치면 조정으로 이미 뺀 것을 다시 재는 꼴이라
검정이 자기 자신을 통과시킨다.

**오염 검사는 검정이 아니라 표본 정의다**(Kothari-Warner 사건연구 표준). 사건창 안에 다른
공시가 있는 기업은 그 창의 초과수익이 무엇의 것인지 원리적으로 알 수 없다. 지금까지 이
절차는 제안의 `false_if` 문자열("같은 날 지수 편입 변경이 있었다면 죽는다")로만 남고
**아무도 조회하지 않았다** - 죽을 조건을 적어 놓고 확인하지 않으면 그 문장은 장식이다.

실행 불가한 대조를 목록에서 빼지 않는 이유: 빼면 "돌렸는데 조용했다"와 "못 돌렸다"가 같은
표현(부재)이 된다. P3 이 그래프에서 닫은 실패 모드와 같은 것이다 - 두 상태를 구분할 수
없으면 침묵이 통과로 읽힌다.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import numpy as np

from ..adapters.sql_surface import MAX_ROWS
from ..observability import log
from . import stats as S
from .contracts import (
    ConfoundingScreen,
    DiscriminationPlan,
    NegativeControl,
    Question,
    WorldGraph,
)

# 처치 코호트를 긁는 창. `run.explain(window_days=60)` 과 같다 - 음성대조는 **그 설계에
# 대한** 검사이므로 본 검정과 다른 창을 쓰면 다른 설계를 검사하게 된다.
WINDOW_DAYS = 60
N_NULL = 1000
# 표본 바닥. 셀 설계는 원리적으로 작다 - 막지 않고 요구만 낮춘다(engine 의 cell 기준과 같다).
NMIN = 8
ALPHA = 0.05
# 사전 추세 창: 거래일 [t-40, t-21]. lag 를 조정변수 창(t-20..t-1) 밖으로 물린다.
PRE_DAYS, PRE_LAG = 20, 21
# 발행 주체 역할. 처치의 역할을 원장에서 못 읽었을 때의 기본값이다.
ISSUER = "ISSUER"

_TYPE = re.compile(r"event_type_code\s*=\s*'([^']+)'", re.I)
_STAGE = re.compile(r"lifecycle_stage\s*=\s*'([^']+)'", re.I)
_ROLE = re.compile(r"role_code\s*=\s*'([^']+)'", re.I)


# ── 술어 조립 ───────────────────────────────────────────────────────────
def _lit(v: object) -> str:
    """SQL 문자열 리터럴 본문. 작은따옴표를 두 배로 만든다."""
    return str(v).replace("'", "''")


def _in(col: str, vals, *, negate: bool = False) -> str:
    body = ", ".join(f"'{_lit(v)}'" for v in vals)
    return f"{col} {'NOT ' if negate else ''}IN ({body})"


def _day(v: object) -> date:
    """무엇이 오든 달력일 하나로. 원장은 date 를, 계약은 문자열을 흘릴 수 있다."""
    return date.fromisoformat(str(v)[:10])


def _keys(pairs) -> set[tuple[str, str]]:
    return {(str(i), str(d)[:10]) for i, d in pairs}


# ── 대비 한 판 ──────────────────────────────────────────────────────────
def _diff(x: np.ndarray, y: np.ndarray) -> float | None:
    """처치-대조 평균차. x 가 0/1 이면 OLS 계수와 같은 값이라 회귀를 돌릴 이유가 없다.

    퇴화한 순열(한쪽이 비는 층)에서 None 을 돌린다 - `placebo` 가 그 세계를 버린다.
    """
    t, c = y[x > 0], y[x <= 0]
    return float(t.mean() - c.mean()) if len(t) and len(c) else None


def _contrast(cd, treated, reference: str, *, outcome, name: str, seed: int = 0) -> dict:
    """처치 대 대조 한 판. **p 는 `permute`+`placebo` 만 만든다.**

    손으로 t 값을 세우지 않는 이유: 사건 표본은 날짜로 군집돼 있어 독립 가정이 깨지고,
    정규 근사의 p 는 그 군집만큼 낙관적으로 나온다. 순열 귀무는 설계가 조건화한 것
    (여기서는 날짜)을 보존하므로 그 편향이 들어오지 않는다.

    조회 실패를 예외로 올리지 않는다 - 음성대조 하나가 못 돌았다고 설명 전체를 멈추면
    나머지 대조의 정보까지 잃는다. 실행 불가로 남기고 P8 이 그대로 적는다.
    """
    try:
        dates = sorted({_day(d) for _, d in treated})
        control = cd.universe(reference, dates, exclude=treated)
        if not control:
            return {"n": 0, "why": f"대조 코호트가 비었다 (술어 `{reference}`)"}
        pairs = list(treated) + list(control)
        x = np.array([1.0] * len(treated) + [0.0] * len(control))
        y = outcome(pairs)
    except Exception as exc:  # noqa: BLE001 - 조회 실패도 산출물이다
        log("causal.p7.failed", control=name, error=f"{type(exc).__name__}: {exc}")
        return {"n": 0, "why": f"조회 실패: {type(exc).__name__}: {exc}"}

    ok = np.isfinite(y)
    pairs = [p for p, keep in zip(pairs, ok) if keep]
    x, y = x[ok], y[ok]
    n, n_t = len(y), int(x.sum())
    if n < NMIN or n_t < 2 or n_t == n:
        return {"n": n, "why": f"표본 부족 - 전체 {n} / 처치 {n_t} (바닥 {NMIN})"}

    obs = _diff(x, y)
    # 대조를 처치의 날짜 안에서 골랐으므로 귀무도 날짜 안에서 섞는다. 자유순열로 섞으면
    # 귀무 분산이 날짜 효과로 부풀어 조용하지 않은 대조가 조용해 보인다.
    strata = np.array([str(d)[:10] for _, d in pairs])
    nulls = S.permute(x, strata=strata, n=N_NULL, seed=seed)
    test = S.placebo(lambda w: _diff(w["x"], y), {"x": x}, nulls, null_kind="label")
    if not test.get("testable"):
        return {"n": n, "effect": obs, "why": f"귀무 불가: {test.get('reason', '?')}"}
    return {"n": n, "n_treated": n_t, "effect": obs, "p": float(test["p"]),
            "null_sd": test.get("null_sd")}


def _verdict(kind: str, name: str, r: dict, what: str) -> NegativeControl:
    """대비 결과를 판정으로. **실행 불가도 판정이다 - 목록에서 빠지지 않는다.**"""
    if "p" not in r:
        return NegativeControl(kind=kind, name=name, n=int(r.get("n", 0)),
                               effect=r.get("effect"), p=None, passed=False,
                               says=f"실행 불가: {r.get('why', '?')}")
    quiet = r["p"] >= ALPHA
    tail = ("조용하다. 이 자리에서 설계 오류의 증거는 나오지 않았다 - 검정력 한계가 있으므로 "
            "주장 상한을 올리지는 못한다"
            if quiet else
            "시끄럽다. **효과가 아니라 공통 교란·선택의 증거다** - 본 검정 수치를 그만큼 깎는다")
    return NegativeControl(
        kind=kind, name=name, n=int(r["n"]), effect=r["effect"], p=r["p"], passed=quiet,
        says=f"{what}: 효과 {r['effect']:+.3%} p={r['p']:.3f} n={r['n']} - {tail}")


# ── 그래프에서 술어 캐기 ────────────────────────────────────────────────
# v_cohort 의 컬럼. 술어는 이 위에서만 성립한다.
_COHORT_COLS = ("instrument_id", "trade_date", "source_event_id", "event_type_code",
                "predicate_code", "role_code", "lifecycle_stage", "sector_name",
                "industry_name", "market_cap", "listing_market", "ticker")
_PREDICATE = re.compile(
    r"\b(?:" + "|".join(_COHORT_COLS) + r")\b\s*(?:=|<>|!=|<|>|~|IS\b|IN\b|NOT\b|LIKE\b|BETWEEN\b)",
    re.IGNORECASE)


def _sql_predicate(raw: object) -> str:
    """SQL 술어처럼 생긴 것만 통과시킨다. **아니면 빈 문자열이다.**

    P3 는 `exposure` 를 "이 경로에 노출된 집합" 으로 받는데, 모델은 그 자리에 산문을 쓴다 -
    2026-07-30 실측에서 `WHERE (삼성전자 실적 발표를 접한 투자자)` 가 그대로 실행돼
    `SyntaxError` 로 음성대조 전량이 죽었다. 문법이 우연히 맞는 산문이면 더 나쁘다:
    조용히 엉뚱한 코호트가 잡히고 "검사했다"고 기록된다. 그래서 **컬럼 이름 위의 비교식**
    이라는 최소 형태를 요구하고, 아니면 버려서 접지 사건 폴백으로 내려보낸다.
    """
    s = str(raw or "").strip()
    return s if s and _PREDICATE.search(s) else ""


def _predicates(graph: WorldGraph) -> tuple[str, str]:
    """P3 간선의 (exposure, reference). 처치 노드에서 나가는 간선을 먼저 본다.

    노드에는 술어가 없다(P3 규약: nodes 는 says/observed/events). exposure 가 아예 없거나
    술어가 아니면 가설이 접지한 `source_event_id` 로 만든다 - 그게 후보 사건 자체이므로
    가장 좁고 정확한 처치 정의다. 대신 그 술어로는 타입·역할을 알 수 없어 노출 대조는
    조회가 한 번 더 든다.
    """
    treatments = {h.treatment for h in graph.hypotheses}
    edges = sorted(graph.edges, key=lambda e: 0 if e.get("from") in treatments else 1)
    exposure = next((s for e in edges if (s := _sql_predicate(e.get("exposure")))), "")
    reference = next((s for e in edges if (s := _sql_predicate(e.get("reference")))), "")
    if not exposure:
        events = list(dict.fromkeys(ev for h in graph.hypotheses for ev in h.events))
        if events:
            exposure = _in("source_event_id", events)
    return exposure, reference


def _reference(cd, treated, as_of_date) -> str:
    """대조 술어가 그래프에 없을 때 산업 동종군으로 만든다. **비교군 없이는 대조가 없다.**

    산업을 고르는 이유: 같은 날 같은 산업이 이 파이프라인의 기본 비교 축이고
    (`industry_map` 은 층화용으로 이미 있다), 시장 전체를 대조로 두면 산업 충격이 처치
    효과로 새어 들어온다.
    """
    try:
        imap = cd.industry_map(as_of_date)
    except Exception as exc:  # noqa: BLE001
        log("causal.p7.failed", control="reference", error=f"{type(exc).__name__}: {exc}")
        return ""
    names = sorted({v for i, _ in treated if (v := imap.get(str(i)))})
    return _in("industry_name", names) if names else ""


def _facts(sql, texts: list[str], events: list[str]) -> dict[str, Any]:
    """처치의 타입·lifecycle·역할. 술어 문자열에서 읽고, 없으면 원장에 한 번 묻는다.

    노출 대조는 "같은 타입의 다른 무엇"이라 타입을 모르면 만들 수 없다. 술어가
    `source_event_id IN (...)` 형태로만 오는 경우가 있으므로 v_cohort 조회를 붙인다 -
    `sql` 이 없으면 그 대조는 실행 불가로 남는다(추측으로 타입을 지어내지 않는다).
    """
    out: dict[str, Any] = {"type": "", "stages": [], "roles": [], "why": ""}
    for t in texts:
        if not out["type"] and (m := _TYPE.search(t)):
            out["type"] = m.group(1)
        if (m := _STAGE.search(t)) and m.group(1) not in out["stages"]:
            out["stages"].append(m.group(1))
        if (m := _ROLE.search(t)) and m.group(1) not in out["roles"]:
            out["roles"].append(m.group(1))
    if out["type"] and out["stages"]:
        return out
    if not events:
        if not out["type"]:
            out["why"] = "처치 술어에 event_type_code 가 없고 접지된 source_event_id 도 없다"
        return out
    if sql is None:
        if not out["type"]:
            out["why"] = "처치 술어에 event_type_code 가 없고 SQL 표면이 없어 원장에 못 묻는다"
        return out
    q = ("SELECT DISTINCT event_type_code, lifecycle_stage, role_code FROM v_cohort"
         f" WHERE {_in('source_event_id', events)}")
    try:
        rows = sql.query(q)
    except Exception as exc:  # noqa: BLE001
        log("causal.p7.failed", control="facts", error=f"{type(exc).__name__}: {exc}")
        if not out["type"]:
            out["why"] = f"처치 사건 조회 실패: {type(exc).__name__}: {exc}"
        return out
    if not rows and not out["type"]:
        out["why"] = "접지된 source_event_id 가 원장(PIT 클램프 뒤)에서 0행이다"
    for r in rows:
        if not out["type"] and r.get("event_type_code"):
            out["type"] = str(r["event_type_code"])
        for key, bag in (("lifecycle_stage", "stages"), ("role_code", "roles")):
            v = r.get(key)
            if v and str(v) not in out[bag]:
                out[bag].append(str(v))
    return out


# ── 1. 표본 오염 (Kothari-Warner) ───────────────────────────────────────
def _name_conflicts(sql, ids: list[str], lo: date, hi: date,
                    exclude_event_type: str) -> tuple[dict, str]:
    """충돌 사건에 이름을 붙인다. `cd.cohort` 는 (instrument_id, trade_date) 만 돌려준다.

    판정과 명명을 나눈 이유: 판정은 언제나 `cd.cohort` 가 한다. 명명 표면(`sql`)이
    주입됐는지에 따라 **버려지는 표본 수가 달라지면** 같은 셀이 배선에 따라 다른 표본을
    쓰게 된다 - 그건 스크린이 아니라 잡음이다.
    """
    if sql is None:
        return {}, "SQL 표면 미주입 - 충돌 사건의 타입을 특정하지 못함"
    q = ("SELECT instrument_id, trade_date, event_type_code, source_event_id FROM v_cohort"
         f" WHERE {_in('instrument_id', ids)}"
         f" AND trade_date BETWEEN '{lo.isoformat()}' AND '{hi.isoformat()}'"
         f" AND event_type_code <> '{_lit(exclude_event_type)}'"
         " ORDER BY instrument_id, trade_date")
    try:
        rows = sql.query(q)
    except Exception as exc:  # noqa: BLE001
        log("causal.p7.failed", step="name_conflicts", error=f"{type(exc).__name__}: {exc}")
        return {}, f"충돌 사건 명명 실패 - 타입 미상: {type(exc).__name__}: {exc}"
    named = {(str(r["instrument_id"]), str(r["trade_date"])[:10]): r for r in rows}
    if len(rows) >= MAX_ROWS:
        # 상한에 닿으면 뒤쪽 충돌은 이름이 없다. 판정은 cohort 가 했으므로 표본은 정확하다.
        return named, f"충돌 사건 조회가 {MAX_ROWS}행 상한에 닿았다 - 일부 타입은 미상"
    return named, ""


def screen_confounding(cd, *, treated: list[tuple[str, object]], as_of: str,
                       exclude_event_type: str, window_days: int = 3,
                       sql=None) -> ConfoundingScreen:
    """사건창 안에 다른 공시가 있는 기업을 표본에서 뺀다. **검정이 아니라 표본 정의다.**

    창 안에 두 사건이 있으면 그 창의 초과수익은 둘의 합이고, 어느 쪽 몫인지는 관측으로
    가를 수 없다. 남겨 두면 효과가 부풀거나(같은 방향) 사라지고(반대 방향), 어느 쪽이든
    그 수치는 처치의 것이 아니다.

    `window_days` 는 **달력일**이다 - `cd.cohort` 의 창이 달력 기준이라서다. ±3 달력일이
    거래일 ±2~3 을 덮으므로 표준 절차(±1~5 거래일)와 어긋나지 않는다.

    타 공시는 `event_type_code <> exclude_event_type` 으로 정의한다. 같은 타입의 다른 건은
    오염이 아니라 처치의 반복이므로 남긴다.

    조회가 실패하면 `checked=False` 로 돌려준다 - **오염 없음과 검사 못 함은 다른 상태다.**
    스크린을 건너뛰고 조용히 전량을 남기면 P8 이 그 둘을 구분할 수 없다.
    """
    n_before = len(treated)
    if not treated:
        return ConfoundingScreen(n_before=0, n_dropped=0, checked=False,
                                 note="처치 표본이 비어 있다 - 오염을 검사할 대상이 없다")
    if not (exclude_event_type or "").strip():
        return ConfoundingScreen(n_before=n_before, n_dropped=0, checked=False,
                                 note="처치 event_type_code 가 없다 - 무엇이 '타 공시'인지 "
                                      "정의할 수 없다")
    ids = sorted({str(i) for i, _ in treated})
    days = [_day(d) for _, d in treated]
    span = timedelta(days=int(window_days))
    lo, hi = min(days) - span, max(days) + span
    where = f"{_in('instrument_id', ids)} AND event_type_code <> '{_lit(exclude_event_type)}'"
    try:
        hits = cd.cohort(where, as_of=as_of, w0=lo, w1=hi)
    except Exception as exc:  # noqa: BLE001 - 실패를 침묵으로 바꾸지 않는다
        log("causal.p7.failed", step="screen", error=f"{type(exc).__name__}: {exc}")
        return ConfoundingScreen(n_before=n_before, n_dropped=0, checked=False,
                                 note=f"코호트 조회 실패 - 오염 여부 미상: "
                                      f"{type(exc).__name__}: {exc}")

    by_id: dict[str, list[date]] = {}
    for i, d in hits:
        by_id.setdefault(str(i), []).append(_day(d))
    named, note = _name_conflicts(sql, ids, lo, hi, exclude_event_type)

    dropped: list[dict[str, Any]] = []
    for (inst, raw), d0 in zip(treated, days):
        near = sorted((abs((d - d0).days), d) for d in by_id.get(str(inst), ())
                      if abs((d - d0).days) <= window_days)
        if not near:
            continue
        row = named.get((str(inst), near[0][1].isoformat())) or {}
        dropped.append({
            "instrument_id": str(inst),
            "trade_date": _day(raw).isoformat(),
            "conflicting_event_type": str(row.get("event_type_code") or "미상"),
            "source_event_id": str(row.get("source_event_id") or "미상"),
            "conflict_date": near[0][1].isoformat(),
            "n_conflicts": len(near),
        })
    log("causal.p7.screened", n_before=n_before, n_dropped=len(dropped),
        window_days=int(window_days), named=bool(named))
    return ConfoundingScreen(n_before=n_before, n_dropped=len(dropped), dropped=dropped,
                             checked=True, note=note)


# ── 2. 음성대조 (Lipsitch) ──────────────────────────────────────────────
def _all_blocked(why: str) -> list[NegativeControl]:
    """세 대조가 같은 이유로 못 돈다. **빼지 않고 이유를 붙여 남긴다.**"""
    return [
        NegativeControl(kind="outcome", name=f"사전추세 [t-{PRE_DAYS + PRE_LAG}, t-{PRE_LAG}]",
                        n=0, effect=None, p=None, passed=False, says=f"실행 불가: {why}"),
        NegativeControl(kind="exposure", name="다른 lifecycle_stage", n=0, effect=None,
                        p=None, passed=False, says=f"실행 불가: {why}"),
        NegativeControl(kind="exposure", name=f"비{ISSUER} 역할", n=0, effect=None,
                        p=None, passed=False, says=f"실행 불가: {why}"),
    ]


def negative_controls(cd, sql, *, question: Question, graph: WorldGraph,
                      plan: DiscriminationPlan) -> list[NegativeControl]:
    """영향이 없어야 할 자리 세 곳을 실제로 재 본다. **셋 다 목록에 남는다.**

      결과 대조 · 사건 이전 초과수익. 사건이 원인이면 이전은 조용해야 한다
      노출 대조 · 같은 타입의 다른 lifecycle_stage. 확정 전 단계가 확정과 같은 반응을
                  낸다면 잡은 것은 사건이 아니라 그 사건을 부른 무언가다
      노출 대조 · 같은 타입에서 발행 주체가 아닌 참여자. 발행 주체가 아닌 쪽까지 같은
                  크기로 움직이면 처치 정의가 종목을 특정하지 못하고 있다

    처치·대조 술어는 P3 간선(exposure/reference)에서 가져오고, 없으면 가설이 접지한
    `source_event_id` 와 산업 동종군으로 만든다. `plan` 의 실행 가능한 판별식 SQL 은
    타입·단계를 읽는 재료로만 쓴다 - 판별 설계가 이미 그 문자열을 확정했으면 다시 만들
    이유가 없다.
    """
    w1 = question.trade_date
    w0 = w1 - timedelta(days=WINDOW_DAYS)
    exposure, reference = _predicates(graph)
    if not exposure:
        log("causal.p7.done", n=3, executed=0, reason="no_exposure")
        return _all_blocked("그래프 간선에 처치 술어(exposure)가 없고 접지된 사건도 없다")
    try:
        treated = cd.cohort(exposure, as_of=question.as_of, w0=w0, w1=w1)
    except Exception as exc:  # noqa: BLE001
        log("causal.p7.failed", step="treated", error=f"{type(exc).__name__}: {exc}")
        return _all_blocked(f"처치 술어 `{exposure}` 조회 실패: {type(exc).__name__}: {exc}")
    if not treated:
        return _all_blocked(f"처치 술어 `{exposure}` 가 창 {w0}~{w1} 에서 0건이다")
    if not reference:
        reference = _reference(cd, treated, w1)
    if not reference:
        return _all_blocked("대조 술어(reference)가 없고 처치 종목의 산업도 모른다 - "
                            "비교군을 만들 수 없다")

    out = [_verdict(
        "outcome", f"사전추세 [t-{PRE_DAYS + PRE_LAG}, t-{PRE_LAG}]",
        _contrast(cd, treated, reference, name="pre_trend",
                  outcome=lambda p: cd.mom(p, days=PRE_DAYS, lag=PRE_LAG)),
        "사건 이전 누적 초과수익")]

    texts = [exposure] + [d.sql for d in plan.discriminators if d.executable and d.sql]
    events = list(dict.fromkeys(ev for h in graph.hypotheses for ev in h.events))
    f = _facts(sql, texts, events)
    drop = _keys(treated)
    if not f["type"]:
        why = f["why"] or "처치의 event_type_code 를 특정하지 못했다"
        out += _all_blocked(why)[1:]
        log("causal.p7.done", n=len(out), executed=1, reason="no_event_type")
        return out

    stages, roles = f["stages"], f["roles"] or [ISSUER]
    specs = [
        ("다른 lifecycle_stage",
         f"event_type_code = '{_lit(f['type'])}' AND {_in('lifecycle_stage', stages, negate=True)}"
         if stages else "",
         "처치의 lifecycle_stage 를 특정하지 못했다 - '다른 단계'를 정의할 수 없다",
         f"{f['type']} 의 다른 lifecycle_stage"),
        (f"비{roles[0]} 역할",
         f"event_type_code = '{_lit(f['type'])}' AND {_in('role_code', roles, negate=True)}",
         "", f"{f['type']} 의 {'·'.join(roles)} 아닌 참여자"),
    ]
    for name, where, blocked, what in specs:
        if not where:
            out.append(NegativeControl(kind="exposure", name=name, n=0, effect=None, p=None,
                                       passed=False, says=f"실행 불가: {blocked}"))
            continue
        try:
            placebo_t = [p for p in cd.cohort(where, as_of=question.as_of, w0=w0, w1=w1)
                         if (str(p[0]), str(p[1])[:10]) not in drop]
        except Exception as exc:  # noqa: BLE001
            log("causal.p7.failed", control=name, error=f"{type(exc).__name__}: {exc}")
            out.append(NegativeControl(kind="exposure", name=name, n=0, effect=None, p=None,
                                       passed=False,
                                       says=f"실행 불가: 술어 `{where}` 조회 실패: "
                                            f"{type(exc).__name__}: {exc}"))
            continue
        if not placebo_t:
            out.append(NegativeControl(kind="exposure", name=name, n=0, effect=None, p=None,
                                       passed=False,
                                       says=f"실행 불가: 술어 `{where}` 가 창 {w0}~{w1} 에서 "
                                            "0건이다 - 이 자리에 위약 사건이 없다"))
            continue
        out.append(_verdict("exposure", name,
                            _contrast(cd, placebo_t, reference, name=name, outcome=cd.ar),
                            what))

    log("causal.p7.done", n=len(out), executed=sum(1 for c in out if c.p is not None),
        loud=[c.name for c in out if c.p is not None and not c.passed])
    return out


__all__ = ["negative_controls", "screen_confounding"]
