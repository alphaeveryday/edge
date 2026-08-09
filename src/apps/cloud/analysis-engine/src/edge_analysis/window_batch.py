"""Cloud-only 요청창 배치: 기존 분봉 계보가 있는 ETF를 계산하고 RDS/S3에 적재한다."""
from __future__ import annotations

from dataclasses import replace

from .adapters.eventstore import EventStore
from .adapters.lake import make_s3_client
from .adapters.trace import write_agent_trace
from .config import PipelineError, load_settings
from .observability import collect_trace, log, record
from .statics.duck import CausalLake
from .statics.interval import final_explanation_payload, window_facts
from .statics.plain import card
from .statics.record import Verdicts, as_explanation

_ROUTE_KIND = {
    "COMMON_FACTOR": "시장",
    "CONCENTRATED": "고유",
    "FALLBACK_2LEG": "혼합",
    "FLOW_DOMINATED": "괴리단독",
}


def run_window_universe(day: str | None, start: str, end: str, request_id: str | None) -> int:
    """S3/Glue/RDS만 사용해 가능한 당일 ETF 요청창을 전량 저장한다."""
    base = load_settings(trade_date=day, request_id=request_id)
    if not base.release_bundle_version:
        raise PipelineError("ALPHAMALE_RELEASE_BUNDLE_VERSION이 없어 explanation_run FK를 고정할 수 없습니다")
    s3 = make_s3_client(base)
    store = EventStore.connect(base)
    try:
        candidates = store.window_candidates(base.trade_date)
        if not candidates:
            raise PipelineError(f"{base.trade_date}: 분봉 route 계보가 있는 ETF가 없습니다")
        # 거래일을 넘긴다 - 5분봉 정본이 그 날을 담는지 판정해야 낡은 표를 안 잡는다.
        lake = CausalLake(day=base.trade_date.isoformat())
        stored = skipped = errors = 0
        for candidate in candidates:
            settings = replace(
                base,
                etf_ticker=candidate.ticker,
                request_id=f"{base.request_id}-{candidate.ticker}",
            )
            try:
                with collect_trace() as trace:
                    record("window_batch.start", ticker=candidate.ticker, day=str(base.trade_date),
                           window_start=start, window_end=end, route_id=candidate.route_id)
                    facts = window_facts(
                        lake, candidate.ticker, candidate.instrument_id,
                        str(base.trade_date), start, end,
                    )
                    payload = final_explanation_payload(facts)
                    for block in payload["blocks"]:
                        record("window_batch.block", **block)
                    record("window_batch.complete", blocks=len(payload["blocks"]))
                manifest = write_agent_trace(s3, settings, trace)
                if manifest is None:
                    raise PipelineError(f"{candidate.ticker}: 중간 분석 trace S3 적재 실패")
                stage = {
                    "window": {
                        "window_start": facts.window_start,
                        "window_end": facts.window_end,
                        "as_of": facts.window_end,
                        "lineage": list(facts.lineage),
                    },
                    "final_explanation": payload,
                    "analysis_trace": manifest,
                }
                verdicts = Verdicts(
                    route_kind=_ROUTE_KIND.get(candidate.route_code, ""),
                )
                explanation = as_explanation(
                    payload["rendered_text"], card(payload["rendered_text"]), verdicts, stage,
                )
                outcome = store.persist_explanation(
                    settings, candidate.instrument_id, explanation,
                    route_id=candidate.route_id,
                    bundle=base.release_bundle_version,
                    primary_thread_id=None,
                    events=[],
                    run_reason="WINDOW_BACKFILL",
                )
                stored += 1
                log("window_batch.stored", ticker=candidate.ticker, **outcome)
            except ValueError as exc:
                skipped += 1
                log("window_batch.skipped", ticker=candidate.ticker, reason=str(exc))
            except Exception as exc:  # noqa: BLE001 — 한 종목 실패 뒤 나머지 가능한 종목은 계속한다
                errors += 1
                log("window_batch.failed", ticker=candidate.ticker,
                    error=f"{type(exc).__name__}: {exc}")
        log("window_batch.done", candidates=len(candidates), stored=stored,
            skipped=skipped, errors=errors, window_start=start, window_end=end)
        return 1 if errors or not stored else 0
    finally:
        store.close()
