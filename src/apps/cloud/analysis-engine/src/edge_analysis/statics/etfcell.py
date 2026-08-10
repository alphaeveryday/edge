"""ETF 셀 워크플로우 — 층 분해 → **라우팅** → 층에 맞는 인과 시행 → 산문.

## 왜 라우팅이 먼저인가

지금까지는 어느 층이 끌었는지 보지 않고 종목 가설부터 세웠다. 실측(042700 07-31):
하루의 77% 가 시장인데 9간선 전부 종목 가설이었고, 전부 죽었다. **틀린 질문을 잘
검정한 것**이다.

라우팅이 질문을 정한다:

    시장 지배    밤사이 해외 팩터 · 장중 시장 사건 · 국면으로 환원. 종목 가설 금지
    섹터 지배    그 업종 공통 처치. 종목 개별성은 조절자로만
    고유 지배    |기여| 상위 3종목에 개별 RCT 시행
    혼합         어느 층도 55% 미달 - 층별로 나눠 묻고 그 사실을 말한다
    괴리단독     바스켓 미이동 - ETF 수급/유동성. 구성종목으로 안 내려간다

## 설명할 수 없는 것은 접는다. 아무것도 설명 못 하는 건 안 된다

라우팅이 그 균형을 잡는다. 시장 지배면 종목 인과는 접지만 **시장 환원은 반드시
말한다**(밤사이 팩터 · 서킷브레이커 · 국면). 직관적으로 보이는 요인을 침묵하지
않는다.

사용:  python -m edge_analysis.statics.etfcell <etf_ticker> <YYYY-MM-DD>
"""
from __future__ import annotations

import os
from dataclasses import replace

from ..config import PipelineError
from .route import route_etf, say_route
from .interval import (
    ROLL_UNSET,
    build_block_plan,
    final_explanation_payload,
    window_facts,
)


def _thread_news_lines(rows: list[dict]) -> tuple[str, ...]:
    """Render one stable customer sentence per thread without dropping event lineage."""
    def canonical_key(row: dict) -> tuple[str, ...]:
        return tuple(str(row.get(field) or "") for field in (
            "available_at", "title", "evidence_id", "event_type_code",
            "thread_id", "source_event_id",
        ))

    grouped: dict[str, dict[str, dict]] = {}
    for row in rows:
        event_id = str(row["source_event_id"])
        thread_id = str(row.get("thread_id") or event_id)
        events = grouped.setdefault(thread_id, {})
        current = events.get(event_id)
        if current is None or canonical_key(row) > canonical_key(current):
            events[event_id] = row

    lines = []
    for thread_id in sorted(grouped):
        events = grouped[thread_id]
        representative = max(
            events.values(),
            key=lambda row: (str(row.get("available_at") or ""),
                             str(row["source_event_id"])),
        )
        available_at = str(representative.get("available_at") or "")
        clock = available_at[11:16] if len(available_at) >= 16 else available_at
        title = str(representative["title"])
        prefix = f"{clock}, {title}" if clock else title
        lines.append(f"{prefix} (관련 기사 {len(events)}건)")
    return tuple(lines)


