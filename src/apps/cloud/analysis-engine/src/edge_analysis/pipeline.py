"""run 오케스트레이션 — 트리거 소비 → 분해 → 설명 → 영속.

의존성(lake·store·client·s3)을 주입해 제어 흐름을 I/O 없이 테스트할 수 있다. 엔진은
파이프라인이 만든 feature 산출물만 읽는다(ADR-0028) — 트리거 행이 없으면 그날은
'정상 변동'이 일급 답이다.
"""
from __future__ import annotations

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
from .adapters.llm import AnalysisClient, TracingClient, analyze
from .adapters.trace import write_agent_trace
from .config import KST, PipelineError, ReturnsNotReadyError, Settings
from .domain.decomposition import compute_decomposition, decide_route
from .domain.models import EventContext
from .observability import collect_trace, log, utcnow_iso

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

    route_code, event_search = decide_route(decomp)
    ids = store.persist_observation_route(
        gate.trigger_id, decomp, route_code, event_search, entity_index,
        minute=minute_gate is not None,
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

    # P0–P9 인과귀속으로 설명을 만든다. `analyze` 시그니처는 고정이고 의존성만 주입한다 —
    # 클라우드 진입점(CLI·run)은 그대로다. store 커넥션을 공유하므로 PIT 기준이 갈리지 않는다.
    causal = store.causal_data() if settings.causal_enabled else None
    # P2·P3·P5 의 자유 질의 표면. 같은 커넥션·같은 시점이라 두 표면이 갈리지 않는다.
    causal_sql = (store.sql_surface(as_of=utcnow_iso(), trade_date=settings.trade_date)
                  if causal is not None else None)
    # canonical(S3) 온톨로지 표면. **선택 의존**이고 셋이 다 있어야 붙는다. 안 붙으면
    # 재무·지배구조 어휘가 프롬프트에 아예 안 실리고 P8 이 그 영역을 미개봉으로 적는다 -
    # 조용히 빈 것과 알고 없는 것을 가르는 자리다.
    if causal_sql is not None and settings.canonical_manifest and settings.canonical_database:
        from .adapters.canonical_surface import (CanonicalSurface, Surfaces,
                                                 athena_runner, load_manifest)
        manifest = load_manifest(settings.canonical_manifest)
        canonical_sql = CanonicalSurface(
            athena_runner(database=settings.canonical_database,
                          output=settings.canonical_output,
                          profile=settings.domain_docs_profile),
            manifest,
            # ★ 시점은 **거래일**이다. `utcnow` 를 쓰면 그날 이후 정정된 값이 보인다 -
            # Postgres 쪽은 `available_at <= as_of` 로 사건 공개 시각을 다루지만
            # canonical 은 날짜 단위 공표축이라 기준이 다르다.
            as_of=settings.trade_date)
        causal_sql = Surfaces(causal_sql, canonical_sql)
        log("canonical.attached", database=settings.canonical_database,
            tables=len(manifest.get("tables") or ()))
    # 도메인 문서 조회는 **선택 의존**이다. 버킷이 없으면 붙이지 않고, 그러면 제안이
    # `lookups` 로 물어도 조회 없이 진행한다 - 산업 지식이 없다고 설명을 멈추지 않는다.
    domain_docs = None
    if settings.domain_docs_bucket:
        from .adapters.domain_docs import DomainDocs
        domain_docs = DomainDocs(bucket=settings.domain_docs_bucket,
                                 profile=settings.domain_docs_profile or None)
        log("domain_docs.attached", bucket=settings.domain_docs_bucket)
    with collect_trace() as trace:
        # 인과 경로만 프롬프트·응답을 남긴다 — trace 를 쓰지 않는 단일 프롬프트 경로에
        # 데코레이터를 끼우면 버퍼 없이 도는 호출에 비용만 붙는다.
        explanation = analyze(
            TracingClient(client) if causal is not None else client,
            etf_ticker=settings.etf_ticker, etf_name=etf_name,
            name_by_ticker=name_by_ticker, trade_date=settings.trade_date,
            decomp=decomp, gate=gate, route_code=route_code, events=events,
            causal=causal,
            causal_sandbox=settings.causal_sandbox_enabled,
            domain_docs=domain_docs, causal_sql=causal_sql,
            causal_registry_root=settings.causal_registry_root or None,
            etf_instrument_id=etf_instrument_id,
        )
    if causal is not None:
        # 인과가 꺼진 런은 남길 중간 과정이 없다 — 내용 없는 trace 파일을 만들지 않는다.
        write_agent_trace(s3, settings, trace)
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
