"""오케스트레이션 — **비용 순 게이트.** 싼 것부터, LLM 은 마지막에 가깝게.

    1  산술    필요 초과수익 vs 타입 과거 최대            무료. LLM 전에 돈다
    2  제안    LLM 1회. 설계만                          비싸다
    3  구조    시간선행·종별·매개·timing (graph.validate) 무료
    4  식별    조정 or IV or 불가 (코드가 정한다)          싸다
    5  추정    간선마다 층화 순열 검정                     중간
    6  적합    국소 CI 전수 -> 전역 Shipley C            싸다. 마지막에 한 번
    7  서술    수치는 계산된 것만                         무료

왜 이 순서인가. 실측: 바이오 셀은 1번에서 1.37초에 죽었고, 우리는 그 계산을 707초짜리
검정과 여러 LLM 세션 **뒤에** 했다. 순서가 거꾸로였다.

전역 적합은 **마지막에 한 번만** 본다(§11). 진단은 국소가 한다 - 전역 카이제곱은
어디가 틀렸는지 안 알려준다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np

from ..observability import log
from . import agents, fit
from . import graph as G
from .engine import EdgeDesign, EdgeResult, arithmetic_gate, estimate, identify
from .narrate import CausalReport, EdgeFinding, narrate

# 그래프 노드 id 를 관측 열로 잇는 규약. 여기 한 곳에서만 안다.
_MOM, _VOL, _CAP = "MOM", "VOL", "LOGCAP"


def _screen(candidates: list[dict], residual: float) -> list[dict]:
    """산술 게이트. **가장 싼 게이트가 가장 세다** - 무게 없는 원인은 여기서 죽는다."""
    return [{**c, "killed": arithmetic_gate(residual, c.get("share"), c.get("prior") or {})}
            for c in candidates]


def _frame(cd, designs: list[EdgeDesign], nodes: dict, *, as_of: str,
           w0: date, w1: date) -> tuple[list, dict]:
    """전역 적합용 **공동 프레임.** 모든 노드가 같은 (instrument, date) 위의 열이 된다.

    간선마다 따로 잰 표본으로 전역 적합을 하면 노드끼리 다른 단위를 가리킨다.
    그래서 첫 간선의 대조 술어로 셀 수준 유니버스를 만들고 그 위에 전부 올린다.
    """
    if not designs:
        return [], {}
    treated_sets = {}
    dates: set = set()
    for d in designs:
        t = cd.cohort(d.treated, as_of=as_of, w0=w0, w1=w1)
        treated_sets[d.src] = {(i, str(dt)[:10]) for i, dt in t}
        dates |= {dt for _, dt in t}
    if not dates:
        return [], {}
    pairs = cd.universe(designs[0].control, sorted(dates))
    for d in designs:
        pairs += [p for p in cd.cohort(d.treated, as_of=as_of, w0=w0, w1=w1) if p not in pairs]
    if not pairs:
        return [], {}
    cols: dict[str, np.ndarray] = {}
    for node, meta in nodes.items():
        if str(meta.get("kind")) not in ("OBSERVABLE", "SHOCK", "CONFOUND", "TARGET"):
            continue           # 잠재는 열이 없다 - fit 이 "미검정"으로 표시한다
        head = node.split("@")[0].upper()
        if node in treated_sets:
            s = treated_sets[node]
            cols[node] = np.array([1.0 if (i, str(dt)[:10]) in s else 0.0 for i, dt in pairs])
        elif head.startswith(_MOM):
            cols[node] = cd.mom(pairs)
        elif head.startswith(_VOL):
            cols[node] = cd.vol(pairs)
        elif head.startswith(_CAP):
            continue           # 시가총액은 프레임에 안 올린다(분류 원장 별도 조회)
        else:
            cols[node] = cd.ar(pairs)
    return pairs, cols


def explain(cd, client, *, etf_name: str, etf_instrument_id: str, trade_date: date,
            as_of: str, observed: float, route_code: str,
            contributors: list[tuple[str, float]], candidates: list[dict],
            window_days: int = 60, industry: dict | None = None,
            grounded: set[str] | None = None) -> dict[str, Any]:
    """한 셀의 인과 설명. `Explanation.raw` 계약에 맞는 dict 를 돌려준다.

    **잔차는 엔진이 계산한다.** 파이프라인의 `Decomposition` 에는 잔차가 없다 -
    `proxy_ret` 은 ETF 자체 등락이다. 그걸 잔차로 넘기면 설명해야 할 폭이 부풀고,
    산술 게이트가 헐거워진다. 시장(횡단면 평균) 대비를 여기서 직접 뺀다.
    """
    w1 = trade_date
    w0 = date.fromordinal(max(trade_date.toordinal() - window_days, 1))
    missing_inputs: list[str] = []
    residual = observed
    if etf_instrument_id:
        try:
            ex = cd.ar([(etf_instrument_id, trade_date)])
            if len(ex) and np.isfinite(ex[0]):
                residual = float(ex[0])
            else:
                missing_inputs.append("ETF 당일 시장대비 초과수익")
        except Exception as exc:  # noqa: BLE001 — 잔차 실패가 설명을 막지 않는다
            log("causal.residual_failed", error=f"{type(exc).__name__}: {exc}")
            missing_inputs.append("ETF 당일 시장대비 초과수익")
    else:
        missing_inputs.append("ETF instrument_id")

    # 1 산술 — LLM 전에
    screened = _screen(candidates, residual)
    alive = [c for c in screened if not c.get("killed")]
    log("causal.screened", candidates=len(screened), alive=len(alive))
    findings = [EdgeFinding(cause=c.get("label") or c["event_type_code"], because="",
                            effect=None, p=None, n=0, share=c.get("share"),
                            killed_by=c["killed"])
                for c in screened if c.get("killed")]

    report = CausalReport(etf_name=etf_name, trade_date=trade_date.isoformat(),
                          observed=observed, residual=residual, route_code=route_code,
                          top_contributors=contributors, findings=findings,
                          missing=list(missing_inputs))
    if not alive:
        log("causal.done", reason="arithmetic_gate", findings=len(findings))
        return narrate(report)

    # 2·3 제안 + 구조 — 비용 순서를 지킨다. **구조(무료)가 코호트 조회보다 먼저다.**
    # 되먹임은 최대 1회. 에이전트는 도구가 없어 자기 술어가 무언가를 맞히는지 제안 전에
    # 확인할 수 없으므로, 사유를 돌려주고 한 번만 다시 묻는다.
    text = agents.brief(etf_name=etf_name, trade_date=trade_date.isoformat(),
                        observed=observed, residual=residual, route_code=route_code,
                        contributors=contributors, candidates=screened)
    nodes: dict = {}
    designs: list[EdgeDesign] = []
    missing: list[str] = []
    edges: list[dict] = []
    violations: list[str] = []
    feedback = ""
    for attempt in (1, 2):
        nodes, designs, got = agents.parse(agents.propose(client, text, feedback=feedback))
        missing = list(dict.fromkeys(missing + got))
        log("causal.proposed", attempt=attempt, nodes=len(nodes), edges=len(designs))
        if not designs:
            break
        edges = [{"from": d.src, "to": d.dst, "timing": d.timing} for d in designs]
        # 접지 집합은 **저장소에서 온 사건 id** 다. 파이프라인이 조립해 준 것이므로
        # 구성상 접지돼 있다 - 안 넘기면 정상 제안이 접지 실패로 거부된다(실측).
        violations = G.validate({"nodes": nodes, "structures": [{"id": "A", "edges": edges}]},
                                grounded=set(grounded or ()), require_competing=False)
        if violations:
            log("causal.structure_rejected", attempt=attempt, violations=len(violations))
            feedback = "\n".join(f"- 구조 위반: {v}" for v in violations[:5])
            continue
        empty = _empty_cohorts(cd, designs, as_of=as_of, w0=w0, w1=w1)
        if not empty:
            break
        log("causal.empty_cohort", attempt=attempt, edges=len(empty))
        feedback = "\n".join(empty)
        violations = []
    else:
        designs = []          # 2회차도 못 세웠다 - 억지로 밀지 않는다

    if not designs:
        return narrate(replace(report, missing=missing_inputs + missing,
                               local_violations=violations[:5]))

    # 4·5 식별 + 추정
    results: list[EdgeResult] = []
    for d in designs:
        ident = identify(nodes, edges, d.src, d.dst)
        if ident["strategy"] == "none":
            results.append(EdgeResult(design=d, gate_fail=["식별 전략 없음 (조정 불가·도구 없음)"]))
            continue
        if ident["strategy"] == "iv":
            # IV 추정은 아직 붙이지 않았다 - 조용히 조정으로 떨어지면 편향을 숨긴다
            results.append(EdgeResult(design=d, strategy="iv", iv=ident["iv"],
                                      gate_fail=["조정 불가 - 도구변수 추정 미구현"]))
            continue
        results.append(estimate(cd, d, as_of=as_of, w0=w0, w1=w1,
                                adjust=ident["adjust"], industry=industry))
    log("causal.estimated", edges=len(results),
        passed=sum(1 for r in results if r.passed),
        significant=sum(1 for r in results if r.significant))

    # 6 적합 — 마지막에 한 번
    global_fit: dict[str, Any] = {}
    local_bad: list[str] = []
    try:
        pairs, cols = _frame(cd, designs, nodes, as_of=as_of, w0=w0, w1=w1)
        if cols:
            local = fit.local_fit(nodes, edges, cols)
            global_fit = fit.global_fit(local)
            local_bad = [f"{r['X']} ⊥ {r['Y']}"
                         + (f" | {', '.join(r['Z'])}" if r["Z"] else "")
                         for r in local if r["testable"] and r["p"] < 0.05]
            log("causal.fit", **{k: global_fit.get(k) for k in ("C", "df", "p", "k")})
    except Exception as exc:  # noqa: BLE001 — 적합 실패가 설명을 막지 않는다
        log("causal.fit_failed", error=f"{type(exc).__name__}: {exc}")
        global_fit = {"testable": False, "reason": f"{type(exc).__name__}"}

    # 7 서술
    # 고객 문장에는 **결과 노드로 들어오는 간선만** 원인으로 쓴다. 교란 간선
    # (모멘텀 -> 사건)은 식별에 필요한 구조일 뿐 "원인"이 아니다 - 실측 스모크에서
    # "사전 모멘텀이 원인으로 확인됐습니다" 가 나왔다.
    outcome = {n for n, m in nodes.items() if str(m.get("kind")) == "TARGET"} or {
        d.dst for d in designs}
    for r in [x for x in results if x.design.dst in outcome]:
        share = _share_of(cd, etf_instrument_id, trade_date, r)
        findings.append(EdgeFinding(
            cause=r.design.cause_label, because=r.design.because,
            effect=r.effect, p=r.p, n=r.n, share=share,
            contribution=(share * r.effect) if (share and r.effect is not None) else None,
            survived=r.significant,
            killed_by=_killed(r)))
    return narrate(CausalReport(etf_name=etf_name, trade_date=trade_date.isoformat(),
                                observed=observed, residual=residual, route_code=route_code,
                                top_contributors=contributors, findings=findings,
                                global_fit=global_fit, local_violations=local_bad,
                                missing=missing_inputs + missing))


def _empty_cohorts(cd, designs: list[EdgeDesign], *, as_of: str,
                   w0: date, w1: date) -> list[str]:
    """어느 술어가 아무것도 맞히지 못했나. 되먹임 문장으로 돌려준다."""
    out = []
    for d in designs:
        try:
            t = cd.cohort(d.treated, as_of=as_of, w0=w0, w1=w1)
        except Exception as exc:  # noqa: BLE001 — 잘못된 술어도 되먹임 대상이다
            out.append(f"- 처치 술어 `{d.treated}` 가 실행되지 않았다: "
                       f"{type(exc).__name__}: {exc}")
            continue
        if not t:
            out.append(f"- 처치 술어 `{d.treated}` 가 창 {w0}~{w1} 에서 0건이다.")
            continue
        try:
            c = cd.universe(d.control, sorted({dt for _, dt in t}), exclude=t)
        except Exception as exc:  # noqa: BLE001
            out.append(f"- 대조 술어 `{d.control}` 가 실행되지 않았다: "
                       f"{type(exc).__name__}: {exc}")
            continue
        if not c:
            out.append(f"- 대조 술어 `{d.control}` 가 그 날짜들에서 0건이다 "
                       f"(처치 {len(t)}건은 있다).")
    return out


def _killed(r: EdgeResult) -> str | None:
    if r.gate_fail:
        return "검정 요건을 채우지 못했습니다 (" + "; ".join(r.gate_fail[:2]) + ")."
    if r.p is not None and r.p >= 0.05:
        return (f"같은 종류의 사건 {r.n}건을 모아 비교했으나 "
                "우연과 구별되는 차이는 없었습니다.")
    return None


def _share_of(cd, etf_instrument_id: str, trade_date: date, r: EdgeResult) -> float | None:
    """이 간선의 **처치 종목**이 ETF 에서 차지하는 비중. 실패는 None - 0 으로 대체하지 않는다.

    결측과 0 은 다르다. 0 을 돌려주면 산술 기각과 "조회 실패"가 같은 문장이 된다.
    """
    if not etf_instrument_id or not r.treated_ids:
        return None
    try:
        return cd.weight(etf_instrument_id, trade_date, r.treated_ids).get("share")
    except Exception as exc:  # noqa: BLE001 — 비중 조회 실패가 설명을 막지 않는다
        log("causal.share_failed", error=f"{type(exc).__name__}: {exc}")
        return None