def run(lake, etf: str, day: str, ask=None, *, instrument_id: str | None = None,
        window_start: str | None = None, window_end: str | None = None,
        summary: bool = False, window_meta: dict | None = None,
        roll=ROLL_UNSET) -> str:
    """ETF 하루 또는 요청창 하나를 설명한다.

    ``roll`` 은 호출자가 **이미 계산한** 구간 층 분해다(`pipeline` 의 라우팅이 쓴 것).
    요청창 갈래가 그것을 그대로 쓴다 - 라우팅과 설명이 같은 분해를 봐야 원장의
    route_code 와 산문 근거의 계보가 갈리지 않는다. ``None`` 도 전달된 값이다(라우팅이
    못 얻었다는 사실). 계약은 `interval.window_facts` 가 갖는다.
    """
    if window_start is not None or window_end is not None:
        if not instrument_id or window_start is None or window_end is None:
            raise ValueError("요청창 설명에는 instrument_id·window_start·window_end가 필요하다")
        facts = window_facts(
            lake, etf, instrument_id, day, window_start, window_end, roll=roll)
        # 검정 산출은 **고객 산문에 싣지 않는다**(ALPHA-876, 근거 명세 v3 §0) -
        # 카드에 통계 어휘 금지·완성 문장 저장 금지. 레코드는 stage_results 버퍼
        # (`stat_tests`)에만 보존하고, 근거 포맷 구현 시 통계검정 레코드(§3)의
        # 입력이 된다. 산문 승격은 그 포맷의 렌더 계층 몫이다.
        scoped_news: list[dict] = []
        stat_tests, hypothesis_trials = _window_paneltest(
            lake, instrument_id, day, ask, facts, news_out=scoped_news)
        if any(row.get("reason") == "OBJECTSET_UNAVAILABLE" for row in stat_tests):
            raise PipelineError("OBJECTSET_UNAVAILABLE: scoped news lookup failed")
        if scoped_news:
            facts = replace(
                facts,
                news=_thread_news_lines(scoped_news),
                event_ids=tuple(sorted(set(facts.event_ids) | {
                    str(row["source_event_id"]) for row in scoped_news})),
            )
        final_payload = final_explanation_payload(facts)
        final = final_payload["rendered_text"]
        plan = build_block_plan(facts)
        if window_meta is not None:
            if stat_tests:
                window_meta["stat_tests"] = list(stat_tests)
            if hypothesis_trials:
                # 가설 원장(ALPHA-881) 행 재료 — etfcell 은 store 를 모르므로
                # window_meta 로 올리고 pipeline 이 영속한다(단일 writer, ADR-0005).
                window_meta["hypothesis_trials"] = list(hypothesis_trials)
            window_meta.update({
                "window_start": facts.window_start,
                "window_end": facts.window_end,
                "as_of": facts.window_end,
                "blocks": [
                    {
                        "kind": block.key,
                        "evidence_ids": list(block.evidence_ids),
                        "final": list(block.lines),
                    }
                    for block in plan
                ],
                "lineage": list(facts.lineage),
                "news_events": [{key: value for key, value in row.items() if key != "line"}
                                for row in scoped_news],
                "final_explanation": final_payload,
            })
        return final
    from .premium import screen
    from .premium5 import premium_5m
    out: list[str] = []

    if roll is ROLL_UNSET:
        raise PipelineError("일봉 층 분해 실행 경로는 제거됐다 - 요청창 roll 이 필요하다")
    if roll is None:
        # **부재는 예외로 말한다.** 산문으로 돌려주면 호출자가 그것을 정상 설명과 못 가르고
        # 게시본 자리를 내준다 - 판정불가가 발화의 게시본을 선점하면 나중의 제대로 된
        # 설명이 DRAFT 로 밀린다. 예외로 올리면 `pipeline` 의 표면 부재 경로 하나로 모인다.
        raise PipelineError(f"층 분해 불가 — 구성종목 이력 또는 ETF 봉이 없다 ({etf} {day})")
    # 괴리 판정은 `premium.screen` 이 셀 목록으로 준다 (NAV vs 가격). 그날 이 ETF 의
    # 셀이 없으면 판정 없이 진행한다 - 없는 판정을 만들지 않는다.
    pv = None
    try:
        target_id = instrument_id
        if target_id is None:
            found = lake.sql(
                "SELECT instrument_id FROM rdb.public.instrument "
                f"WHERE ticker = '{etf.split('.')[0]}' LIMIT 1")
            target_id = str(found[0][0]) if found else None
        pv = next((c.verdict for c in screen(lake, day, day)
                   if c.etf_id == target_id), None)
        if pv is None:
            # **사유를 틀리게 쓰지 않는다.** `screen` 은 트리거가 울린 셀만 돌려주므로
            # 부재의 원인은 NAV 가 아니라 **트리거 미발화**일 수 있다. 실측(091160
            # 07-27): 여기서 'NAV 커버리지 밖' 이라 말하는 같은 산출물의 다음 줄이
            # NAV 로 5분 괴리를 분해했다 - 진단이 자기모순이면 백필 우선순위가 틀어진다.
            out.append("[괴리] 이 ETF·이 날의 괴리 셀 없음 — 트리거 미발화 또는 NAV·종가 "
                       "부재 (둘 중 무엇인지는 아래 5분 분해 줄이 가른다). "
                       "바스켓/수급 분기 없이 층 라우팅만 한다")
    except Exception as e:                          # noqa: BLE001 - 부재는 사유와 함께
        out.append(f"[괴리] 판정 불가 — {type(e).__name__}: {str(e)[:70]}")
    # 5분 괴리 분해는 **선제적**이다 - NAV 가 있으면 하루를 바스켓 몫과 괴리변화 몫으로
    # 쪼갠다(로그 항등식). 재료가 없으면 사유 한 줄만 남기고 넘어간다 - 이것 때문에
    # 셀 설명이 멈추면 안 된다. 실측(2026-08): NAV 33종목 중 5분봉이 겹치는 것 1종목.
    split, why5 = premium_5m(lake, etf, day)
    out.append(f"[괴리·5분] {why5}")
    if split is not None:
        # 괴리 몫이 **어느 창에서** 났는지 짚는다 - 하루 합만 주면 '방금 왜' 에 답이 없다
        for w in sorted(split.wins, key=lambda x: -abs(x.d_prem))[:3]:
            out.append(f"  {str(w.ts)[11:16]} 괴리 {w.premium * 100:+.2f}% · "
                       f"ETF {w.r_etf * 100:+.2f}%p = 바스켓 {w.r_bk * 100:+.2f}%p "
                       f"+ 괴리 {w.d_prem * 100:+.2f}%p")

    out.append(f"[ETF] {etf} {roll.etf_name} · {day} · 하루 "
               f"{roll.total * 100:+.2f}%p (로그)")
    out.append(f"  귀속 커버리지 {roll.weight_covered:.0%} · 구성종목 {roll.n_names}"
               + (f" · 추적오차 {roll.rollup_gap * 100:+.2f}%p" if roll.rollup_gap else "")
               + (f" · 동어반복 제외 {len(roll.twins)}" if roll.twins else ""))

    r = route_etf(roll, pv)
    out.append(say_route(r))
    out.append("")
    imps: list = []
    out.extend(_workflow(lake, roll, r, day, imps))
    honest = "\n".join(out)
    if ask is None:
        return honest
    return _dual(lake, roll, r, day, honest, ask, split, imps)


