"""오케스트레이션 — **P0–P9.** 설계도: `docs/analysis-engine/architecture/causal-attribution-p0p9.drawio`

    P0  질문 고정      반사실을 문장으로 정의한다. 답의 형태를 먼저 선언한다   무료
    --  산술 게이트    무게 없는 원인은 여기서 죽는다. **LLM 전에 돈다**       무료
    P1  지문 채취      모양·넓이·시점·사전표류·크기. 가설 이전, LLM 이전       싸다
    P2  다중 가설      어휘 무제한 · 세션 n개 독립                             비싸다
    P3  그래프 + 완비  그린 변수의 공통원인 전수 선언 + 배정기제 U 자동 삽입    비싸다
    P4  식별 · 경계    3값. `not_identified` 가 정상 종료다                    싸다
    P5  판별 설계      ★ 선언된 U 마다 소거 검정. 못 적으면 미소거로 확정       비싸다
    --  검정 실행      샌드박스 또는 축약 경로. 기존 기계를 그대로 쓴다         가장 비싸다
    --  예산           귀속의 합이 잔차를 넘으면 그래프가 틀렸다                무료
    P6  민감도         E-value. 식별이 안 될 때 강도를 재는 유일한 축           무료
    P7  음성대조·스크린 영향 없어야 할 자리가 조용한가 · 혼재 공시 제외          싸다
    P8  처분 원장      기여 / 비기여 / 미결 — 전건 명시. 침묵 금지              무료
    P9  누적           메커니즘 레지스트리. 단일 사례는 반복으로만 검정력을 얻는다 무료

**이전 구조와의 차이는 순서가 아니라 폐쇄다.** 예전에도 비용 순이었다. 바뀐 것은 셋:
회계(예산)·교란(선언된 U 가 P5 를 지나거나 P8 에 남는다)·처분(검토한 전건에 판정)이
닫힌 것이고, 그 대가로 어휘를 완전히 열었다(P2 에 골격도 후보 목록도 없다).

`explain` 의 외부 시그니처는 `adapters.llm.analyze` 가 부르는 것이므로 호환을 지킨다 -
`sql`·`registry_root` 만 선택 인자로 늘었다.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

import numpy as np

from ..adapters.causal_data import COHORT_COLUMNS, UNIVERSE_COLUMNS
from ..observability import log
from . import chain
from . import p0_question as p0
from . import p1_fingerprint as p1
from . import p2_hypotheses as p2
from . import p3_graph as p3
from . import p4_identify as p4
from . import p5_discriminate as p5
from . import p6_sensitivity as p6
from . import p7_negative as p7
from . import p8_findings as p8
from . import p9_registry as p9
from . import verify as V
from .contracts import ConfoundingScreen, DiscriminationPlan, WorldGraph
from .engine import EdgeDesign, EdgeResult, arithmetic_gate, estimate

N_HYPOTHESES = 3
# 결과 산포를 잴 대조 표본의 하한. 이보다 적으면 E-value 의 분모가 잡음이다.
_SD_MIN = 30
# 참조집합에 들어오면 안 되는 컬럼. `cd.universe` 는 종목 속성만 받고 날짜는 코드가
# 창으로 붙인다 - 모델이 여기 `trade_date` 를 박으면 창이 하루로 접힌다.
_NOT_UNIVERSE = r"\b(?:" + "|".join(
    c for c in COHORT_COLUMNS if c not in UNIVERSE_COLUMNS) + r")\b"


def explain(cd, client, *, etf_name: str, etf_instrument_id: str, trade_date: date,
            as_of: str, observed: float, route_code: str,
            contributors: list[tuple[str, float]], candidates: list[dict],
            window_days: int = 60, industry: dict | None = None,
            grounded: set[str] | None = None, sandbox: bool = True,
            docs=None, sql=None, registry_root=None) -> dict[str, Any]:
    """한 셀의 인과 설명. `Explanation.raw` 계약에 맞는 dict 를 돌려준다.

    **어느 단계도 건너뛰지 않는다.** 후보가 산술로 전멸하거나 가설이 0개여도 P8 은 돈다 -
    처분 폐쇄가 그것을 요구한다. 검토했는데 원장에 안 남는 후보가 없어야 한다.

    `sql` 은 P2·P3·P5 가 쓰는 자유 질의 표면이다(`adapters.sql_surface.SqlSurface`).
    없으면 세 단계가 조회 없이 돌고 그 사실이 산출물에 남는다 - 판별 검정은 전부
    실행 불가가 되므로 주장 상한이 자동으로 내려간다.
    """
    w1 = trade_date
    w0 = date.fromordinal(max(trade_date.toordinal() - window_days, 1))

    # P0 ─ 반사실을 먼저 정의한다
    q = p0.ask(cd, etf_name=etf_name, etf_instrument_id=etf_instrument_id,
               trade_date=trade_date, as_of=as_of, observed=observed,
               route_code=route_code, contributors=contributors, candidates=candidates)

    # 산술 게이트 ─ 가장 싼 게이트가 가장 세다. LLM 전에 돈다
    screened = [{**c, "killed": arithmetic_gate(q.residual, c.get("share"),
                                                c.get("prior") or {})}
                for c in candidates]
    alive = [c for c in screened if not c.get("killed")]
    log("causal.screened", candidates=len(screened), alive=len(alive))

    # P1 ─ 지문. 후보가 전멸해도 뜬다(무엇을 못 쟀는지가 P8 의 미결 항목이 된다)
    fp = p1.take(cd, sql, question=q, candidates=screened)

    graph = WorldGraph()
    idents: list = []
    plan = DiscriminationPlan()
    proofs: list[V.EdgeProof] = []
    budget = chain.budget([], q.residual)

    # 무사건 가설을 1급으로 올린 자리. 잔차가 이 셀 **자신의** 귀무분포 안에 있으면
    # 설명할 것이 없고, 그러면 LLM 을 부르지 않는다. 부르면 반드시 무언가를 찾아내는데
    # 그 서사는 반증 불가능하다 - 검출 하한 아래에서는 어떤 관측도 그것을 죽이지 못한다.
    # 게이트 통과 셀만 분석한다는 사실 때문에 무보정 극단성 판정은 순환논증이므로,
    # Šidák 스캔 보정을 거친 p 를 쓴다 (`p0_question._power`).
    if q.no_explanandum:
        log("causal.no_explanandum", residual=round(q.residual, 5),
            p_scan=q.p_scan, note=q.null_note)
        alive = []

    if alive:
        # P2 ─ 어휘 무제한. 세션 n개 독립
        hyps = p2.propose(client, sql, question=q, fingerprint=fp,
                          candidates=screened, n=N_HYPOTHESES)
        if hyps:
            # P3 ─ 공통원인 완비 + 배정기제 U 자동 삽입
            graph = p3.build(client, sql, question=q, hypotheses=hyps,
                             grounded=set(grounded or ()))
            # P4 ─ 3값 식별. 지지집합은 타입 과거 최대에서 온다
            idents = p4.identify_all(graph, support=_support(screened))
            # P5 ─ ★ 선언된 U 마다 소거 검정
            plan = p5.design(client, sql, question=q, fingerprint=fp,
                             graph=graph, idents=idents)
            # 검정 실행 + 예산. **구조가 안 선 그래프로는 코호트를 뽑지 않는다** -
            # 구조 검사는 무료고 검정은 가장 비싸다. 위반이 붙은 채로 내려가면 시간
            # 역행 그래프로 검정 세션을 열게 되고, 그 추정치가 처분까지 올라간다.
            if graph.violations:
                log("causal.estimate_skipped", violations=len(graph.violations))
            else:
                proofs = _estimate(cd, client, graph, idents, as_of=as_of, w0=w0, w1=w1,
                                   trade_date=trade_date,
                                   etf_instrument_id=etf_instrument_id,
                                   screened=screened, industry=industry,
                                   sandbox=sandbox, docs=docs)
                budget = chain.budget(_routes(graph, proofs), q.residual)
                log("causal.budget", share=round(budget["share"], 3),
                    over=budget["over_budget"], measured=budget["n_measured"],
                    blocked=budget["n_blocked"])

    # P6 ─ 식별이 안 될 때 강도를 재는 유일한 축
    sens = p6.evaluate(proofs, outcome_sd=_outcome_sd(cd, graph, w0, w1))

    # P7 ─ 혼재 스크린 · 음성 대조
    screen = _screen(cd, sql, as_of=as_of, screened=screened)
    controls = p7.negative_controls(cd, sql, question=q, graph=graph, plan=plan)

    # P8 ─ 전건 처분. **여기는 조건 없이 돈다**
    kw = {"question": q, "fingerprint": fp, "graph": graph, "idents": idents,
          "plan": plan, "proofs": proofs, "budget": budget, "sensitivities": sens,
          "controls": controls, "screen": screen,
          # **전건을 넘긴다.** 죽은 후보만 넘기면 산술을 통과했는데 가설로 서지 못한
          # 후보가 원장에서 통째로 사라진다 - 처분 폐쇄가 배선에서 새는 자리였다.
          "screened_candidates": screened}
    findings = p8.dispose(**kw)
    raw = p8.narrate(findings, p8.audit_block(**kw))

    # P9 ─ 누적. 뿌리가 없으면 건너뛰되 그 사실을 로그로 남긴다
    if registry_root:
        rec = p9.record(findings, graph, plan, root=registry_root, idents=idents)
        log("causal.p9.recorded", **{k: v for k, v in rec.items() if k != "mechanism_ids"})
    else:
        log("causal.p9.skipped", reason="registry_root 미지정 - 소환 기록 없음")
    return raw


# ── 검정 실행 ───────────────────────────────────────────────────────────
def _designs(g: WorldGraph) -> list[EdgeDesign]:
    """검정 대상은 **statistical 간선뿐이다.**

    항등식은 계산이고 탄력성은 출처 대조라 코호트를 짜서 검정할 대상이 아니다. 이 구분이
    없으면 계산을 검정하거나 추정을 계산으로 위장한다.

    `cause_label` 은 그 간선을 그린 가설의 뿌리 이름이다 - 통계 간선의 부모는 대개 중간
    매개(기대·심리)라 그걸 원인이라 쓰면 "주주환원 기대가 원인입니다" 가 고객에게 나간다.

    `control`(참조집합)은 **종목 속성 술어만** 쓸 수 있다 - 날짜는 코드가 창으로 붙인다.
    사건 컬럼이나 `trade_date` 가 섞이면 `cd.universe` 가 거부하고, 그러면 E-value 분모가
    통째로 미산출된다(2026-08-01 nanfix-20260801-01: 6/6 sd_failed). 여기서 걸러 빈
    문자열로 내려보내면 뒤가 산업 동종군 폴백으로 비교군을 만든다.
    """
    out: list[EdgeDesign] = []
    for e in g.edges:
        if e.get("kind") != "statistical":
            continue
        src, dst = e.get("from"), e.get("to")
        owner = next((h for h in g.hypotheses
                      if any(x.get("from") == src and x.get("to") == dst
                             for x in h.edges)), None)
        control = str(e.get("reference") or "")
        if control and re.search(_NOT_UNIVERSE, control):
            log("causal.reference_rejected", edge=f"{src}->{dst}", predicate=control[:120])
            control = ""
        # 처치 술어가 비면 **접지된 원인 노드의 타입 코드**로 만든다. 검정 에이전트가
        # 사람 말 노드에서 코호트를 짜려다 0행이 되고 n=1 로 G2 에서 죽던 자리다
        # (2026-08-01 tools-20260801-01: 4/4 "이벤트 코드를 알 수 없다").
        treated = str(e.get("exposure") or "")
        if not treated:
            code = str((g.nodes.get(src) or {}).get("event_type_code") or "")
            treated = f"event_type_code = '{code}'" if code else ""
        out.append(EdgeDesign(
            src=src, dst=dst,
            treated=treated, control=control,
            strata="date", scope="type", claims="L4",
            say=e.get("says") or "", because=e.get("because") or "",
            false_if=e.get("false_if") or "", needs=e.get("needs") or "",
            timing=e.get("timing") or "unscheduled",
            cause_label=(owner.cause_label if owner else "") or src))
    return out


def _admg(g: WorldGraph) -> list[dict]:
    """방향 간선 + **잠재를 양방향으로.** 검정 브리프가 U 를 보게 하는 유일한 경로다."""
    return list(g.edges) + [{"from": a, "to": b, "kind": "bidirected"}
                            for a, b in g.bidirected]


def _estimate(cd, client, g: WorldGraph, idents: list, *, as_of: str, w0: date, w1: date,
              trade_date: date, etf_instrument_id: str, screened: list[dict],
              industry: dict | None, sandbox: bool, docs) -> list[V.EdgeProof]:
    """간선마다 검정. 식별이 안 서는 간선도 **검정 에이전트에게 준다** - 축약형·부분식별로
    내려갈 수 있고, 못 하면 `impossible` 로 무엇이 없어서 막혔는지 돌려준다.

    P4 의 판정을 그대로 내려보낸다. 안 넘기면 `verify.plan` 이 완비 선언 없는 최소
    그래프로 **재판정**하고, 그러면 원장에 적히는 식별 상태와 추정 에이전트가 본 식별
    상태가 갈린다 - 에이전트가 원장에 없는 가정 아래 일하게 된다.
    """
    edges = _admg(g)
    by_pair = {(i.src, i.dst): i for i in idents}
    proofs: list[V.EdgeProof] = []
    for d in _designs(g):
        # 간선 하나의 검정 세션이 죽어도 **나머지는 돈다.** 안 그러면 모델의 형식 습관
        # 하나가 유니버스 런 전체를 넘어뜨린다(ALPHA-633 과 같은 비대칭 - 2026-08-01
        # ref-20260801-01 에서 검정 한 턴의 파싱 실패로 런이 exit 1 했다). 실패는 침묵이
        # 아니라 `gate_fail` 을 단 증명으로 남아 P8 이 미결로 처분한다.
        try:
            p = V.plan(g.nodes, edges, d, prior=_prior_for(d, g, screened),
                       ident=by_pair.get((d.src, d.dst)))
            if sandbox:
                proofs.append(V.verify(cd, client, d, p, as_of=as_of, w0=w0, w1=w1,
                                       trade_date=trade_date,
                                       etf_instrument_id=etf_instrument_id, docs=docs))
            elif p["strategy"] == "none":
                proofs.append(_as_proof(EdgeResult(
                    design=d, gate_fail=["식별 전략 없음 (조정 불가·도구 없음)"]), p))
            else:
                proofs.append(_as_proof(
                    estimate(cd, d, as_of=as_of, w0=w0, w1=w1, adjust=p["adjust"],
                             industry=industry), p))
        except Exception as exc:  # noqa: BLE001 - 한 간선의 실패가 런을 죽이지 않는다
            log("causal.estimate_failed", edge=f"{d.src}->{d.dst}",
                error=f"{type(exc).__name__}: {exc}"[:300])
            proofs.append(_as_proof(EdgeResult(
                design=d,
                gate_fail=[f"검정 실패: {type(exc).__name__}: {exc}"[:300]]), {}))
    log("causal.verified", edges=len(proofs),
        passed=sum(1 for r in proofs if r.passed),
        significant=sum(1 for r in proofs if r.significant),
        requests=sum(1 for r in proofs if r.data_request))
    return proofs


def _prior_for(d: EdgeDesign, g: WorldGraph, screened: list[dict]) -> dict:
    """이 간선의 원인 노드에 붙은 타입 사전. 접지된 event_id 로 잇는다."""
    ev = set((g.nodes.get(d.src) or {}).get("events") or ())
    for c in screened:
        if c.get("event_id") in ev or c.get("source_event_id") in ev:
            return c.get("prior") or {}
    return {}


def _as_proof(r: EdgeResult, p: dict) -> V.EdgeProof:
    """축약 경로 결과를 검정 결과 모양으로. **두 경로가 같은 산출 계약을 쓴다.**

    설계 자체가 실패한 간선은 `p` 가 비어 온다 - 그 자리에서 KeyError 를 내면 격리해
    살려 둔 런이 다시 죽는다(실측 2026-08-01 parse-20260801-01). 없는 설계는 빈 값이다.
    """
    return V.EdgeProof(
        design=r.design, status="통과" if r.passed else "미통과",
        strategy=p.get("strategy") or "none", adjust=list(p.get("adjust") or ()),
        iv=list(p.get("iv") or ()),
        n=r.n, effect=r.effect, p=r.p, null_sd=r.null_sd, null_kind=r.null_kind,
        unit="stock", strata_declared=r.design.strata != "none", strata_reason="",
        units=list(r.treated_ids), gate_fail=list(r.gate_fail),
        ledger=[], perms=[], code=[], turns=0)


# ── 예산 ────────────────────────────────────────────────────────────────
def _routes(g: WorldGraph, proofs: list[V.EdgeProof]) -> list[chain.Path]:
    """사슬을 세우고 statistical 칸을 검정 결과로 채운다.

    귀무 산포를 반폭으로 쓰는 이유는 그것이 **이 검정이 실제로 만든 불확실성**이라서다.
    정규 근사 신뢰구간을 따로 만들면 원장에 없는 수가 산출물에 들어간다.
    """
    got = {(r.design.src, r.design.dst): r for r in proofs if r.effect is not None}
    edges: list[chain.Edge] = []
    for e in g.edges:
        kind = e.get("kind")
        if kind not in chain.KINDS:
            continue
        iv = _interval(e.get("effect"))
        r = got.get((e.get("from"), e.get("to")))
        if kind == "statistical" and iv is None and r is not None:
            half = abs(r.null_sd or 0.0) * 1.96
            iv = chain.Interval(r.effect - half, r.effect + half)
        edges.append(chain.Edge(
            src=e["from"], dst=e["to"], kind=kind, says=e.get("says") or "",
            because=e.get("because") or "", false_if=e.get("false_if") or "",
            effect=iv, formula=e.get("formula") or "", source=e.get("source") or "",
            exposure=e.get("exposure") or "", reference=e.get("reference") or "",
            needs=e.get("needs") or ""))
    if not edges:
        return []
    target = next((h.outcome for h in g.hypotheses if h.outcome), "")
    anchors = {h.treatment: chain.Interval(*h.anchor)
               for h in g.hypotheses if h.anchor and h.treatment}
    return chain.paths(edges, target, anchors)


def _interval(raw) -> chain.Interval | None:
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return chain.Interval(float(raw[0]), float(raw[1]))
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return chain.Interval(float(raw), float(raw))
    return None


# ── P6·P7 의 재료 ───────────────────────────────────────────────────────
def _support(screened: list[dict]) -> tuple[float, float] | None:
    """결과의 지지집합. **유계 가정 없이는 Manski 경계가 무한하다.**

    이 도메인에서 방어 가능한 유계 가정은 하나뿐이다 - 그 사건 타입의 과거 |초과수익|
    최대. 후보가 여럿이면 가장 넓은 것을 쓴다(좁게 잡으면 경계가 근거 없이 좁아진다).
    """
    mx = [abs(float((c.get("prior") or {}).get("abs_max") or 0.0)) for c in screened]
    top = max(mx, default=0.0)
    return (-top, top) if top > 0 else None


def _outcome_sd(cd, g: WorldGraph, w0: date, w1: date) -> dict[str, float]:
    """결과 노드의 산포. **귀무 산포로 대신하지 않는다** - 부풀어서 E-value 가 커진다.

    대조 술어가 있는 간선의 참조집합을 창 전체로 펼쳐 초과수익 표본 표준편차를 잰다.
    술어가 없으면(검정 에이전트가 자기 코드로 비교군을 만든 경우) 잴 수 없고, 그때는
    E-value 가 미산출로 나간다 - 없는 분모를 지어내는 것보다 낫다.
    """
    out: dict[str, float] = {}
    dates = [date.fromordinal(o) for o in range(w0.toordinal(), w1.toordinal() + 1)]
    for d in _designs(g):
        if not d.control.strip():
            continue
        try:
            pairs = cd.universe(d.control, dates)
            if len(pairs) < _SD_MIN:
                continue
            v = cd.ar(pairs)
            v = v[np.isfinite(v)]
            if len(v) < _SD_MIN:
                continue
            sd = float(np.std(v, ddof=1))
        except Exception as exc:  # noqa: BLE001 - 산포 실패가 설명을 막지 않는다
            log("causal.p6.sd_failed", edge=f"{d.src}->{d.dst}",
                error=f"{type(exc).__name__}: {exc}")
            continue
        if sd > 0:
            out[f"{d.src}->{d.dst}"] = sd
            out.setdefault(d.dst, sd)
    return out


def _screen(cd, sql, *, as_of: str, screened: list[dict]) -> ConfoundingScreen:
    """혼재 공시 스크린. **처치군을 특정하지 못하면 검사했다고 말하지 않는다.**

    처치 쌍은 후보에서 온다 - 검정 결과(`EdgeProof.units`)에는 종목만 있고 날짜가 없어
    사건창을 만들 수 없다. 후보는 (instrument_id, event_date) 를 둘 다 들고 있는 유일한
    입력이고, 그게 이 셀에서 실제로 처치로 쓰인 쌍이다.
    """
    units = [(c["instrument_id"], c["event_date"])
             for c in screened
             if not c.get("killed") and c.get("instrument_id") and c.get("event_date")]
    etype = next((c.get("event_type_code") for c in screened
                  if not c.get("killed") and c.get("event_type_code")), "")
    if not units or not etype:
        return ConfoundingScreen(
            n_before=len(units), n_dropped=0, checked=False,
            note="처치군 또는 사건 타입을 특정하지 못해 혼재 공시 스크린을 돌리지 못했다")
    return p7.screen_confounding(cd, treated=units, as_of=as_of,
                                 exclude_event_type=etype, sql=sql)


__all__ = ["N_HYPOTHESES", "explain"]
