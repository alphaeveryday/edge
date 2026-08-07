"""run 오케스트레이션 — 트리거 소비 → 분해 → **통계 인과귀속** → 영속.

의존성(lake·store·client·s3)을 주입해 제어 흐름을 I/O 없이 테스트할 수 있다. 엔진은
파이프라인이 만든 feature 산출물만 읽는다(ADR-0028) — 트리거 행이 없으면 그날은
'정상 변동'이 일급 답이다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, replace
from datetime import timedelta
from typing import Any

from .adapters.archive import archived_events, decomp_summary, write_run_archive
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
    if not settings.trigger_id:
        # 진입점을 계약으로 막는다(ALPHA-806). 트리거 없이 도는 일봉 설명은 확정 일봉이
        # 마감 뒤에야 나와 장중엔 원리적으로 층을 못 세웠고(`layer_route=미상`), 같은
        # 대상에 분봉 경로와 다른 답을 냈다. 설명은 분봉 트리거로만 시작한다.
        raise PipelineError(
            "trigger_id 없이 분석을 시작할 수 없다 — 설명은 분봉 트리거로만 시작한다")
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
    # 분봉 분해 입력(ALPHA-710) — 분모는 **전일 종가**다(ALPHA-747). 트리거 판정이
    # `intraday-anchor-v2` 로 전일 종가 앵커가 되면서 시가 축은 판정과 갈렸고,
    # 시가 원장(minute_session_open)은 판정기에서 prev_close 결손 시 폴백으로만
    # 남아 대개 비어 있다 — 그것을 분해의 필수 입력으로 두면 장중 설명이 전건
    # 차단된다(08-05 dev 실측: start 709건·분해 0건).
    # 트리거 window 의 세대·checksum 은 원장의 마지막 커밋 쌍이 정본이다 — 발화 후
    # 정정이 끼면 최신 커밋이 더 정확한 가격이고, checksum 은 그 세대의 바이트다.
    trigger_meta = store.fetch_minute_window_meta(
        minute_row.session_id, minute_row.window_start)
    if trigger_meta is None:
        raise ReturnsNotReadyError(
            f"트리거 window 원장이 없다: session={minute_row.session_id}"
            f" window={minute_row.window_start.isoformat()} — 커밋 미착지")
    trigger_generation, trigger_checksum = trigger_meta
    prev_closes = lake.load_prev_closes(_MARKET, settings.trade_date)
    if not prev_closes:
        # 분모가 통째로 없으면 모든 unit 이 None 이 돼 total_priced=0 분해가 정상
        # 설명으로 영속된다 — 아래 빈 검사는 dict 가 truthy 라 못 잡는다(Rule 12).
        raise ReturnsNotReadyError(
            f"직전 거래일 price_daily 파티션이 없다: market={_MARKET}"
            f" trade_date={settings.trade_date.isoformat()} — 분봉 분해의 분모가"
            " 없다. 빈 분해를 설명으로 만들지 않는다")
    returns = lake.load_minute_returns(
        _MARKET, settings.trade_date.isoformat(),
        minute_row.window_start.astimezone(KST).strftime("%H%M"),
        trigger_generation, trigger_checksum, prev_closes,
    )
    # 트리거 window 가 INCOMPLETE 면 발화 ETF 행만 있고 구성종목이 통째로 빠질 수
    # 있다 — dict 는 truthy 라 아래 빈 검사를 통과하고, total_priced=0 분해가 정상
    # 설명으로 영속된다(원결함의 부활 코너). 정정 세대가 낫게 하는 실패라 재시도.
    if returns and not any(returns.get(h.ticker) is not None for h in holdings):
        raise ReturnsNotReadyError(
            f"구성종목 가격이 0건이다: session={minute_row.session_id}"
            f" window={minute_row.window_start.isoformat()} — INCOMPLETE window"
            " 이거나 구성종목 미수집. 빈 분해를 설명으로 만들지 않는다")
    if not returns:
        empty_reason = (
            f"분봉 canonical 수익률이 비었다: session={minute_row.session_id}"
            f" window={minute_row.window_start.isoformat()} — window artifact"
            " 미착지(커밋 지연)거나 결손이다. 빈 분해를 설명으로 만들지 않는다"
        )
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

    # L0 게이트는 계산이 아니라 소비다(ALPHA-411). 분봉 입력이면 게이트가 이미 손에
    # 있다 — 발화했기에 호출됐다. 트리거가 곧 진입 조건이 된 뒤로(ALPHA-806) 게이트
    # 부재('평온한 날')는 도달할 수 없는 상태다 — 일 단위 조회 폴백과 함께 걷어냈다.
    gate = minute_gate

    # ── 라우팅은 **층 회계**가 정한다 (ALPHA-671) ─────────────────────────────
    # 레거시 `decide_route` 는 구성종목 집중도만 봤다. 그런데 하루를 끈 것이 시장인지
    # 섹터인지 그 종목인지는 **층 분해**가 답한다 - 실측(042700 07-31): 하루의 77%가
    # 시장인데 종목 가설 9간선을 세워 전부 죽었다. 원장의 route_code 가 실제로 물은
    # 질문과 달라지면 계보가 거짓말을 한다.
    #
    # `event_search` 를 호출자가 정하게 두지 않는다 - 원장 CHECK 가 route_code 와
    # 묶으므로 코드에서 파생한다(`route_code_of`).
    from .statics.duck import CausalLake
    from .statics.interval import clamp
    from .statics.layers import SESSION_OPEN, decompose as layer_decompose
    from .statics.record import route_code_of
    from .statics.route import route_etf

    day_iso = settings.trade_date.isoformat()
    # 거래일을 **레이크 구성 시점에** 넘긴다 - 5분봉 정본(Iceberg)이 이 날을 담고 있는지
    # 를 거기서 판정하고, 못 담으면 canonical 합집합으로 내려간다(`iceberg_covers`).
    lake = causal_lake if causal_lake is not None else CausalLake(day=day_iso)

    # 분봉 트리거는 **구간 모드**로 층을 가른다. 하루 모드는 당일 일봉을 요구하는데
    # (`decompose` 는 `d0` 가 계열에 없으면 None) 확정 일봉은 마감 뒤에 나온다 - 장중엔
    # 원리적으로 못 채운다. 2026-08-06 장중 전건이 그 자리에서 `layer_route=미상` 이었다.
    # 구간 모드는 같은 시각창의 과거 60일에서 β 를 뽑아 5분봉만으로 선다(`_series`).
    #
    # 창과 층 분해를 **여기서 한 번만** 만들어 라우팅과 설명이 나눠 쓴다. 두 번 계산하면
    # 둘이 조용히 갈라지고, 그러면 원장의 route_code 가 산문이 설명한 창과 달라진다.
    # `clamp` 를 거치는 이유도 같다 - 설명 쪽(`window_facts`)이 그것을 쓰므로 정규화
    # 결과가 한 벌이어야 한다.
    #
    # 창 문자열도 아래 `window` 조립분과 **같은 변수**를 쓴다 - 같은 식을 두 번 적으면
    # 한쪽만 고쳐지는 날이 온다. `roll` 은 `run_statics` 로 넘겨 `window_facts` 가
    # 재계산하지 않게 한다(그 재계산이 갈림의 실제 경로였다).
    window_end = (
        minute_row.window_start + timedelta(minutes=5)
    ).astimezone(KST).strftime("%H:%M")
    clock = clamp(SESSION_OPEN[:5], window_end)[:2]

    roll = rt = None
    routing_failed = False
    try:
        roll = layer_decompose(lake, settings.etf_ticker, day_iso, clock=clock)
        rt = route_etf(roll)
    except Exception as exc:                # noqa: BLE001 - 표면 부재를 사유로 남긴다
        # **분해까지 됐어도 라우팅이 터지면 그 분해는 버린다.** `route_etf` 가 던지면
        # `roll` 만 살아남는데, 원장에는 `rt=None` 이라 미상(PRICE_ONLY)이 적힌다.
        # 그 상태로 분해를 설명 쪽에 넘기면 원장은 "층을 못 봤다" 인데 산문에는 시장·
        # 섹터·종목 기여가 실린다 - 이 경로가 닫으려는 갈림 그대로다.
        #
        # 버리기 전에 **무엇을 버렸는지는 남긴다** - 분해는 됐는데 라우터가 깨진 경우
        # 예외 문자열만으로는 어떤 층 값이 깨뜨렸는지 재현이 안 된다.
        #
        # ⚠️ 진단은 **어떤 경우에도 런을 죽이지 않는다.** 이 요약이 만지는 값은 방금
        # 라우터를 깨뜨린 바로 그 값이다 - `x.kind` 역참조든 `layers` 순회 자체든 다시
        # 던질 수 있고, 그러면 `roll = None` 과 route 적재까지 못 가 런이 통째로 죽는다.
        # `getattr` 기본값은 `AttributeError` 만 삼키므로 부족하다. 블록째 감싼다.
        try:
            discarded = [getattr(x, "kind", "?")
                         for x in (getattr(roll, "layers", None) or ())]
        except Exception:               # noqa: BLE001 - 진단 실패는 진단으로만 말한다
            discarded = ["?"]
        log("statics.layers.failed", error=f"{type(exc).__name__}: {exc}",
            discarded_layers=discarded, had_rollup=roll is not None)
        roll = None
        # 폐기가 **확신도를 올려서는 안 된다**. 아래 `idio_qualified` 는 `roll is None`
        # 을 "분해가 아예 없다 → 자격을 따질 대상도 없다(관대하게 True)" 로 읽는데,
        # 폐기된 None 은 뜻이 다르다 - 층을 봤는데 라우팅이 깨진 것이다. 구분하지 않으면
        # 실패한 런이 오히려 더 높은 확신도로 영속된다(Rule 12).
        routing_failed = True
    route_code, event_search = route_code_of(rt.kind if rt else "")
    ids = store.persist_observation_route(
        gate.trigger_id, decomp, route_code, event_search, entity_index,
        minute=True,
    )
    log("trigger.consumed", route=route_code, event_search=event_search,
        layer_route=(rt.kind if rt else "미상"), **ids)

    # 영속 전제를 **LLM 앞에서** 검사한다(ALPHA-797). 세 전제가 모두 이 시점에 확정돼
    # 있다 - profile·bundle 은 이 런이 만드는 게 아닌 사전 상태고, route 는 바로 위
    # `persist_observation_route` 가 커밋했다. 뒤에서 검사하면 결손을 **LLM 을 태운
    # 뒤에야** 알게 되고, 그때는 결과를 버리기 아까워 S3 폴백으로 접게 된다 - 그 폴백이
    # `explanation_run` 을 안 남겨 소비자의 멱등 프리플라이트(`has_run_for_route`)가
    # 영영 false 로 남고, 재배달이 같은 트리거에 LLM 을 다시 태운다(#554 리뷰).
    #
    # ⚠️ **EOD 레인의 폭발 반경이 여기서 바뀐다.** 셋 중 결손이 실제로 갈리는 건
    # `profile` 뿐이다 - `route` 는 종목별이되 바로 위가 방금 커밋해 결손이 구조적으로
    # 불가능하고, `bundle` 은 런 전역이다. 그래서 신규 ETF 를 유니버스에 넣고
    # `etf_profile` 을 안 채우면 그 한 종목의 비0 종료가 `AnalysisResultCheck`(전량성공
    # 게이트, ADR-0028)를 통해 **유니버스 일일 런 전체**를 실패로 만든다.
    #
    # 이전에는 그 종목만 S3 로 새고 런은 초록이었다 - 그게 관대한 쪽 오류였다. 설명이
    # 없는 ETF 가 조용히 초록에 묻히면 아무도 안 채운다. 대가는 의식적으로 받는다:
    # profile 결손은 데이터가 아니라 **사람이 채우는 마스터**라 자기치유가 없고, 채울
    # 때까지 일일 런이 매일 빨갛다. 같은 종류의 선례가 바로 이 파일에 이미 있다 -
    # `resolve_etf_instrument` 결손도 종목 하나로 런을 죽인다(ALPHA-467). `holdings`
    # 빈은 선례로 못 쓴다: 그건 매일 적재되는 데이터라 다음날 자연 복구된다(Rule 12).
    prereqs = store.explanation_prerequisites(settings, etf_instrument_id)
    missing = [key for key, value in prereqs.items() if not value]
    if missing:
        raise PipelineError(f"설명 영속 전제가 없다: {missing}")

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
    # 표면 부재 런은 **게시본 자리를 차지하면 안 된다.** 게시 판정은 발화당 첫 결과를
    # PUBLISHED 로 만드는데("첫 결과가 그 발화의 최선"), 판정불가가 먼저 오면 그 전제가
    # 깨진다 - 데이터가 들어온 뒤 재실행해도 DRAFT 로 밀린다. 여기서만 그 사실을 안다.
    surface_ok = True
    window_meta: dict[str, Any] = {}
    with collect_trace() as trace:
        try:
            window = {
                "instrument_id": etf_instrument_id,
                "window_start": SESSION_OPEN[:5],
                "window_end": window_end,
                "window_meta": window_meta,
            }
            # 라우팅이 쓴 그 분해를 그대로 넘긴다 - 같은 창을 두 번 질의하면 그 사이
            # 분봉 canonical 이 정정될 때 route_code 와 산문 근거가 한 explanation 안에서
            # 갈린다. **창 인자 밖**에 두는 이유: 하루 모드(배치)도 같은 분해를 두 번
            # 부르고 있었다 - 두 경로가 같은 계약을 쓴다.
            #
            # `None` 도 **넘기는 값**이다(재료 부재·질의 실패·라우팅 실패로 폐기). 그때
            # 설명 쪽이 다시 계산하면, 라우팅 시점엔 없던 재료가 그새 착지했을 때 원장은
            # 미상(PRICE_ONLY)인데 산문에는 층 근거가 실린다 - 같은 갈림이다.
            #
            # ⚠️ 하루 모드에 남는 갈림: 분해는 한 벌이 됐지만 **괴리 판정(`pv`)은 아직
            # 아니다** - `etfcell` 하루 갈래가 `screen()` 으로 자기 `pv` 를 뽑아
            # `route_etf(roll, pv)` 로 다시 라우팅하므로, 원장이 `COMMON_FACTOR` 인데
            # 산문은 `괴리단독` 일 수 있다(이 변경 전에도 그랬다 - 좁혔을 뿐 닫지 못했다).
            # 닫으려면 라우팅 결과 자체를 넘겨야 하는데, 하루 경로는 ALPHA-785 범위 밖이라
            # 여기서 손대지 않는다.
            text = run_statics(lake, settings.etf_ticker, day_iso, ask,
                               roll=roll, **window)
        except Exception as exc:            # noqa: BLE001 - 표면 부재를 사유로 남긴다
            # 레이크가 못 서면 **설명이 없다고 말한다** - 빈 산문을 정상 분석으로
            # 위장하지 않는다(Rule 12). 계보는 그래도 남아 재실행 대상이 된다.
            text = (f"[ETF] {settings.etf_ticker} {day_iso} 판정불가 — "
                    f"{_verdict_reason(exc)}")
            log("statics.surface.unavailable", error=f"{type(exc).__name__}: {exc}")
            surface_ok = False
    # **커버리지를 읽는 소비자를 만든다.** `layers`·`duck` 은 로깅을 모르고 경로와 폴백
    # 사유를 `exists`/`unbound` 에만 적는다 - "커버리지 보고가 곧 '어떻게 쟀는가' 다"
    # (`_series` 주석). 그런데 그 딕셔너리를 아무도 읽지 않아, 2026-08-06 하루 동안
    # 다음을 전부 추측으로 다뤘다: 5분봉을 Iceberg 로 읽었나 canonical 합집합으로
    # 읽었나 · Athena 로 집계했나 DuckDB 로 폴백했나(그 사유는 무엇인가) · 층 분해가
    # 왜 None 인가.
    #
    # 성공·실패 어느 쪽으로 끝났든 찍는다 - 폴백은 **성공한 런에서** 일어나므로
    # 예외 경로에만 두면 정작 봐야 할 것을 못 본다.
    log("statics.coverage",
        exists=_redacted(getattr(lake, "exists", {})),
        unbound=_redacted(getattr(lake, "unbound", {})))
    trace_manifest = write_agent_trace(s3, settings, trace)
    if trace_manifest is None:
        # **관측 부재가 설명을 버리지 않는다** - `trace.py` 의 계약이 그것이다("쓰기 실패·
        # 비 s3:// prefix·빈 trace 면 None, 어느 쪽이든 런은 계속"). 여기서 죽이면
        # **성공한 런일수록** 죽는다: 분봉 요청창 경로가 예외 없이 완주하면서 log() 를 한
        # 번도 안 남기면 trace 가 비고, 그 빈 리스트가 설명을 통째로 버린다 - 2026-08-06
        # dev 장중에 소비자 런 전건이 이 자리에서 죽어 설명 저장이 0건이었다.
        #
        # `events` 가 두 부재를 가른다: 0 이면 **기록할 것이 없었고**, 그 외는 쓰기가 실패한
        # 것이다(그 사유는 `write_agent_trace` 의 `agent_trace.failed` 가 이미 남긴다).
        # 이름을 `failed` 로 쓰지 않는 이유가 그것이다 - 대개 S3 에 가지도 않았다.
        log("agent_trace.absent", events=len(trace))

    honest = plain = text.strip()
    from .statics.plain import card as _card
    headline = _card(plain)
    # 층·라우팅은 위에서 이미 냈다 - 다시 분해하지 않는다(같은 셀에 두 답 금지)
    stage = {"route": (rt.kind if rt else ""), "layers": [
        {"kind": x.kind, "name": x.name, "contribution": x.contribution}
        for x in (roll.layers if roll else ())],
        "idio": (roll.idio if roll else None), "rho": (roll.rho if roll else None),
        # **왜 층이 비었는지**를 원장이 스스로 말한다. 라우팅 실패로 폐기한 경우와 재료가
        # 없던 경우는 위 필드가 똑같이 비지만 확신도가 갈린다(폐기면 LOW) - 이 값이 없으면
        # explanation_result 만 보는 감사·재처리 소비자가 그 차이를 재현하지 못하고 로그를
        # 뒤져야 한다. 로그는 보존 기간이 있고 원장은 남는다.
        "routing_failed": routing_failed,
        # 요청창의 내부 블록·시간축·근거 조회키는 ``stage_results``에도 구조화해
        # 최종 문자열에서 다시 파싱하지 않고 추적한다.
        "plain": plain.strip().lstrip("=").strip()}
    final_payload = window_meta.pop("final_explanation", None)
    if window_meta:
        stage["window"] = window_meta
    if final_payload is not None:
        stage["final_explanation"] = final_payload
    payload_bundles = tuple(
        ref.removeprefix("analysis_evidence_bundle:")
        for block in ((final_payload or {}).get("blocks") or ())
        for ref in (block.get("evidence_refs") or ())
        if ref.startswith("analysis_evidence_bundle:")
    )
    stage["analysis_trace"] = trace_manifest
    verdicts = verdicts_from(
        text, route_kind=(rt.kind if rt else ""),
        idio_qualified=bool(not routing_failed and (
            roll is None or roll.rho is None or abs(roll.rho) < 0.20)),
        bundles=tuple(sorted(set(payload_bundles or re.findall(
            r"\bev_[0-9a-f]{16}\b", text)))))
    explanation = as_explanation(honest.strip(), headline, verdicts, stage)
    log("statics.explained", route=stage["route"], type=explanation.explanation_type,
        confidence=explanation.confidence_level, bundles=len(verdicts.bundles))
    outcome = store.persist_explanation(
        settings, etf_instrument_id, explanation,
        route_id=prereqs["route"],
        bundle=prereqs["bundle"],
        primary_thread_id=_primary_thread_id(events),
        events=events,
        publishable=surface_ok,
    )
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


def _verdict_reason(exc: Exception) -> str:
    """판정불가 산문에 실을 사유. **도메인 예외는 사유만, 그 외는 타입까지.**

    이 문자열은 검수 화면에 그대로 뜬다. 도메인 예외(`PipelineError`·`IntervalError`)의
    메시지는 이미 사람이 읽는 문장이라 `ValueError:` 같은 접두가 붙으면 스택 레벨 문구가
    된다 — 실측: `판정불가 — 통계 표면 부재 — ValueError: 요청창 09:00~09:54 5분 수익률을
    계산하지 못했습니다`.

    예상 못 한 예외는 **타입을 남긴다.** 지우면 버그가 데이터 부재처럼 읽혀, 고칠 것과
    기다릴 것이 같은 문장이 된다(Rule 12 — 부재와 결함을 갈라 말한다).
    """
    from .statics.interval import IntervalError
    if isinstance(exc, (PipelineError, IntervalError)):
        return str(exc)[:160]
    return f"{type(exc).__name__}: {str(exc)[:160]}"


_DSN_SECRET = re.compile(
    r"(password\s*=\s*)('[^']*'|\"[^\"]*\"|\S+)"   # 키워드형 (따옴표 안 공백 포함)
    r"|(?<=://)([^:/@\s]*:)[^@\s]+(?=@)",          # URI 형 [user]:pass@host (사용자명 생략 가능)
    re.IGNORECASE)


def _redacted(notes: dict) -> dict:
    """커버리지 값의 자격증명을 가린다 — **2차 방어**다.

    커버리지는 부재를 예외 전문 그대로 담는다(침묵 금지). 그 문자열에 DSN 이 섞일 수 있는
    유일한 지점은 `CausalLake._rdb` 이고, **보증은 거기서 한다** — 지울 문자열을 손에 쥐고
    있으니 정확히 지운다(`str(e).replace(dsn, …)`).

    여기는 그 뒤를 받치는 그물이다. 정규식으로 안전을 주장하지 않는다(`adapters/readonly.py`
    가 같은 말을 한다: 안전은 앞 층이 담당하고 마지막 층은 사용성이다) — 새 원천이 생겨도
    자격증명이 한 겹은 걸리게 두는 것이 목적이다.
    """
    return {k: _DSN_SECRET.sub(lambda m: (m.group(1) or "") + (m.group(3) or "") + "***", v)
            if isinstance(v, str) else v for k, v in notes.items()}


def _primary_thread_id(events: list[EventContext]) -> str | None:
    """설명이 대표로 매다는 event thread — **스레드가 붙은 첫 이벤트**를 고른다.

    ``events[0]`` 을 그대로 쓰면(fetch 는 source_event_id 순 정렬), upstream assemble-events
    가 아직 스레드하지 않은(thread_id NULL) 구성종목 이벤트가 먼저 오면 primary_thread_id
    가 NULL 이 돼, 스레드된 이벤트가 목록에 있는데도 계보가 끊긴다. 뉴스 대상을 KODEX
    9종에서 전체 holdings 로 넓히며 unthreaded 이벤트가 섞이기 시작했다(ALPHA-467,
    edge-review). ``None`` 은 목록의 **어떤** 이벤트도 스레드되지 않았을 때만.
    """
    return next((e.thread_id for e in events if e.thread_id), None)