def _no_apply_reason(r) -> str:
    """성립했으나 `applies_today` 가 아닌 사유. `EdgeReport.applies_today` 의
    조건과 거울이다 - 여기가 갈리면 산문이 판정과 다른 사유를 말한다."""
    if not r.assignable:
        return "몫 배정 비지원"
    if r.null_kind == "date":
        return "날짜 귀무 - 귀속 자격 없음"
    if not r.cond_measurable:
        return "조건 오늘 측정불가 (결측은 충족이 아니다)"
    if r.cond_satisfied is False:
        return "오늘 조건 미충족"
    if r.reduction.startswith("불일치"):
        return f"환원 검사 {r.reduction}"
    if r.trigger_fired is False:
        return r.trigger_note or "오늘 방아쇠 미발화"
    return "오늘 적용 요건 미충족"


# EdgeVerdict(한글) → hypothesis_trial.verdict(영문 코드). 저장은 영문 코드 원칙 —
# 산문·버퍼는 한글 원값을 유지하고, 원장만 코드로 적는다(ALPHA-881).
_VERDICT_CODE = {"성립": "ESTABLISHED", "불성립": "NOT_ESTABLISHED",
                 "판정불가": "UNDECIDABLE"}


def _window_paneltest(lake, instrument_id: str, day: str, ask, facts,
                      news_out: list[dict] | None = None,
                      ) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    """요청창 사건·발화 계열 → LLM 가설 제안 → 코드 검정 → **레코드 버퍼**.

    반환은 (stat_tests, hypothesis_trials) 두 버퍼다. 앞은 근거 포맷 입력
    (stage_results, 한글 원값), 뒤는 가설 원장(hypothesis_trial) 행 재료다
    (ALPHA-881, 영문 코드) — 기각된 제안도 사유와 함께 행이 된다. 기각이
    침묵되면 "무엇이 제안됐고 왜 죽었나"가 사라진다(Rule 12).

    계약 세 줄:
      1. LLM 은 **제안만** 한다 - 판정은 `edge_gate`·`applies_today`(코드)다.
         LLM 출력이 검정을 우회해 어디로도 직행하지 않는다.
      2. 산출은 **고객 산문에 실리지 않는다**(ALPHA-876, 근거 명세 v3) - 전 판정
         (성립·불성립·판정불가·적용불가)을 영문 코드 아닌 슬롯 원값 그대로
         stage_results 버퍼에 남긴다. 숨기는 게 아니라 축을 옮긴 것이다(Rule 12):
         근거 포맷(통계검정 레코드 §3)이 이 버퍼를 입력으로 렌더한다.
      3. 방아쇠 재료(창 안 사건 타입 또는 오늘 발화 계열족)가 없으면 LLM 을
         부르지 않는다 - 재료 없는 가설은 소원이고, 호출 자체가 비용이다.
    """
    from ..observability import log
    from .hypothesize import propose
    from .interval import _etypes, thread_context
    from .model_contract import ModelContractError
    from .paneltest import FEATURES, Z_ANOM, edge_tests, series_z
    from .trial import prev_trading_day

    try:
        ets = _etypes(lake, list(facts.event_ids))
    except Exception as e:                      # noqa: BLE001 - 부재는 빈 목록
        # 침묵 폴백이 방아쇠 판정을 흔든다(실행마다 결과가 다른데 이유가 안 남는다) -
        # 빈 목록 폴백은 유지하되 사유는 로그로 드러낸다.
        log("hypothesis.etypes_failed", error=f"{type(e).__name__}: {str(e)[:80]}")
        ets = []
    try:
        fired = sorted(f for f, z in series_z(lake, instrument_id, day).items()
                       if abs(z) >= Z_ANOM)
    except Exception as e:                      # noqa: BLE001 - 부재는 빈 목록
        log("hypothesis.series_z_failed", error=f"{type(e).__name__}: {str(e)[:80]}")
        fired = []
    object_runtime = None
    object_tools = None
    scoped_context: tuple[str, ...] = ()
    scoped_count = 0
    structured_ready = False
    try:
        from .objectset_tools import NewsScope, ObjectSetRuntime
        object_runtime = ObjectSetRuntime(
            lake, as_of=f"{day}T{facts.window_end}:00",
            news_scope=NewsScope(instrument_id, prev_trading_day(lake, day)))
        threads = object_runtime.call("news.find_threads", {"limit": 40})
        if not threads.get("ok"):
            code = str(threads.get("error", {}).get("code") or "EXECUTION_FAILED")
            log("hypothesis.objectset_unavailable", tool="news.find_threads", code=code)
            return ({"stage": "propose", "verdict": "판정불가",
                     "reason": "OBJECTSET_UNAVAILABLE", "error_type": code},), ()
        events = object_runtime.call(
            "news.list_events", {"handle": threads["handle"], "limit": 40})
        if not events.get("ok"):
            code = str(events.get("error", {}).get("code") or "EXECUTION_FAILED")
            log("hypothesis.objectset_unavailable", tool="news.list_events", code=code)
            return ({"stage": "propose", "verdict": "판정불가",
                     "reason": "OBJECTSET_UNAVAILABLE", "error_type": code},), ()
        from .hypothesis_preview import HypothesisPreviewRuntime
        preview_runtime = HypothesisPreviewRuntime(lake, object_runtime, day=day)
        object_tools = {
            "specs": [*object_runtime.tool_specs(), *preview_runtime.tool_specs()],
            "call": preview_runtime.call,
            "resolve_preview": preview_runtime.resolve,
        }
        rows = events.get("events", [])
        thread_by_event: dict[str, str] = {}
        for thread in threads.get("threads", []):
            thread_id = str(thread.get("thread_id") or "")
            if not thread_id:
                continue
            selected_thread = object_runtime.call(
                "news.get_thread", {"thread_id": thread_id})
            if not selected_thread.get("ok"):
                code = str(selected_thread.get("error", {}).get("code")
                           or "EXECUTION_FAILED")
                log("hypothesis.objectset_unavailable", tool="news.get_thread", code=code)
                return ({"stage": "propose", "verdict": "판정불가",
                         "reason": "OBJECTSET_UNAVAILABLE", "error_type": code},), ()
            thread_events = object_runtime.call(
                "news.list_events", {"handle": selected_thread["handle"], "limit": 40})
            if not thread_events.get("ok"):
                code = str(thread_events.get("error", {}).get("code")
                           or "EXECUTION_FAILED")
                log("hypothesis.objectset_unavailable", tool="news.list_events", code=code)
                return ({"stage": "propose", "verdict": "판정불가",
                         "reason": "OBJECTSET_UNAVAILABLE", "error_type": code},), ()
            for thread_event in thread_events.get("events", []):
                thread_by_event[str(thread_event["source_event_id"])] = thread_id
        structured_ready = True
        scoped_count = len(rows)
        ets = sorted(set(ets) | {
            str(row["event_type_code"]) for row in rows
            if row.get("event_type_code")})
        scoped_context = tuple(
            f'{row.get("available_at", "")} {row.get("event_type_code", "")} '
            f'{row.get("source_event_id", "")}' for row in rows)
        discovered: list[dict] = []
        for row in rows:
            narrowed = object_runtime.call("objectset.filter", {
                "handle": events["handle"], "field": "source_event_id",
                "operator": "eq", "value": row["source_event_id"],
            })
            if not narrowed.get("ok"):
                code = str(narrowed.get("error", {}).get("code") or "EXECUTION_FAILED")
                log("hypothesis.objectset_unavailable", tool="objectset.filter", code=code)
                return ({"stage": "propose", "verdict": "판정불가",
                         "reason": "OBJECTSET_UNAVAILABLE", "error_type": code},), ()
            grounded = object_runtime.call(
                "news.get_event_evidence", {"handle": narrowed["handle"], "limit": 20})
            if not grounded.get("ok"):
                code = str(grounded.get("error", {}).get("code") or "EXECUTION_FAILED")
                log("hypothesis.objectset_unavailable",
                    tool="news.get_event_evidence", code=code)
                return ({"stage": "propose", "verdict": "판정불가",
                         "reason": "OBJECTSET_UNAVAILABLE", "error_type": code},), ()
            evidence = [item for item in grounded.get("evidence", [])
                        if item.get("evidence_type") in {"TITLE", "SUMMARY"}
                        and str(item.get("evidence_text") or "").strip()]
            if not evidence:
                log("hypothesis.objectset_unavailable",
                    tool="news.get_event_evidence", code="EVIDENCE_UNAVAILABLE")
                return ({"stage": "propose", "verdict": "판정불가",
                         "reason": "OBJECTSET_UNAVAILABLE",
                         "error_type": "EVIDENCE_UNAVAILABLE"},), ()
            chosen = next((item for item in evidence
                           if item.get("evidence_type") == "TITLE"), evidence[0])
            title = str(chosen["evidence_text"]).strip()
            available_at = str(row.get("available_at") or "")
            clock = available_at[11:16] if len(available_at) >= 16 else available_at
            discovered.append({
                "source_event_id": str(row["source_event_id"]),
                "event_type_code": str(row.get("event_type_code") or ""),
                "title": title,
                "available_at": available_at,
                "thread_id": thread_by_event.get(str(row["source_event_id"])),
                "evidence_id": str(chosen.get("evidence_id") or "") or None,
                "line": f"{clock}, {title}" if clock else title,
            })
        if news_out is not None:
            news_out.extend(discovered)
    except Exception as e:                      # noqa: BLE001 - structured abstention
        log("hypothesis.objectset_unavailable", error=f"{type(e).__name__}: {str(e)[:80]}")
        return ({"stage": "propose", "verdict": "판정불가",
                 "reason": "OBJECTSET_UNAVAILABLE", "error_type": type(e).__name__},), ()
    if ask is None or (not ets and not fired):
        return (), ()

    # 제안 접지(ALPHA-885): 직전 거래일부터 요청창 끝까지의 스레드 문맥. 검정의
    # 점 방아쇠 접지(`ets`, 창 안)는 그대로다 - 문맥은 제안에만 실린다.
    if structured_ready:
        context, n_ctx, n_fail = scoped_context, scoped_count, 0
    else:
        context, n_ctx, n_fail = thread_context(
            lake, instrument_id, day, facts.window_end, facts.event_ids)
    log("hypothesis.context", events=n_ctx, lookup_failures=n_fail,
        in_window=len(facts.event_ids))

    brief = (f"[셀] {facts.ticker} {facts.name} · {day} "
             f"요청창 {facts.window_start}~{facts.window_end} "
             f"수익 {facts.window_return * 100:+.2f}%\n"
             f"창 안 사건 타입: {ets or '없음'} · 오늘 발화 계열족: {fired or '없음'}")
    if getattr(facts, "beta_path", ""):  # 시변 β 경로 요약(ALPHA-803) - facts 가 정본
        brief += "\n" + facts.beta_path
    if context:
        brief += (f"\n\n[사건 문맥 - 직전 거래일부터 요청창 끝까지 · "
                  f"as_of={day} {facts.window_end}]\n" + "\n\n".join(context))
    try:
        tuples, rejected = propose(ask, facts=brief, event_types=ets,
                                   measurable=sorted(FEATURES),
                                   series_families=fired, object_tools=object_tools)
    except ModelContractError:
        raise
    except Exception as e:                      # noqa: BLE001 - 실패는 사유와 함께
        return ({"stage": "propose", "verdict": "제안실패",
                 "reason": f"{type(e).__name__}: {str(e)[:80]}"},), ()
    # 기각 제안은 사유째로 원장 행이 된다 — 유효 튜플이 몇 개든(0개 포함) 남긴다.
    trials: list[dict] = [
        {"stage": "REJECTED", "verdict": "REJECTED", "reason": why}
        for why in rejected]
    if not tuples:
        return ({"stage": "propose", "verdict": "가설없음",
                 "reason": (f"제안 전부 거부됨 ({len(rejected)}건)" if rejected
                            else "가설이 제안되지 않았다")},), tuple(trials)

    from dataclasses import asdict
    records: list[dict] = []
    for t, r in edge_tests(lake, tuples, day, cell_instrument_id=instrument_id):
        reason = (r.reason if r.verdict not in ("성립", "불성립")
                  else ("" if r.applies_today or r.verdict == "불성립"
                        else _no_apply_reason(r)))
        records.append({
            "stage": "test",
            "trigger": t.trigger.ident,
            # 근거 포맷 §5 게이트 재료(ALPHA-888): 계열 방아쇠는 발화를 명시
            # 확인해야 한다 — applies_today 는 trigger_fired=None(미계측)을
            # `is not False` 로 통과시키므로, 유도기가 원값을 직접 본다.
            "trigger_kind": t.trigger.kind,
            "trigger_fired": r.trigger_fired,
            "null_kind": r.null_kind,
            "channel": t.channel,
            "exposure": f"{t.exposure.ident}/{t.exposure.transform}",
            "layer": t.layer,
            "verdict": r.verdict,
            "applies_today": bool(r.applies_today),
            "n": r.n, "p": r.p,
            "effect_low": r.effect_low, "effect_high": r.effect_high,
            "reason": reason,
        })
        trials.append({
            "stage": "TESTED",
            "verdict": _VERDICT_CODE[r.verdict],
            "trigger_slot": f"{t.trigger.kind}:{t.trigger.ident}",
            "channel": t.channel,
            "exposure": f"{t.exposure.ident}/{t.exposure.transform}",
            "layer": t.layer,
            "conditions": [asdict(v) for v in t.conditions],
            "applies_today": bool(r.applies_today),
            "n": r.n, "p": r.p,
            "effect_low": r.effect_low, "effect_high": r.effect_high,
            "reason": reason,
        })
    return tuple(records), tuple(trials)


