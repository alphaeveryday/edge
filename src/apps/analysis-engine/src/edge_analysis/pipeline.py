"""Run orchestration — consume the trigger, decompose, explain, persist.

Dependencies (lake, store, client, s3) are injected so the control flow is
testable without I/O. The engine reads only pipeline-produced feature outputs
(ADR-0028): a missing trigger row is a first-class "normal variation" answer.
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
from .config import Settings
from .domain.decomposition import compute_decomposition, decide_route
from .domain.models import KODEX_CONSTITUENTS
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
    log("start", trade_date=settings.trade_date.isoformat(), request_id=settings.request_id)

    entity_index = store.load_entity_index()
    etf_instrument_id = store.resolve_etf_instrument(settings.etf_ticker)

    # Consume price from the lake and decompose the ETF move (L1).
    holdings, holdings_asof = lake.load_holdings(settings.etf_ticker, _MARKET, settings.trade_date)
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

    # The L0 gate is consumed, not computed (ALPHA-411): no row == normal day.
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

    # Consume the events the pipeline assembled (ALPHA-412) — read only.
    kodex_events = []
    if event_search:
        kodex_events = store.fetch_kodex_events(settings.trade_date, list(KODEX_CONSTITUENTS))
        log("events.ready", kodex_events=len(kodex_events))

    explanation = analyze(
        client, etf_ticker=settings.etf_ticker, trade_date=settings.trade_date,
        decomp=decomp, gate=gate, route_code=route_code, events=kodex_events,
    )
    outcome = _persist_explanation(store, s3, settings, etf_instrument_id, explanation, kodex_events)
    write_run_archive(s3, settings, {
        "outcome": "explained",
        "trigger": asdict(gate),
        "route_code": route_code,
        "decomposition": decomp_summary(decomp),
        "holdings_asof": holdings_asof,
        "kodex_events": archived_events(kodex_events),
        "explanation": explanation.raw,
        "persistence": outcome,
    })
    log("done", route=route_code, kodex_events=len(kodex_events), **outcome)
    return 0


def _persist_explanation(
    store: EventStore, s3, settings: Settings, etf_instrument_id: str, explanation, kodex_events,
) -> dict[str, Any]:
    """Persist to RDS when FK prerequisites exist, otherwise fall back to S3."""
    prereqs = store.explanation_prerequisites(settings, etf_instrument_id)
    missing = [key for key, value in prereqs.items() if not value]
    if missing:
        location = write_explanation_to_s3(s3, settings, explanation.raw, kodex_events)
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
        primary_thread_id=kodex_events[0].thread_id if kodex_events else None,
        event_count=len(kodex_events),
    )
