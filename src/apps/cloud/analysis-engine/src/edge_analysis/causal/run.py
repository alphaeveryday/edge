"""오케스트레이션 — **비용 순 게이트.** 싼 것부터, LLM 은 뒤로.

    1  산술    필요 초과수익 vs 타입 과거 최대            무료. LLM 전에 돈다
    2  제안    LLM 1회. 산문 DAG (설계·주장·반증조건)      비싸다
    3  구조    시간선행·종별·접지·timing·매개 선언          무료 (graph.validate)
    4  형식    반증 표면(함의 CI) 열거                    무료 (graph)
    5  식별    조정 or IV or 불가 — **코드가 정한다**        싸다
    6  검정    간선마다 검정 에이전트가 샌드박스에서 코드 실행  가장 비싸다
    7  적합    국소 CI 전수 -> 전역 Shipley C              싸다. 마지막에 한 번
    8  서술    수치는 원장에 있는 것만                      무료

왜 이 순서인가. 실측: 바이오 셀은 1번에서 1.37초에 죽었고, 우리는 그 계산을 707초짜리
검정과 여러 LLM 세션 **뒤에** 했다. 순서가 거꾸로였다.

**데이터 부재는 기각 사유가 아니다.** 구조가 맞는데 잴 수 없는 간선은 검정 에이전트가
`impossible` 로 되돌리며 무엇이 필요한지 적고, 그것이 `data_requests` 로 산출된다.
지금 단계에서 중요한 것은 커버리지가 아니라 **DAG 가 맞는 인과를 잡았는지와 그것이
검정 가능한 형태로 기술됐는지**다.

전역 적합은 **마지막에 한 번만** 본다(설계 하네스 §11). 진단은 국소가 한다 - 전역
카이제곱은 어디가 틀렸는지 안 알려준다.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np

from ..config import PipelineError
from ..observability import log
from . import agents, chain, fit
from . import graph as G
from . import verify as V
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

    간선마다 따로 잰 표본으로 전역 적합을 하면 노드끼리 다른 단위를 가리킨다. 그래서
    대조 술어로 셀 수준 유니버스를 만들고 그 위에 전부 올린다. 술어가 없으면(검정
    에이전트가 자기 코드로 비교군을 만든 경우) 공동 프레임을 만들 수 없다 - 그때는
    전역 적합을 **건너뛴다고 말한다.** 없는 프레임을 지어내지 않는다.
    """
    withpred = [d for d in designs if (d.treated or "").strip() and (d.control or "").strip()]
    if not withpred:
        return [], {}
    treated_sets = {}
    dates: set = set()
    for d in withpred:
        t = cd.cohort(d.treated, as_of=as_of, w0=w0, w1=w1)
        treated_sets[d.src] = {(i, str(dt)[:10]) for i, dt in t}
        dates |= {dt for _, dt in t}
    if not dates:
        return [], {}
    pairs = cd.universe(withpred[0].control, sorted(dates))
    for d in withpred:
        pairs += [p for p in cd.cohort(d.treated, as_of=as_of, w0=w0, w1=w1) if p not in pairs]
    if not pairs:
        return [], {}
    cols: dict[str, np.ndarray] = {}
    for node, meta in nodes.items():
        if not (meta or {}).get("observed"):
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
            grounded: set[str] | None = None, sandbox: bool = True,
            docs=None) -> dict[str, Any]:
    """한 셀의 인과 설명. `Explanation.raw` 계약에 맞는 dict 를 돌려준다.

    `sandbox=True` 면 간선마다 **검정 에이전트가 파이썬을 써서** 추정한다(원래 의도).
    False 면 축약 경로(`engine.estimate`)로 떨어진다 - 술어가 있는 간선만 검정된다.

    `docs` 가 있으면 제안이 `lookups` 로 물은 것을 정기보고서 원문에서 찾아 붙이고 다시
    묻는다. 없으면 조회 없이 진행한다 - 도메인 지식이 없다고 설명을 멈추지 않는다.

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

    # 2·3 제안 + 구조 — 비용 순서를 지킨다. **구조(무료)가 검정(비싸다)보다 먼저다.**
    text = agents.brief(etf_name=etf_name, trade_date=trade_date.isoformat(),
                        observed=observed, residual=residual, route_code=route_code,
                        contributors=contributors, candidates=screened, industry=industry)
    prop = agents.Proposal()
    violations: list[str] = []
    feedback = ""
    looked: list[tuple[str, list[dict]]] = []
    attempt = 0
    tries = 0
    while tries < 4:
        tries += 1
        # `propose` 는 **감싸지 않는다.** 전송·API 오류(402·타임아웃·응답 붕괴)를
        # `adapters/llm.py` 가 재시도 소진 후 PipelineError 로 올려 보내는데, 그건 모델이
        # 계약을 어긴 게 아니라 **소스가 죽은 것**이라 런이 죽어야 한다. 같이 감싸면
        # 크레딧이 바닥난 채 UNCERTAIN 설명으로 초록이 된다(ALPHA-589).
        raw_proposal = agents.propose(client, text, feedback=feedback)
        try:
            prop = agents.parse(raw_proposal)
        except PipelineError as exc:
            # 계약을 어긴 산출은 되먹임 대상이다 - 구조 위반·빈 코호트와 같은 취급.
            # 여기서 예외가 루프를 탈출하면 AnalyzeOne 이 exit 1 이 되고, analyze
            # 전량성공 게이트(ADR-0028)에 걸려 **유니버스 전체 런**이 FAILED 된다
            # (ALPHA-633). 틀린 그래프는 강등되는데 깨진 산출은 런을 죽이던 비대칭이다.
            attempt += 1
            log("causal.parse_rejected", attempt=attempt, error=str(exc))
            feedback = f"- 산출 계약 위반: {exc}"
            violations = [str(exc)]
            if attempt >= 2:
                prop = agents.Proposal()
                break
            continue
        # 조회 요청은 **시도 횟수를 쓰지 않는다.** 모르는 것을 묻는 것과 구조를 틀리는
        # 것은 다른 일이고, 둘을 같은 예산에서 세면 모델이 묻기를 포기한다.
        if prop.lookups and docs is not None and not looked:
            looked = _lookup(docs, prop.lookups)
            log("causal.lookups", asked=len(prop.lookups),
                hits=sum(len(h) for _, h in looked))
            feedback = agents.evidence(looked)
            continue
        attempt += 1
        log("causal.proposed", attempt=attempt, nodes=len(prop.nodes),
            edges=len(prop.chain), statistical=len(prop.designs),
            needs=len(prop.needs), lookups=len(prop.lookups))
        if not prop.chain:
            break
        # 접지 집합은 **저장소에서 온 사건 id** 다. 파이프라인이 조립해 준 것이므로
        # 구성상 접지돼 있다 - 안 넘기면 정상 제안이 접지 실패로 거부된다(실측).
        violations = G.validate(
            {"nodes": prop.nodes, "structures": [{"id": "A", "edges": prop.edges}]},
            grounded=set(grounded or ()), require_competing=False)
        if violations:
            # 위반 내용을 함께 남긴다 - 건수만으로는 사후에 무엇이 왜 거부됐는지 모른다.
            log("causal.structure_rejected", attempt=attempt, violations=len(violations),
                detail=violations[:5])
            feedback = "\n".join(f"- 구조 위반: {v}" for v in violations[:5])
            if attempt >= 2:
                prop = agents.Proposal()   # 2회차도 구조가 안 섰다 - 억지로 밀지 않는다
                break
            continue
        # 술어가 0건이면 **이유를 구별해서** 되먹임한다. 침묵하면 모델이 설계를 고치는
        # 대신 전략을 통째로 갈아탄다(실측 - 갈아탄 전략이 구조 규칙에 걸려 시도 소진).
        empty = _empty_cohorts(cd, prop.designs, as_of=as_of, w0=w0, w1=w1)
        if not empty:
            break
        log("causal.empty_cohort", attempt=attempt, edges=len(empty), detail=empty[:5])
        feedback = "\n".join(empty)
        violations = []
        if attempt >= 2:
            prop = agents.Proposal()   # 2회차도 코호트가 비었다 - 억지로 밀지 않는다
            break

    if not prop.chain:
        return narrate(replace(report, missing=missing_inputs + prop.missing,
                               local_violations=violations[:5],
                               data_requests=_requests([], prop)))

    # 4 형식 — **검정 가능한 형태인가.** 그래프에서 열거되고, 손으로 쓰지 않는다.
    # 양방향은 잠재 공통원인으로 펼쳐서 본다 - 펼치지 않으면 미지의 교란이 함의를
    # 줄이는 효과가 표면에 안 나타난다(공짜가 아니라는 사실이 사라진다).
    full = G.expand(*G.split(prop.edges))
    surface = sorted(G.fmt_ci(c) for c in G.implied_ci(prop.nodes, full))
    if not surface:
        # **빈 목록은 "반증 불가"로 읽히지만 실제로는 "함의를 열거할 쌍이 없다"다.**
        # 노드 2~3개짜리 사슬에서는 비인접 쌍이 아예 없어 조건부독립 함의가 0 이다.
        # 침묵으로 두면 사후에 "반증 표면이 비었다"와 "검사하지 않았다"를 구별할 수 없다.
        surface = [f"(조건부독립 함의 없음 - 노드 {len(prop.nodes)}개·간선 "
                   f"{len(prop.chain)}개로는 비인접 쌍이 만들어지지 않는다. 반증 경로는 "
                   "간선별 검정·예산 정합·간선의 false_if 뿐이다)"]
    log("causal.surface", implied_ci=len(surface))

    # 5·6 식별 + 검정
    proofs: list[V.EdgeProof] = []
    for d in prop.designs:
        p = V.plan(prop.nodes, prop.edges, d, prior=_prior_for(d, prop.nodes, screened))
        if p["strategy"] == "none" and not sandbox:
            proofs.append(_as_proof(EdgeResult(
                design=d, gate_fail=["식별 전략 없음 (조정 불가·도구 없음)"]), p))
            continue
        if sandbox:
            # 식별이 안 서는 간선도 **검정 에이전트에게 준다** - 축약형·부분식별로 내려갈 수
            # 있고, 못 하면 impossible 로 무엇이 없어서 막혔는지 돌려준다.
            proofs.append(V.verify(cd, client, d, p, as_of=as_of, w0=w0, w1=w1,
                                   trade_date=trade_date,
                                   etf_instrument_id=etf_instrument_id, docs=docs))
        else:
            proofs.append(_as_proof(
                estimate(cd, d, as_of=as_of, w0=w0, w1=w1, adjust=p["adjust"],
                         industry=industry), p))
    log("causal.verified", edges=len(proofs),
        passed=sum(1 for r in proofs if r.passed),
        significant=sum(1 for r in proofs if r.significant),
        requests=sum(1 for r in proofs if r.data_request))

    # 7 예산 — **귀속의 합이 잔차를 넘으면 그래프가 틀렸다.**
    # 적합도 카이제곱(`fit.global_fit`)을 여기서 쓰지 않는 이유: 그건 "이 DAG 가 모집단
    # 공분산과 정합하나"를 묻는 타입 수준 도구다. 우리 물음은 "오늘 이 움직임을 어디까지
    # 설명했나"이고, 그 답은 예산 정합이다 - 훨씬 싸고 훨씬 날카롭다.
    routes = agents.measured(prop, proofs).routes()
    budget = chain.budget(routes, residual)
    log("causal.budget", share=round(budget["share"], 3),
        over=budget["over_budget"], measured=budget["n_measured"],
        blocked=budget["n_blocked"])

    local_bad: list[str] = []
    try:
        pairs, cols = _frame(cd, prop.designs, prop.nodes, as_of=as_of, w0=w0, w1=w1)
        if cols:
            local = fit.local_fit(prop.nodes, prop.edges, cols)
            local_bad = [f"{r['X']} ⊥ {r['Y']}"
                         + (f" | {', '.join(r['Z'])}" if r["Z"] else "")
                         for r in local if r["testable"] and r["p"] < 0.05]
            log("causal.fit", violations=len(local_bad), implied=len(local))
    except Exception as exc:  # noqa: BLE001 — 함의 검정 실패가 설명을 막지 않는다
        log("causal.fit_failed", error=f"{type(exc).__name__}: {exc}")

    # 8 서술
    # 고객 문장에는 **결과 노드로 닿는 경로만** 원인으로 쓴다. 교란 간선(모멘텀 -> 사건)은
    # 식별에 필요한 구조일 뿐 "원인"이 아니다 - 실측 스모크에서 "사전 모멘텀이 원인으로
    # 확인됐습니다" 가 나왔다.
    #
    # **결과에 직결된 간선만 보면 여러 단계 사슬이 침묵한다.** 통계 간선이 상류에 있고
    # (사건 -> 매출 변화) 하류가 연역이면(매출 변화 -> ETF 수익률), 예산은 경로를 계산하고
    # 감사에는 증명이 남는데 문장은 "확인된 원인이 없습니다"가 된다. 측정된 경로를 원인으로
    # 쓴다 - 경로의 통계 간선이 그 경로의 검정 근거다.
    outcome = {prop.target} if prop.target else {d.dst for d in prop.designs}
    by_edge = {(x.design.src, x.design.dst): x for x in proofs}
    spoken: set[tuple[str, str]] = set()
    for r in [x for x in proofs if x.design.dst in outcome]:
        spoken.add((r.design.src, r.design.dst))
        share = _share_of(cd, etf_instrument_id, trade_date, r)
        contribution = (share * r.effect) if (share and r.effect is not None) else None
        against = _countervailing(contribution if contribution is not None else r.effect,
                                  residual)
        findings.append(EdgeFinding(
            cause=r.design.cause_label, because=r.design.because,
            effect=r.effect, p=r.p, n=r.n, share=share,
            contribution=contribution,
            survived=r.significant and not budget["over_budget"] and not against,
            killed_by=_killed(r) or (budget["reason"] if budget["over_budget"] else None)
            or (_COUNTER if against else None)))
    for path, proof in _upstream(routes, by_edge, spoken):
        iv = path.predict()
        contribution = iv.mid if iv else None
        against = _countervailing(contribution, residual)
        findings.append(EdgeFinding(
            cause=prop.label(path.cause), because=proof.design.because,
            effect=contribution, p=proof.p, n=proof.n, share=None,
            contribution=contribution,
            survived=proof.significant and not budget["over_budget"] and not against,
            killed_by=_killed(proof) or (budget["reason"] if budget["over_budget"] else None)
            or (_COUNTER if against else None)))
    return narrate(CausalReport(
        etf_name=etf_name, trade_date=trade_date.isoformat(), observed=observed,
        residual=residual, route_code=route_code, top_contributors=contributors,
        findings=findings, budget=_budget_row(budget, routes),
        local_violations=local_bad,
        spec_sensitive=any(r.spec_sensitive for r in proofs),
        missing=missing_inputs + prop.missing,
        proofs=[_audit_row(r) for r in proofs],
        falsification_surface=surface,
        data_requests=_requests(proofs, prop)))


def _upstream(routes: list, by_edge: dict, spoken: set) -> list[tuple]:
    """측정된 경로 중 **결과에 직결된 간선으로 이미 말한 것이 아닌** 것들.

    여러 단계 사슬(사건 -> 매출 -> 수익률)에서 통계 간선은 상류에 있다. 그 경로의 검정
    근거는 그 통계 간선의 증명이고, 크기는 사슬 곱(`path.predict()`)이다. 통계 간선이
    없는 경로(전부 연역)는 검정 근거가 없어 문장에 쓰지 않는다 - 감사 블록에만 남는다.
    """
    out = []
    for path in routes:
        if not path.measured:
            continue
        stat = [(e.src, e.dst) for e in path.edges if e.kind == "statistical"]
        if not stat or any(k in spoken for k in stat):
            continue
        proof = next((by_edge[k] for k in stat if k in by_edge), None)
        if proof is None:
            continue
        spoken.update(stat)
        out.append((path, proof))
    return out


_COUNTER = ("설명해야 할 움직임과 **반대 방향**으로 밀었습니다 - 이 경로는 그 움직임을 "
            "설명하지 않습니다(상쇄 요인).")


def _countervailing(effect: float | None, residual: float) -> bool:
    """이 경로가 잔차와 **반대 방향**인가.

    예산 정합은 크기만 본다(`abs(mid) > cap`). 그래서 잔차가 -3% 인데 +2% 로 유의한
    경로가 한도 안에 들어와 "원인으로 확인됐습니다"로 게시된다 - 설명해야 할 움직임을
    설명하지 않고 반대로 민 것을 원인이라 부르는 문장이다. 상쇄 요인은 값 있는 사실이지만
    원인 확정과는 다른 말이라, 여기서 갈라 문장에 사유를 남긴다.

    부호가 없거나(측정 실패) 잔차가 0 이면 판정하지 않는다 - 없는 근거로 죽이지 않는다.
    """
    if effect is None or residual == 0.0 or effect == 0.0:
        return False
    return (effect > 0) != (residual > 0)


def _empty_cohorts(cd, designs: list[EdgeDesign], *, as_of: str,
                   w0: date, w1: date) -> list[str]:
    """어느 술어가 아무것도 맞히지 못했나. 되먹임 문장으로 돌려준다."""
    out = []
    for d in designs:
        if not (d.treated or "").strip():
            continue          # 술어 없는 간선은 검정 에이전트가 자기 코드로 비교군을 만든다
        try:
            t = cd.cohort(d.treated, as_of=as_of, w0=w0, w1=w1)
        except Exception as exc:  # noqa: BLE001 — 잘못된 술어도 되먹임 대상이다
            out.append(f"- 처치 술어 `{d.treated}` 가 실행되지 않았다: "
                       f"{type(exc).__name__}: {exc}")
            continue
        if not t:
            out.append(f"- 처치 술어 `{d.treated}` 가 창 {w0}~{w1} 에서 0건이다."
                       + _surrogate_hint(d.treated))
            continue
        if not (d.control or "").strip():
            continue
        try:
            c = cd.universe(d.control, sorted({dt for _, dt in t}), exclude=t)
        except Exception as exc:  # noqa: BLE001
            out.append(f"- 대조 술어 `{d.control}` 가 실행되지 않았다: "
                       f"{type(exc).__name__}: {exc}")
            continue
        if not c:
            out.append(f"- 대조 술어 `{d.control}` 가 그 날짜들에서 0건이다 "
                       f"(처치 {len(t)}건은 있다)." + _surrogate_hint(d.control))
    return out


# 티커꼴 리터럴: 6자리 숫자, 또는 KRX 신형(숫자 4 + 영문 1 + 숫자 1, 예 0007C0).
_TICKER_LITERAL = re.compile(r"'(?:\d{6}|\d{4}[A-Z]\d)'")


def _surrogate_hint(predicate: str) -> str:
    """`instrument_id` 에 티커를 넣은 술어에 그 사실을 말해준다.

    0건의 이유가 "그런 사건이 없다"가 아니라 "컬럼을 잘못 골랐다"일 때, 그 구별을 주지 않으면
    LLM 이 설계를 고치는 대신 전략을 통째로 갈아탄다 - 실제로 그랬고, 갈아탄 전략(가격 노드
    직결)이 구조 규칙에 걸려 2회차까지 소진됐다. `instrument_id` 는 불투명 서로게이트이고
    티커는 `ticker` 컬럼에 있다.
    """
    if "instrument_id" not in predicate or not _TICKER_LITERAL.search(predicate):
        return ""
    return (" `instrument_id` 는 티커가 아니라 불투명 식별자(`inst_...`)다 - "
            "티커로 고르려면 `ticker` 를 써라.")


def _lookup(docs, queries: list[str], *, k: int = 3,
            cap: int = 6) -> list[tuple[str, list[dict]]]:
    """모델이 만든 질의를 도메인 문서에 던진다. **질의는 우리가 고르지 않는다.**

    상한을 두는 이유는 비용뿐이다(질의마다 임베딩 1회 + rerank 1회). 조회 실패가 설명을
    막지 않도록 예외는 삼키고 빈 결과로 남긴다 - 빈 결과 자체가 "못 찾았다"는 되먹임이 되고,
    프롬프트가 그 경우 추측하지 말라고 이미 말한다.
    """
    out: list[tuple[str, list[dict]]] = []
    for q in queries[:cap]:
        try:
            out.append((q, docs.search(q, k=k)))
        except Exception as exc:  # noqa: BLE001 — 조회 실패가 설명을 막지 않는다
            log("causal.lookup_failed", query=q[:60],
                error=f"{type(exc).__name__}: {exc}")
            out.append((q, []))
    return out


def _budget_row(b: dict, routes: list) -> dict[str, Any]:
    """예산 정합을 아카이브 모양으로. **미설명분을 항상 싣는다.**

    바텀업 귀속은 설명 못 한 부분을 숨길 수 없는 구조다. 그 성질을 산출물에서 유지하는
    것이 여기서의 정직성 장치다 - 설명 비율만 내면 남은 폭이 사라진다.
    """
    return {
        "residual": b["residual"], "share": b["share"],
        "explained": [b["explained"].lo, b["explained"].hi],
        "unexplained": b["unexplained"],
        "over_budget": b["over_budget"], "reason": b["reason"],
        "n_paths": b["n_paths"], "n_measured": b["n_measured"],
        "n_blocked": b["n_blocked"], "blocked": b["blocked"],
        "paths": [{"cause": p.cause, "kinds": p.kinds,
                   "steps": [f"{e.src}→{e.dst}" for e in p.edges],
                   "predict": (str(p.predict()) if p.measured else None),
                   "widest": (w.says[:60] if (w := p.widest()) else None)}
                  for p in routes],
    }


def _prior_for(design: EdgeDesign, nodes: dict, candidates: list[dict]) -> dict:
    """이 간선의 원인 노드에 붙은 타입 사전. 접지된 event_id 로 잇는다.

    새 계약은 접지 사건을 `events` 에 담는다(`graph.validate` 도 그 필드를 본다).
    `member_events` 만 읽으면 정상 제안에서 항상 비고, 그러면 사건이 둘 이상인 셀에서
    **다른 사건의 사전**이 검정 세션에 실린다 - 잘못된 모집단·귀무 맥락으로 유도된다.
    """
    meta = nodes.get(design.src) or {}
    ev = {str(x) for x in (meta.get("events") or meta.get("member_events") or [])}
    for c in candidates:
        if c.get("event_id") and c["event_id"] in ev:
            return c.get("prior") or {}
    for c in candidates:
        if not c.get("killed"):
            return c.get("prior") or {}
    return {}


def _as_proof(r: EdgeResult, p: dict) -> V.EdgeProof:
    """축약 경로 결과를 검정 결과 모양으로. **두 경로가 같은 산출 계약을 쓴다.**

    수치 원천이 둘이면 어느 쪽이 말한 것인지 사후에 알 수 없다. 그래서 모양을 하나로
    맞추고 `strategy` 로 어느 경로였는지 남긴다.
    """
    return V.EdgeProof(
        design=r.design, status=("통과" if r.passed else "게이트실패"),
        n=r.n, effect=r.effect, p=r.p, null_sd=r.null_sd, null_kind=r.null_kind,
        adjust=list(r.adjust), strategy=r.strategy, iv=list(r.iv),
        units=list(r.treated_ids), gate_fail=list(r.gate_fail),
        data_request=({"need": f"{p['from']}→{p['to']} 축약 경로가 막혔다: "
                               f"{'; '.join(r.gate_fail[:2])}",
                       "grain": "미분류", "unlocks": "", "why": "; ".join(r.gate_fail),
                       "edge": f"{p['from']}→{p['to']}"} if r.gate_fail else None),
        turns=0)


def _audit_row(r: V.EdgeProof) -> dict[str, Any]:
    """아카이브에 남는 간선 한 줄. **설계·산문·원장이 전부 여기 있다.**

    이게 없으면 사후에 "무엇을 무엇과 비교해서 이 p 가 나왔는가"를 재구성할 수 없다.
    게이트는 구성상 통과하지만 그 통과의 증거가 사라진다.
    """
    d = r.design
    return {
        "edge": f"{d.src}→{d.dst}",
        "say": d.say, "because": d.because, "false_if": d.false_if,
        "claims": d.claims, "null_ok": sorted(V.NULL_OK.get(d.claims, ())),
        "timing": d.timing, "scope": d.scope,
        "treated": d.treated, "control": d.control, "strata_design": d.strata,
        "needs": d.needs,
        "status": r.status, "strategy": r.strategy, "adjust": r.adjust, "iv": r.iv,
        "n": r.n, "effect": r.effect, "p": r.p, "null_sd": r.null_sd,
        "null_kind": r.null_kind, "unit": r.unit, "strata_used": r.strata_declared,
        "strata_reason": r.strata_reason,
        "units": r.units, "gate_fail": r.gate_fail,
        "turns": r.turns, "n_placebo": len(r.ledger), "n_permute": len(r.perms),
        "spec_sensitive": r.spec_sensitive,
        "ledger": r.ledger, "perms": r.perms, "code": r.code,
    }


def _requests(proofs: list[V.EdgeProof], prop: agents.Proposal) -> list[dict]:
    """데이터 요청 큐. **못 잰 것은 침묵이 아니라 요청이다.**

    셀이 쌓이면 이 표가 수집 우선순위가 된다. 그래서 같은 요청은 간선을 합쳐 한 줄로 둔다.
    """
    out: dict[str, dict] = {}
    for r in proofs:
        if r.data_request:
            out.setdefault(str(r.data_request.get("need"))[:80], dict(r.data_request))
    for d in prop.designs:
        if not d.needs:
            continue
        key = d.needs[:80]
        if key in out:
            out[key]["edge"] = f"{out[key].get('edge', '')}, {d.src}→{d.dst}"
        else:
            out[key] = {"need": d.needs, "grain": "미분류", "unlocks": d.say or d.because,
                        "why": "제안이 간선을 남기고 데이터를 요청했다",
                        "edge": f"{d.src}→{d.dst}"}
    for m in prop.missing:
        out.setdefault(m[:80], {"need": m, "grain": "미분류", "unlocks": "",
                                "why": "제안이 셀 수준에서 요청했다", "edge": ""})
    return list(out.values())


def _killed(r: V.EdgeProof) -> str | None:
    if r.status == "불가":
        need = str((r.data_request or {}).get("need") or "필요한 자료")
        return f"확인에 필요한 자료가 없어 검정하지 못했습니다 ({need[:80]})."
    if r.gate_fail:
        return "검정 요건을 채우지 못했습니다 (" + "; ".join(r.gate_fail[:2]) + ")."
    if r.p is not None and r.p >= 0.05:
        return (f"같은 종류의 사건 {r.n}건을 모아 비교했으나 "
                "우연과 구별되는 차이는 없었습니다.")
    return None


def _share_of(cd, etf_instrument_id: str, trade_date: date, r: V.EdgeProof) -> float | None:
    """이 간선의 **처치 단위**가 ETF 에서 차지하는 비중. 실패는 None - 0 으로 대체하지 않는다.

    결측과 0 은 다르다. 0 을 돌려주면 산술 기각과 "조회 실패"가 같은 문장이 된다.
    """
    if not etf_instrument_id or not r.units:
        return None
    try:
        return cd.weight(etf_instrument_id, trade_date, r.units).get("share")
    except Exception as exc:  # noqa: BLE001 — 비중 조회 실패가 설명을 막지 않는다
        log("causal.share_failed", error=f"{type(exc).__name__}: {exc}")
        return None


__all__ = ["explain", "identify"]