def _dual(lake, roll, r, day: str, honest: str, ask, split=None,
          imps: list | None = None) -> str:
    """정직한 설명 + 토스식. ETF 는 5분봉이 없어 창이 없다 - **그 사실을 정직하게**
    다루고, '최근 시점' 대신 시장 사건의 시각(있으면)이나 밤사이를 쓴다."""
    from .mkttrial import event_days
    from .evidence import say_save
    from .plain import PlainError, context, dual, narrate_plain
    from .trial import reduce_market
    # 이름만 넘기면 모델이 방향을 지어낸다 - 실측에서 VIX 가 -17% 인데 'VIX 강세'
    # 라고 썼다. **부호를 지우면 서사가 거짓이 된다.** 수치는 못 주므로 방향을 말로
    # 준다. VIX 는 오르면 불안이라 강세/약세로 부르면 뜻이 뒤집힌다 - 따로 옮긴다.
    over: list[str] = []
    mr: dict = {}
    try:
        mr = reduce_market(lake, day, symbol=f"{roll.etf}.KS")
        for nm, v in (mr.get("overnight") or ()):
            f = float(v)
            if abs(f) <= 0.01:
                continue
            name = str(nm)
            if "VIX" in name.upper() or "변동성" in name:
                over.append("미국 시장의 불안이 " + ("커졌어요" if f > 0 else "줄었어요"))
            else:
                over.append(f"{name}가 " + ("올랐어요" if f > 0 else "내렸어요"))
            if len(over) >= 3:
                break
    except Exception:                       # noqa: BLE001 - 부재는 빈 목록
        over = []
    # 시장 사건 검정의 **무유의도 사실이다**: '특별한 뉴스 때문이 아니다' 는
    # 사람이 가장 궁금해하는 답 중 하나다(급변인데 뉴스가 없을 때).
    from .mkttrial import say_screen, screen_market
    facts: list[str] = []
    scr: list = []
    try:
        scr = screen_market(lake, day)
        if scr and "유의한 시장 사건 없음" in say_screen(scr):
            facts.append("특별한 국내 소식 때문은 아님")
        elif any(d == day for d in event_days(lake, day)):
            facts.append("시장 전체에 영향을 준 소식")
    except Exception:                       # noqa: BLE001 - 부재는 빈 목록
        pass
    # **밤사이 환원이 곧 이유다.** 이것을 `established` 에 안 넣으면 산문이
    # '밤사이 올랐어요' 라 말한 뒤 '이유는 안 보여요' 라고 자기모순을 낸다(실측).
    if over:
        facts.insert(0, "밤사이 해외에서 " + over[0])
    ctx = context(
        ticker_name=roll.etf_name, day_log=roll.total,
        idio_log=roll.idio, route_kind=r.kind,
        market_name=r.layer_name or "코스피 대형주",
        # ETF 는 창이 없다(5분봉 부재) - 밤사이가 하루의 시작이므로 그것을 시점으로.
        recent={"when": "밤사이", "events": facts[:1]},
        established=facts, overnight=over,
        # ρ 게이트는 층 회계에서 빠졌다(ALPHA-862) - 고유 자격 판정은 칼만이 가져온다.
        unexplained_top=False)
    # 통계 재료. **수치는 재료에만** 있고 산문에는 못 들어간다(코드가 막는다).
    # 밤사이 환원도 통계다(β 구간 × 팩터 수익) - 이것을 참조 가능한 근거로 안 주면
    # '밤사이 미국 반도체가 올라서' 라는 주장이 접지 실패로 즉사한다(실측 2회).
    from .evidence import _plain_num
    from .gates import ALPHA
    stats: list[dict] = []
    # 밤사이 해외 지수 움직임은 **관측치**다 - 추론이 아니므로 구간도 p 도 없다.
    # 그런데 ref 를 안 주면 '밤사이 불안이 줄었어요' 라는 사실 서술이 '근거 없이
    # 단언' 으로 즉사한다(실측 2회). 관측도 근거다 - ref 를 준다.
    for nm in over:
        stats.append({"ref": f"s{len(stats) + 1}", "kind": "밤사이 해외 실측",
                      "무엇": nm, "note": ""})
    if mr and mr.get("factor_name"):
        stats.append(_plain_num({
            "ref": f"s{len(stats) + 1}", "kind": "밤사이 환원", "factor": mr["factor_name"],
            "factor_ret": mr.get("gap_factor_ret"),
            "beta_ci": list(mr.get("gap_beta") or ()),
            "explained": mr.get("mkt_explained"),
            "note": mr.get("gap_reason", "")[:60]}))
    # 5분 괴리 분해가 있으면 **하루의 두 몫**을 재료로 준다. 이것이 '바스켓이 올라서'
    # 와 'ETF 값만 따로 올라서(수급)' 를 낱말이 아니라 수치로 갈라 준다.
    if split is not None:
        top = max(split.wins, key=lambda w: abs(w.d_prem))
        stats.append(_plain_num({
            "ref": f"s{len(stats) + 1}", "kind": "5분 괴리 분해",
            "하루": round(split.total, 5), "바스켓몫": round(split.basket, 5),
            "괴리변화몫": round(split.premium_move, 5),
            "괴리_시작": round(split.prem_open, 5), "괴리_끝": round(split.prem_last, 5),
            "괴리_최대창": str(top.ts)[11:16], "그_창_괴리몫": round(top.d_prem, 5),
            "창수": len(split.wins),
            "판정": ("바스켓이 끌었다" if abs(split.basket) >= abs(split.premium_move)
                   else "ETF 값만 따로 움직였다(수급)")}))
    # 성립한 함의의 **조절 조건**을 재료로 준다. 이것이 '이 사건이 이런 종목에서 더
    # 크게 나타났어요' 를 가능하게 한다 - 없으면 쉬운 설명은 평균 효과만 말하고,
    # 정직한 설명에만 조절이 찍혀 두 산출물이 다른 것을 말한다.
    for x in (imps or []):
        for nm, coef, pi_, pj in getattr(x, "mods", ()):
            stats.append(_plain_num({
                "ref": f"s{len(stats) + 1}", "kind": "조절 조건",
                "사건": x.claim.split()[0] if x.claim else "",
                "조건": nm, "방향말": "높을수록 더 크게" if coef > 0 else "높을수록 덜",
                "조절계수": round(float(coef), 5),
                "안정성": round(float(pi_), 3), "p": round(float(pj), 4),
                "판정": "조절 성립 (원인 아님 - 조절이다)"}))

    # **판정 등급을 코드가 실어 준다.** 수치만 주면 모델이 p 를 제 맘대로 읽는다 -
    # 실측: p=0.232 · ATT -2.5%p 를 '큰 영향을 주지 않았어요' 로 단정했다.
    stats += [_plain_num({
        "ref": f"s{len(stats) + i}", "kind": "시장사건 시행", "etype": x["etype"],
        "att": round(float(x["att"]), 5), "p": round(float(x["p"]), 4),
        "n_days": x["n_days"], "unit": "거래일",
        "판정": "유의" if x["p"] < ALPHA else "못 가름(무유의 - 영향 없음이 아니다)"})
        for i, x in enumerate(
            (z for z in (scr or []) if z.get("verdict") == "계산됨"), 1)]
    try:
        # **구성종목 뉴스는 항상 재료로 준다.** `narrative_allowed` 는 '서사로 하루를
        # 설명해도 되나' 를 묻는 게이트이고, '어떤 소식이 있었나' 는 관측이다 - 둘을
        # 같은 스위치로 묶으면 통계가 서는 날에는 뉴스를 영원히 못 말한다.
        news = member_news(lake, roll, day)
        plain, bundles = narrate_plain(ask, ctx, news=news, stats=stats, cell=roll.etf,
                                      day=day, layer=r.kind)
        # 묶음은 **만든 그 자리에서** 적재한다. 꼬리표 id 만 산출물로 나가고 본문이
        # 아무 데도 없으면, 나중에 그 문장이 무엇에 근거했는지 되짚을 방법이 없다.
        if line := say_save(bundles):
            plain += f"\n{line}"
        return dual(honest, plain, bundles)
    except PlainError as e:
        return dual(honest, f"(쉬운 설명 생성 실패 - 계약 위반: {e})")


