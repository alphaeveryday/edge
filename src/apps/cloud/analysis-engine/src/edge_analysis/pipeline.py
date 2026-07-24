"""run 오케스트레이션 — 트리거 소비 → 분해 → 설명 → 영속.

의존성(lake·store·client·s3)을 주입해 제어 흐름을 I/O 없이 테스트할 수 있다. 엔진은
파이프라인이 만든 feature 산출물만 읽는다(ADR-0028) — 트리거 행이 없으면 그날은
'정상 변동'이 일급 답이다.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .adapters.archive import (
    archived_events,
    decomp_summary,
    write_explanation_to_s3,
    write_run_archive,
)
from .adapters.eventstore import EventStore
from .adapters.lake import LakeReader
from .adapters.llm import AnalysisClient, analyze
from .config import PipelineError, Settings
from .domain.decomposition import compute_decomposition, decide_route
from .domain.models import EventContext
from .observability import log

_MARKET = "KR"


def run(
    settings: Settings,
    *,
    lake: LakeReader,
    store: EventStore,
    client: AnalysisClient,
    s3,
) -> int:
    """당일 파이프라인을 실행하고 종료 코드(성공=0)를 반환한다."""
    log("start", trade_date=settings.trade_date.isoformat(), request_id=settings.request_id)

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
    returns = lake.load_returns(_MARKET, settings.trade_date)
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
    gate = store.fetch_price_trigger(etf_instrument_id, settings.trade_date)
    if gate is None:
        write_run_archive(s3, settings, {
            "outcome": "normal_variation",
            "trigger": None,
            "decomposition": decomp_summary(decomp),
            "holdings_asof": holdings_asof,
        })
        log("done", reason="normal_variation", observed_return=decomp.proxy_ret)
        return 0

    route_code, event_search = decide_route(decomp)
    ids = store.persist_observation_route(
        gate.trigger_id, decomp, route_code, event_search, entity_index
    )
    log("trigger.consumed", route=route_code, event_search=event_search, **ids)

    # 파이프라인이 조립한 이벤트를 소비만 한다(ALPHA-412, 읽기 전용).
    events: list[EventContext] = []
    if event_search:
        # 뉴스 대상 티커는 이 ETF 의 holdings 구성종목이다 — 구 KODEX_CONSTITUENTS
        # 9종목 하드코딩은 다른 ETF 로 돌려도 KODEX 뉴스만 읽었다(ALPHA-467).
        events = store.fetch_event_contexts(
            settings.trade_date, [h.ticker for h in holdings])
        log("events.ready", events=len(events))

    explanation = analyze(
        client, etf_ticker=settings.etf_ticker, etf_name=etf_name,
        name_by_ticker=name_by_ticker, trade_date=settings.trade_date,
        decomp=decomp, gate=gate, route_code=route_code, events=events,
    )
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
        event_count=len(events),
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
