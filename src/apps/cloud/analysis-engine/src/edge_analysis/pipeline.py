"""run 오케스트레이션 — 트리거 소비 → 분해 → **통계 인과귀속** → 영속.

의존성(lake·store·client·s3)을 주입해 제어 흐름을 I/O 없이 테스트할 수 있다. 엔진은
파이프라인이 만든 feature 산출물만 읽는다(ADR-0028) — 트리거 행이 없으면 그날은
'정상 변동'이 일급 답이다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, replace
from typing import Any

from .adapters.archive import (
    archived_events,
    decomp_summary,
    write_explanation_to_s3,
    write_run_archive,
)
from .adapters.eventstore import EventStore
from .adapters.lake import LakeReader
from .adapters.llm import AnalysisClient, TracingClient
from .adapters.trace import write_agent_trace
from .config import KST, PipelineError, ReturnsNotReadyError, Settings
from .domain.decomposition import compute_decomposition
from .domain.models import EventContext
from .observability import collect_trace, log

_MARKET = "KR"


def run(
    settings: Settings,
    *,
    lake: LakeReader,
    store: EventStore,
    client: AnalysisClient,
    s3,
    causal_lake=None,
) -> int:
    """당일 파이프라인을 실행하고 종료 코드(성공=0)를 반환한다."""
    # 분봉 트리거 단건 입력(ALPHA-709) — 트리거 행이 대상·날짜의 정본이다. env 기본값
    # (ETF·오늘)으로 다른 대상을 분석하면 계보가 조용히 오염된다(ALPHA-467 과 같은 축).
    minute_row = None
    minute_gate = None
    if settings.trigger_id:
        minute_row = store.fetch_minute_price_trigger(settings.trigger_id)
        if minute_row is None:
            raise PipelineError(f"분봉 트리거가 없다: {settings.trigger_id}")
        minute_gate = minute_row.gate
        settings = replace(
            settings, etf_ticker=minute_row.ticker, trade_date=minute_row.trade_date
        )
    log("start", trade_date=settings.trade_date.isoformat(), request_id=settings.request_id,
        trigger_id=settings.trigger_id)

    entity_index = store.load_entity_index()
    resolved = store.resolve_etf_instrument(settings.etf_ticker)
    if resolved is None:
        # 폴백으로 남의 instrument_id 에 붙이면 계보가 조용히 오염된다 — 대상 ETF 가
        # 마스터에 없으면 그 런은 성립하지 않으므로 비0 종료로 드러낸다(Rule 12, ALPHA-467).
        raise PipelineError(
            f"instrument 마스터에 ETF ticker={settings.etf_ticker} 없음"
            " — 마스터 적재(load-instruments) 여부 확인")
    etf_instrument_id, etf_name = resolved

    # 레이크에서 가격을 소비해 ETF 등락을 분해한다(L1).
    holdings, holdings_asof = lake.load_holdings(settings.etf_ticker, _MARKET, settings.trade_date)
    if not holdings:
        # holdings 가 비면(파티션 결손·정리 등) 분해가 불가하다 — proxy_ret None·구성종목 0·
        # 뉴스 0 인 packet 을 LLM 에 보내면 입력 결손이 정상 분석으로 위장된다(Rule 12).
        raise PipelineError(
            f"canonical holdings 가 비었다: etf={settings.etf_ticker}"
            f" trade_date={settings.trade_date.isoformat()} — 구성종목 없이 분해·설명 불가")
    # 구성종목 티커→종목명(뉴스 이벤트 표시용) — 이 ETF 의 holdings 에서만 파생한다(ALPHA-467).
    name_by_ticker = {h.ticker: h.name for h in holdings if h.name}
    if minute_row is not None:
        # 분봉 분해 입력(ALPHA-710) — 트리거 판정과 같은 축(세션 시가 대비)으로
        # 구성종목 장중 수익률을 파생한다. 기준 window 는 ETF 시가가 확정된 바로
        # 그 window(minute_session_open.source_window)라 두 축이 갈리지 않는다.
        open_ctx = store.fetch_minute_open_window(minute_row.session_id, settings.etf_ticker)
        if open_ctx is None:
            # 트리거가 발화했다면 시가는 확정돼 있어야 한다 — 부재는 원장 지연이거나
            # 결손이므로 빈 분해를 만들지 않고 재시도 축(소비자 가시성 연장)으로 접는다.
            raise ReturnsNotReadyError(
                f"세션 시가 원장이 없다: session={minute_row.session_id}"
                f" etf={settings.etf_ticker} — minute_session_open 미확정")
        open_window, open_generation, open_checksum = open_ctx
        # 트리거 window 의 세대·checksum 은 원장의 마지막 커밋 쌍이 정본이다 — 발화 후
        # 정정이 끼면 최신 커밋이 더 정확한 가격이고, checksum 은 그 세대의 바이트다.
        trigger_meta = store.fetch_minute_window_meta(
            minute_row.session_id, minute_row.window_start)
        if trigger_meta is None:
            raise ReturnsNotReadyError(
                f"트리거 window 원장이 없다: session={minute_row.session_id}"
                f" window={minute_row.window_start.isoformat()} — 커밋 미착지")
        trigger_generation, trigger_checksum = trigger_meta
        returns = lake.load_minute_returns(
            _MARKET, settings.trade_date.isoformat(),
            open_window.astimezone(KST).strftime("%H%M"), open_generation, open_checksum,
            minute_row.window_start.astimezone(KST).strftime("%H%M"),
            trigger_generation, trigger_checksum,
        )
        # 트리거 window 가 INCOMPLETE 면 발화 ETF 행만 있고 구성종목이 통째로 빠질 수
        # 있다 — dict 는 truthy 라 아래 빈 검사를 통과하고, total_priced=0 분해가 정상
        # 설명으로 영속된다(원결함의 부활 코너). 정정 세대가 낫게 하는 실패라 재시도.
        if returns and not any(returns.get(h.ticker) is not None for h in holdings):
            raise ReturnsNotReadyError(
                f"구성종목 가격이 0건이다: session={minute_row.session_id}"
                f" window={minute_row.window_start.isoformat()} — INCOMPLETE window"
                " 이거나 구성종목 미수집. 빈 분해를 설명으로 만들지 않는다")
        empty_reason = (
            f"분봉 canonical 수익률이 비었다: session={minute_row.session_id}"
            f" window={minute_row.window_start.isoformat()} — window artifact"
            " 미착지(커밋 지연)거나 결손이다. 빈 분해를 설명으로 만들지 않는다"
        )
    else:
        returns = lake.load_returns(_MARKET, settings.trade_date)
        # 당일 파티션이 없으면 `load_returns` 는 **가드 없이 {}** 를 돌려준다 — 그대로
        # 분해에 넣으면 전 종목 미가격 분해(etf_return=NULL·total_priced=0)가 LLM 까지
        # 가서 입력 결손이 정상 설명으로 위장된다(Rule 12, 08-03 정합성 감사 실측).
        empty_reason = (
            f"canonical price_daily 수익률이 비었다: market={_MARKET}"
            f" trade_date={settings.trade_date.isoformat()} — 15:40 배치 전이거나"
            " 파티션 결손이다. 빈 분해를 설명으로 만들지 않는다"
        )
    if not returns:
        raise ReturnsNotReadyError(empty_reason)
    decomp = compute_decomposition(holdings, returns)
    log(
        "price.decomposed",
        holdings_asof=holdings_asof,
        constituents=decomp.n_constituents,
        priced=decomp.total_priced,
        coverage=round(decomp.coverage, 4),
        proxy_ret=decomp.proxy_ret,
    )

    # L0 게이트는 계산이 아니라 소비다(ALPHA-411) — 행이 없으면 평온한 날.
    # 분봉 입력이면 게이트가 이미 손에 있다(발화했기에 호출됐다) — 일 단위 조회는
    # 그 트리거를 못 보므로(테이블이 다르다) 여기서 갈아끼운다.
    gate = minute_gate or store.fetch_price_trigger(etf_instrument_id, settings.trade_date)
    if gate is None:
        write_run_archive(s3, settings, {
            "outcome": "normal_variation",
            "trigger": None,
            "decomposition": decomp_summary(decomp),
            "holdings_asof": holdings_asof,
        })
        log("done", reason="normal_variation", observed_return=decomp.proxy_ret)
        return 0

    # ── 라우팅은 **층 회계**가 정한다 (ALPHA-671) ─────────────────────────────
    # 레거시 `decide_route` 는 구성종목 집중도만 봤다. 그런데 하루를 끈 것이 시장인지
    # 섹터인지 그 종목인지는 **층 분해**가 답한다 - 실측(042700 07-31): 하루의 77%가
    # 시장인데 종목 가설 9간선을 세워 전부 죽었다. 원장의 route_code 가 실제로 물은
    # 질문과 달라지면 계보가 거짓말을 한다.
    #
    # `event_search` 를 호출자가 정하게 두지 않는다 - 원장 CHECK 가 route_code 와
    # 묶으므로 코드에서 파생한다(`route_code_of`).
    from .statics.duck import CausalLake
    from .statics.layers import decompose as layer_decompose
    from .statics.record import route_code_of
    from .statics.route import route_etf

    lake = causal_lake if causal_lake is not None else CausalLake()
    day_iso = settings.trade_date.isoformat()
    roll = rt = None
    try:
        roll = layer_decompose(lake, settings.etf_ticker, day_iso)
        rt = route_etf(roll)
    except Exception as exc:                # noqa: BLE001 - 표면 부재를 사유로 남긴다
        log("statics.layers.failed", error=f"{type(exc).__name__}: {exc}")
    route_code, event_search = route_code_of(rt.kind if rt else "")
    ids = store.persist_observation_route(
        gate.trigger_id, decomp, route_code, event_search, entity_index,
        minute=minute_gate is not None,
    )
    log("trigger.consumed", route=route_code, event_search=event_search,
        layer_route=(rt.kind if rt else "미상"), **ids)

    # 파이프라인이 조립한 이벤트를 소비만 한다(ALPHA-412, 읽기 전용).
    events: list[EventContext] = []
    if event_search:
        # 뉴스 대상 티커는 이 ETF 의 holdings 구성종목이다 — 구 KODEX_CONSTITUENTS
        # 9종목 하드코딩은 다른 ETF 로 돌려도 KODEX 뉴스만 읽었다(ALPHA-467).
        events = store.fetch_event_contexts(
            settings.trade_date, [h.ticker for h in holdings])
        log("events.ready", events=len(events))

    # ── 통계 인과귀속 (ALPHA-671) ─────────────────────────────────────────────
    # P0–P9 자유서술 인과 경로를 걷어냈다. 그것은 LLM 이 판정까지 하는 구조였고,
    # 8셀 실측에서 모델 판정이 코드 결론을 뒤집어 무엇이 판정인지 흐렸다.
    # 지금은 **설계는 모델, 판정은 코드**다:
    #   크기  창 분해 (시간 항등식 - 몫의 합 = 하루)
    #   층    시장·섹터·고유 회계 + 고유 자격 검사(잔차 공통상관)
    #   라우팅 어느 층에 물을지 - 시장 지배면 종목 가설을 세우지 않는다
    #   인과  타입 수준 패널 · 매칭 위약 · 순열 p · 시장사건은 거래일 단위
    #   설명  정직한 전문(summary) + 쉬운 한 줄(headline) + 주장별 근거 묶음
    #
    # 레이크는 DuckDB 표면(`CausalLake`)이다 - S3 canonical 과 RDB 를 한 질의에서
    # 조인한다. store 커넥션과 시점이 갈리지 않게 같은 거래일을 쓴다.
    from .statics.etfcell import run as run_statics
    from .statics.record import as_explanation, verdicts_from

    # 레이크는 **주입**한다 - 이 파일의 계약이 그것이다(도크스트링 첫 문장).
    # 안에서 만들면 제어 흐름을 I/O 없이 못 돌리고, 실제로 그렇게 했다가 파이프라인
    # 테스트 7건이 `layers_daily does not exist` 로 죽었다.
    ask = TracingClient(client).complete_json
    text = ""
    with collect_trace() as trace:
        try:
            text = run_statics(lake, settings.etf_ticker, day_iso, ask)
        except Exception as exc:            # noqa: BLE001 - 표면 부재를 사유로 남긴다
            # 레이크가 못 서면 **설명이 없다고 말한다** - 빈 산문을 정상 분석으로
            # 위장하지 않는다(Rule 12). 계보는 그래도 남아 재실행 대상이 된다.
            text = (f"[ETF] {settings.etf_ticker} {day_iso} 통계 표면 부재 — "
                    f"{type(exc).__name__}: {str(exc)[:160]}")
            log("statics.surface.unavailable", error=f"{type(exc).__name__}: {exc}")
    write_agent_trace(s3, settings, trace)

    honest, _, plain = text.partition("[쉬운 설명] 수치 없이 - 방금 왜 움직였나")
    headline = plain.strip().lstrip("=").strip()
    # 층·라우팅은 위에서 이미 냈다 - 다시 분해하지 않는다(같은 셀에 두 답 금지)
    stage = {"route": (rt.kind if rt else ""), "layers": [
        {"kind": x.kind, "name": x.name, "contribution": x.contribution}
        for x in (roll.layers if roll else ())],
        "idio": (roll.idio if roll else None), "rho": (roll.rho if roll else None)}
    verdicts = verdicts_from(
        text, route_kind=(rt.kind if rt else ""),
        idio_qualified=bool(roll is None or roll.rho is None or abs(roll.rho) < 0.20),
        bundles=tuple(sorted(set(re.findall(r"\bev_[0-9a-f]{16}\b", text)))))
    explanation = as_explanation(honest.strip(), headline, verdicts, stage)
    log("statics.explained", route=stage["route"], type=explanation.explanation_type,
        confidence=explanation.confidence_level, bundles=len(verdicts.bundles))
    outcome = _persist_explanation(store, s3, settings, etf_instrument_id, explanation, events)
    write_run_archive(s3, settings, {
        "outcome": "explained",
        "trigger": asdict(gate),
        "route_code": route_code,
        "decomposition": decomp_summary(decomp),
        "holdings_asof": holdings_asof,
        "events": archived_events(events),
        "explanation": explanation.raw,
        "persistence": outcome,
    })
    log("done", route=route_code, events=len(events), **outcome)
    return 0


def _persist_explanation(
    store: EventStore, s3, settings: Settings, etf_instrument_id: str, explanation, events,
) -> dict[str, Any]:
    """FK 전제가 있으면 RDS 에, 없으면 S3 로 폴백해 설명을 영속한다."""
    prereqs = store.explanation_prerequisites(settings, etf_instrument_id)
    missing = [key for key, value in prereqs.items() if not value]
    if missing:
        location = write_explanation_to_s3(s3, settings, explanation.raw, events)
        log(
            "explanation_result.skipped",
            reason="missing_prerequisites",
            missing=missing,
            s3=location,
            trade_date=settings.trade_date.isoformat(),
        )
        return {"persisted": "s3", "location": location, "missing": missing}
    return store.persist_explanation(
        settings, etf_instrument_id, explanation,
        route_id=prereqs["route"],
        bundle=prereqs["bundle"],
        primary_thread_id=_primary_thread_id(events),
        events=events,
    )


def _primary_thread_id(events: list[EventContext]) -> str | None:
    """설명이 대표로 매다는 event thread — **스레드가 붙은 첫 이벤트**를 고른다.

    ``events[0]`` 을 그대로 쓰면(fetch 는 source_event_id 순 정렬), upstream assemble-events
    가 아직 스레드하지 않은(thread_id NULL) 구성종목 이벤트가 먼저 오면 primary_thread_id
    가 NULL 이 돼, 스레드된 이벤트가 목록에 있는데도 계보가 끊긴다. 뉴스 대상을 KODEX
    9종에서 전체 holdings 로 넓히며 unthreaded 이벤트가 섞이기 시작했다(ALPHA-467,
    edge-review). ``None`` 은 목록의 **어떤** 이벤트도 스레드되지 않았을 때만.
    """
    return next((e.thread_id for e in events if e.thread_id), None)