def _observed_types(lake, ticker: str, day: str) -> list[str]:
    """그날 그 종목에 **접지된** 사건 타입. 부재는 빈 목록이다 - 지어내지 않는다.

    하드코딩된 타입으로 검정을 세우면 그 타입이 없는 날에도 "처치일 부족" 만 반복하고,
    실제로 난 사건은 아무도 안 본다(실측: 종목 경로가 `RESULT_RELEASE` 만 물었다).
    """
    try:
        from .paneltest import _base
        rows = lake.sql(
            _base(day) +
            f"\nSELECT DISTINCT e.event_type_code FROM v_event e "
            f"JOIN v_instrument i ON i.instrument_id = e.instrument_id "
            f"WHERE i.ticker = '{ticker.split('.')[0]}' "
            f"  AND e.trade_date = DATE '{day}' ORDER BY 1")
    except Exception:                               # noqa: BLE001 - 부재는 빈 목록
        return []
    return [str(r[0]) for r in rows if r and r[0]]


def _sector_types(lake, day: str, top: int = 3) -> list[str]:
    """그날 시장 전체에서 관측된 **광역 타입** 상위 - 섹터 공통 처치 후보.

    섹터 처치는 특정 종목의 사건이 아니라 여러 종목에 동시에 닿은 타입이다. 그래서
    '몇 종목에 닿았나' 로 고른다 - 하드코딩 2종은 그날 무엇이 났는지와 무관했다.
    """
    try:
        from .paneltest import _base
        rows = lake.sql(
            _base(day) +
            f"\nSELECT e.event_type_code, count(DISTINCT e.instrument_id) n "
            f"FROM v_event e WHERE e.trade_date = DATE '{day}' "
            f"GROUP BY 1 HAVING count(DISTINCT e.instrument_id) >= 2 "
            f"ORDER BY 2 DESC, 1 LIMIT {top}")
    except Exception:                               # noqa: BLE001
        return []
    return [str(r[0]) for r in rows if r and r[0]]


def member_news(lake, roll, day: str, top: int = 4, per: int = 4) -> list[dict]:
    """**구성종목**의 뉴스. ETF 자신에는 사건이 안 붙는다 - 트리거가 없다.

    이것이 없어서 ETF 산문이 '어떤 뉴스 때문인지' 를 영원히 말할 수 없었다:
    `news_objectset(lake, <ETF id>, day)` 는 항상 `[]` 다(사건은 종목의 것이다).
    기여 절댓값 상위 종목의 뉴스를 모아 **그 종목 이름과 함께** 준다 - 어느 종목의
    소식인지 말하지 않으면 ETF 설명에서 뜻이 없다.

    **뉴스는 관측이다.** 여기 있다는 것이 원인이라는 뜻이 아니다 - 그래서 각 항목에
    `판정: 관측 - 방향 미검정` 을 달아 보낸다(`plain._sign_guard` 가 방향 주장을 막는다).
    """
    from .evidence import news_objectset
    from .paneltest import _base
    names = sorted((n for n in roll.names if n.contribution is not None),
                   key=lambda n: -abs(n.contribution))[:top]
    if not names:
        return []
    want = {n.ticker.split(".")[0]: n for n in names}
    try:
        # `v_instrument` 는 **독립 뷰가 아니라 `_base(day)` 가 만드는 CTE** 다.
        # 이걸 빼먹어 질의가 `CatalogException` 으로 죽고 `except: return []` 가
        # 조용히 삼켰다 - 30일 배치에서 뉴스 인용이 0/30 이었던 이유다. 부재를 빈
        # 목록으로 돌려주는 except 절이 원인을 통째로 감췄다.
        rows = lake.sql(_base(day) + "SELECT ticker, instrument_id FROM v_instrument "
                        "WHERE ticker IN (" + ",".join(repr(t) for t in want) + ")")
    except Exception as exc:                        # noqa: BLE001 - 사유를 남긴다
        return [{"ref": "!뉴스오류", "title": f"{type(exc).__name__}: {str(exc)[:80]}",
                 "종목": "", "판정": "조회 실패 - 부재가 아니다"}]
    out: list[dict] = []
    for tk, iid in rows:
        nm = want.get(str(tk))
        for o in news_objectset(lake, str(iid), day, limit=per):
            if str(o.get("ref", "")).startswith("!"):
                continue                            # 조회 오류 항목은 재료가 아니다
            out.append({**o, "ref": f"n{len(out) + 1}",
                        "종목": (nm.label or str(tk)) if nm else str(tk),
                        "판정": "관측 - 방향 미검정"})
    return out


def _workflow(lake, roll, r, day: str,
              sink: list | None = None) -> list[str]:
    """분해에서 실질적인 시장·섹터·고유층을 각각 실행한다.

    `Route.kind`는 대표 제목일 뿐 다른 층을 막지 않는다. `sink`에는 성립한 함의를
    모아 쉬운 설명 재료로 넘긴다.
    """
    from .interval import FLOOR
    from .layers import MARKET_CODE
    from .route import MIN_NAME_SHARE, TOP_NAMES
    from .trial import reduce_market, say_market

    out: list[str] = []
    if r.kind == "괴리단독":
        out.append("[워크플로우] ETF 고유 — 구성종목 인과를 세우지 않는다. 수급·유동성"
                   " 축만 유효하고, 그 축은 되돌림 후보다.")
        return out

    layers = tuple(getattr(roll, "layers", ()))
    total = sum(abs(x.contribution) for x in layers) + abs(roll.idio)

    def material(value: float) -> bool:
        return total > 0 and abs(value) / total >= FLOOR

    is_market_proxy = str(getattr(roll, "etf", "")).endswith(MARKET_CODE)
    market_on = is_market_proxy or any(
        x.kind == "시장" and material(x.contribution) for x in layers)
    sectors = (() if is_market_proxy else tuple(
        x for x in layers if x.kind == "섹터" and material(x.contribution)))
    idio_on = not is_market_proxy and material(roll.idio)
    name_floor = MIN_NAME_SHARE * abs(roll.idio)
    targets = (() if not idio_on else tuple(
        n.ticker for n in roll.names if abs(n.contribution) >= name_floor)[:TOP_NAMES])

    if market_on:
        out.append("[워크플로우] 시장 환원 — 종목 가설을 세우지 않는다")
        out.append(say_market(reduce_market(lake, day, symbol=f"{roll.etf}.KS")))
        from .mkttrial import say_screen, screen_market
        out.append(say_screen(screen_market(lake, day)))

    from .verifier import say_implications, verify
    for sec in sectors:
        out.append(f"[워크플로우] 섹터 공통 처치 — {sec.name} "
                   f"(층 수익 {sec.ret * 100:+.2f}% · 시장 차감 "
                   f"{sec.contribution * 100:+.2f}%p)")
        ets = _sector_types(lake, day)
        if not ets:
            out.append("  그날 2종목 이상에 닿은 타입이 없다 - 섹터 공통 처치를 "
                       "세울 수 없다 (그것도 결과다)")
        for et in ets:
            imps, lg = verify(lake, day, etype=et, layer="섹터")
            if sink is not None:
                sink += [x for x in imps if getattr(x, "credible", True)]
            out.append("  " + lg.replace("\n", "\n  "))
            out.append("  " + say_implications(imps).replace("\n", "\n  "))

    if targets:
        out.append(f"[워크플로우] 종목 개별 시행 — 상위 {len(targets)}종목")
        by_type: dict[str, list[str]] = {}
        for tk in targets:
            nm = next((n for n in roll.names if n.ticker == tk), None)
            ets = _observed_types(lake, tk, day)
            out.append(f"  {tk}" + (f" ({nm.label})" if nm and nm.label else "")
                       + (f" 기여 {nm.contribution * 100:+.2f}%p · 비중 {nm.weight:.1%}"
                          if nm else "")
                       + ("  사건: " + " · ".join(ets) if ets else "  사건 없음"))
            for et in ets:
                by_type.setdefault(et, []).append(tk)
        if not by_type:
            out.append("  상위 종목에 그날 접지 사건이 없다 - 종목 시행을 세울 수 없다 "
                       "(그것도 결과다: 사건 없이 움직인 고유)")
        for et, tks in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            out.append(f"  [검정] {et} — 이 셀에서 {' · '.join(tks)} 가 해당")
            imps, lg = verify(lake, day, etype=et, layer="고유", max_probes=4)
            if sink is not None:
                sink += [x for x in imps if getattr(x, "credible", True)]
            out.append("    " + lg.replace("\n", "\n    "))
            out.append("    " + say_implications(imps).replace("\n", "\n    "))
    elif idio_on:
        out.append("[워크플로우] 고유 몫은 크지만 |기여| 상위 종목이 임계 미달 — "
                   "귀속할 이름이 없다. 이것도 결과다 (분산된 고유)")
    return out
