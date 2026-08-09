"""실행 진입점 — ECS RunTask command 또는 로컬에서 호출한다.

    python -m data_pipeline.run
        {ingest-raw|ingest-price-raw|ingest-raw-financial|ingest-raw-disclosure|ingest-raw-etf|ingest-raw-nav|ingest-raw-inav|ingest-raw-etf-profile|ingest-raw-instrument
         |normalize-price|normalize-news|normalize-disclosure|normalize-disclosure-segment
         |normalize-etf|normalize-etf-nav|normalize-etf-profile|normalize-instrument-profile|tag-news|load-instruments|enrich-corp-code|load-price-triggers|load-documents|load-disclosure|load-etf-nav
         |load-assertions|assemble-events}
        [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--run-id RUN_ID] [--config PATH]
        [--source VENDOR] [--input-run-id RUN_ID] [--limit N] [--window-days N]
        [--interval-sec N]

태깅(tag-news)은 LLM 설정을 **env** 로 받는다 — `LLM_API_KEY`(필수)·`LLM_BASE_URL`·`LLM_MODEL`
(analysis-engine analyze_daily.py 와 같은 관례). 수집 설정(`DATA_PIPELINE_*`)이 아니다.

수집 날짜창(--from/--to) — 뉴스·가격·공시만 사용(재무제표·ETF holdings 는 스냅샷이라 창 없음).
tag-news 는 --from/--to 를 **태깅 대상 파티션 프루닝**에 쓴다(raw 수집 창이 아니라 canonical
published_date 범위이며, 미지정은 증분 기본창이 아니라 전체다):
  - 미지정: 풀스캔 — 창 밖 미태깅·정정본까지 쓸어담는 회수 경로다(수동 백필 기본).
  - 일일 SFN: --window-days N 으로 오늘−N일 창을 앱이 계산해 좁힌다(read=O(전체 코퍼스)
    스캔이 런타임을 지배하므로 창이 곧 속도). EventBridge Scheduler 는 정적 입력만 넣어
    '오늘−N'을 못 만들므로, 창은 이 엔트리가 런타임 시계로 정한다. 넓게 둘수록(한 날짜가
    N+1회 스캔) 일시적 llm_error 가 창 안에서 자가 회복한다.
  - 명시(백필): 일회성 RunTask 로 --from/--to 를 넘겨 과거 구간을 적재한다(--window-days 보다 우선).
run_id 는 미지정 시 UTC 타임스탬프. 같은 run_id·창으로 재실행하면 raw 파티션 파일을
같은 키에 다시 써 겹쳐쓰기된다(재현 실행).
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from datetime import datetime, timedelta, timezone

from .config import load_settings
from .db import db_config_from_env
from .minute.consumer import dlq_reconcile_cli, redrive_cli
from .minute.news_consumer import news_consumer_cli
from .minute.disclosure_worker import disclosure_worker_cli
from .minute.news_worker import news_worker_cli
from .minute.eod import qc_session_cli
from .minute.rollup import rollup_session_cli
from .minute.session_cli import drain_session_cli, plan_session_cli
from .minute.states import MINUTE_DATASETS, SOURCE_GROUPS_BY_DATASET
from .minute.session_ops import start_session_cli, stop_session_cli
from .minute.relay import relay_cli
from .minute.price_consumer import price_consumer_cli
from .minute.worker import (
    inav_worker_cli,
    price_worker_cli,
    sector_index_worker_cli,
)
from .lake import (
    make_storage,
    raw_etf_inav_partition,
    raw_investor_estimate_partition,
    raw_etf_nav_partition,
    raw_etf_profile_partition,
)
from .sources import (
    BigKindsNewsSource,
    DartDisclosureSource,
    DartFinancialSource,
    FmpEtfSource,
    FmpFinancialSource,
    FmpNewsSource,
    FmpPriceSource,
    KisDailyPriceSource,
    KisEtfProfileSource,
    KisInavSource,
    KisInvestorEstimateSource,
    KisInvestorSource,
    KisNavSource,
    KrxEtfSource,
    KrxInstrumentSource,
    PoliteClient,
    YahooPriceSource,
)
from .steps import (
    assemble_events,
    backfill_disclosure,
    enrich_corp_code,
    ingest_price_raw,
    load_assertions,
    load_disclosure,
    load_documents,
    load_etf_flow,
    load_etf_holdings,
    load_etf_nav,
    load_instruments,
    load_investor_intraday,
    load_price_daily,
    load_price_triggers,
    ingest_raw,
    ingest_raw_disclosure,
    ingest_raw_etf,
    ingest_raw_financial,
    ingest_raw_instrument,
    ingest_raw_investor,
    normalize_disclosure,
    normalize_disclosure_segment,
    normalize_etf,
    normalize_etf_nav,
    normalize_etf_profile,
    normalize_instrument_profile,
    normalize_investor,
    normalize_investor_estimate,
    normalize_news,
    normalize_price,
    tag_news,
)
from .sources.kis_inav import DEFAULT_INTERVAL_SEC
from .tagging.llm import DEFAULT_BASE_URL, DEFAULT_MODEL, openai_compatible_complete_fn
from .ops import entry as ops_entry

# KIS 시세 TR 초당 한도(EGW00201) 방어용 최소 간격 — 실측 안전값(프로브 MIN_INTERVAL).
KIS_MIN_INTERVAL_SEC = 0.5

# KRX getJsonData(구성종목 PDF) 응답 타임아웃 — **기본 10초로는 100% 실패한다**(ALPHA-368).
# 2026-07-15 라이브 실측: 같은 세션·같은 요청이 timeout=10s 에선 TimeoutError, 45s 에선 12.4초에
# 8190바이트 성공. 이 엔드포인트는 조회 때마다 집계를 도는지 원래 10초를 넘는다 — 느린 게 성질이다.
# 전역 기본값(PoliteClient timeout=10.0)은 안 건드린다: 다른 소스는 10초로 충분하고, 전역을 올리면
# 진짜로 죽은 엔드포인트를 기다리는 시간만 길어진다(Rule 3 — 느린 건 KRX 하나다).
KRX_ETF_TIMEOUT_SEC = 45.0

# 증분 기본 창의 소급 일수 — 어제부터(런 간 경계 겹침을 dedup 이 흡수하도록) 오늘까지.
DEFAULT_LOOKBACK_DAYS = 1
# 가격 증분은 소급을 넉넉히 둔다 — 주말·공휴일엔 EOD 가 없어 소급 1일이면 월요일 런이
# 직전 거래일(금요일) 봉을 놓친다. raw 는 겹치는 거래일을 그대로 append 해 보존하고
# (dedup 안 함), (market,ticker,trade_date) 정체성 병합은 후속 canonical 소관이다.
DEFAULT_PRICE_LOOKBACK_DAYS = 5


def make_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


# 증분 창의 날짜는 **프로세스 시계가 아니라 벤더 달력**이다(ALPHA-883). 우리가 만든 날짜
# 문자열이 그대로 벤더 질의에 실리기 때문이다 — BigKinds `search_page` 의 startDate/endDate 가
# 그 자리이고, 그 벤더는 KST 벽시계로 라벨한다(`normalize_news._KST`). canonical 뉴스 파티션도
# `published_at[:10]` 이라 KST 날짜 키라, 그 파티션을 고르는 `tag-news` 창도 같은 축이다.
#
# UTC 로 날짜를 뽑으면 KST=UTC+9 이라 **09:00 KST 이전에 도는 슬롯에서 하루가 밀린다.** 지금
# 안 깨지는 유일한 이유는 모든 스케줄 슬롯이 09:00 KST 이후이기 때문이고(공시 09:00 은 정확히
# 경계다), 그 불변식은 어디에도 적혀 있지 않았다. 08:10 슬롯 하나를 넣는 순간 그 런은 전날 창을
# 다시 긁고 그날 뉴스를 0건 가져온다 — **에러 없이** 조용히.
# 이 레포엔 축이 다른 시간 관례가 **둘** 있다. **순간**은 UTC 다(`make_run_id`·`started_date`
# 파티션·원장 타임스탬프·`ops.entry._scheduled_time`). **시장 날짜**는 KST 다 —
# `sources/dart_disclosure.py` 가 "파이프라인 전체가 KR 시장 기준이라 KST 다"라고 명문화했고
# `assemble_events`·`dart_values` 도 같은 관례다. 증분 창은 후자(어느 영업일의 데이터인가)라
# KST 쪽이지만, **기본값은 두지 않는다** — 창을 쓰는 스텝이 늘 때 어느 달력으로든 조용히
# 떨어지면 그 창은 하루가 밀린 채 **성공**한다. 스텝마다 선언하고 미선언은 fail-loud 다
# (`ops.entry._LANE_STATE_MACHINE_ARN_ENV` 와 같은 자세, Rule 12).
KST = timezone(timedelta(hours=9))
# 벤더가 `--source` 로 갈리는 스텝과 그 미지정 기본값. **아래 분기가 이 dict 를 읽는다** —
# 리터럴로 두 벌을 두면 한쪽만 바뀌는 순간 창은 이 벤더로, 질의는 저 벤더로 나간다(리뷰 실측:
# 분기 쪽 기본값만 바꿔도 전 스위트가 초록이었다). 사실을 하나로 줄여 드리프트를 불가능하게 한다.
_VENDOR_SPLIT_STEPS = {"ingest-raw": "fmp", "ingest-price-raw": "fmp"}
# 창을 쓰는 스텝의 달력. 벤더가 안 갈리는 스텝은 스텝이 곧 벤더라 source 를 안 본다.
# 기준은 벤더 국적이 아니라 **그 데이터가 어느 시장의 날짜인가**다.
_WINDOW_CALENDAR: dict[tuple[str, str | None], timezone] = {
    ("ingest-raw", "fmp"): timezone.utc,             # 미국 뉴스
    ("ingest-raw", "bigkinds"): KST,
    ("ingest-price-raw", "fmp"): timezone.utc,       # 미국 시세
    ("ingest-price-raw", "kis"): KST,
    ("ingest-price-raw", "yahoo"): KST,              # index_map 이 ^KS11·^KQ11 (KOSPI·KOSDAQ)
    ("ingest-raw-nav", None): KST,                   # KIS
    ("ingest-raw-inav", None): KST,                  # KIS
    ("ingest-raw-investor", None): KST,              # KIS
    ("ingest-raw-disclosure", None): KST,            # DART
    # canonical 뉴스 파티션(`published_at[:10]`)은 벤더마다 달력이 다르다 — normalize 가
    # BigKinds 는 KST 로, FMP 는 UTC 로 라벨한다. tag-news 는 벤더가 안 갈려 한쪽을 골라야
    # 하고, 실제로 도는 건 KR 레인이라 KST 다.
    ("tag-news", None): KST,
    ("load-disclosure", None): KST,                  # canonical 공시(report_date = DART 접수일)
}


def window_calendar_tz(step: str, source: str | None) -> timezone:
    """이 스텝·벤더의 증분 창이 쓰는 달력. 미선언은 fail-loud.

    ponytail: 달력의 임자는 사실 벤더 어댑터다 — KST 어댑터 10여 개가 이미 자기 `_KST` 를
    들고 있고 `dart_disclosure._window_segments` 는 그걸로 창 끝까지 채운다. 이 표는 그 사실의
    재진술이고, 창이 어댑터 생성 **전에** 계산되기 때문에(`ingest_*.run(…, from_date, to_date)`)
    존재한다. 창 계산을 분기 뒤로 옮기면 표가 통째로 사라진다 — 6분기를 건드리는 일이라 별건.
    """
    if step in _VENDOR_SPLIT_STEPS:
        vendor = source or _VENDOR_SPLIT_STEPS[step]
        tz = _WINDOW_CALENDAR.get((step, vendor))
        if tz is None:
            # 표의 키 집합이 곧 그 스텝의 벤더 화이트리스트다. 오타난 `--source` 를 "달력
            # 미선언"으로 진단하면 시킨 대로 표에 넣어도 아무것도 안 고쳐지고, 그제서야
            # 분기의 진짜 에러를 본다 — 게다가 그 진단이 `--from/--to` 유무로 갈린다.
            known = "|".join(sorted(v for s, v in _WINDOW_CALENDAR if s == step and v))
            raise SystemExit(f"알 수 없는 --source: {vendor} ({known})")
        return tz
    tz = _WINDOW_CALENDAR.get((step, None))
    if tz is None:
        raise SystemExit(
            f"증분 창의 달력이 선언되지 않았다: step={step} — `_WINDOW_CALENDAR` 에 추가하라. "
            "기본값을 두면 새 스텝이 어느 달력으로든 조용히 떨어지고, 그 창은 하루가 밀린 "
            "채로 성공한다.")
    return tz


def default_window(now: datetime, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    """증분 기본 창(from, to) = (오늘-소급일, 오늘). 날짜는 `now` 의 tzinfo 가 정한다 —
    호출부가 `window_calendar_tz` 로 벤더 달력을 골라 넘긴다(ALPHA-883)."""
    to_date = now.date()
    from_date = to_date - timedelta(days=lookback_days)
    return from_date.isoformat(), to_date.isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-pipeline")
    parser.add_argument(
        "step",
        choices=["ingest-raw", "ingest-price-raw", "ingest-raw-financial",
                 "ingest-raw-disclosure", "ingest-raw-etf", "ingest-raw-nav", "ingest-raw-inav", "ingest-raw-etf-profile", "ingest-raw-instrument",
                 "ingest-raw-investor", "ingest-raw-investor-estimate",
                 "normalize-price", "normalize-investor", "normalize-investor-estimate",
                 "normalize-news", "normalize-disclosure", "normalize-disclosure-segment",
                 "normalize-etf", "normalize-etf-nav", "normalize-etf-profile", "normalize-instrument-profile", "tag-news", "load-instruments", "enrich-corp-code", "load-price-triggers",
                 "load-price-daily", "load-documents", "load-disclosure", "backfill-normalize-disclosure", "load-etf-nav", "load-etf-holdings", "load-etf-flow", "load-investor-intraday", "load-assertions", "assemble-events",
                 # 운영 원장(ALPHA-530): plan-run=EventBridge→Planner(원장 기록+SFN 시작),
                 # reconcile=주기 대조. 둘 다 원장 DB 필수, storage/수집창과 무관.
                 "plan-run", "reconcile",
                 # 1분 파이프라인(ALPHA-670): relay=outbox→SQS 발행 상주 루프. 원장 DB +
                 # minute_relay 큐 매핑 필수, storage/수집창과 무관.
                 "relay",
                 # 1분 Consumer 운영(ALPHA-672): dlq-reconcile=DLQ 도착과 DB job 상태
                 # 대사(주기 실행), redrive=DEAD job 을 DB 부터 되살려 새 delivery event
                 # 생성. 둘 다 원장 DB 필수, storage/수집창과 무관.
                 "dlq-reconcile", "redrive",
                 # EOD(ALPHA-693): qc-minute-session=drain 끝난 세션의 누락 확정·확정
                 # (DUE 잔존→MISSING, FINALIZED). 원장 DB + storage(orphan 나열) 필요.
                 "qc-minute-session",
                 # EOD(ALPHA-839): rollup-minute-session=그날 5분 파생을 마감 후 1회
                 # 확정. 커밋 후크는 "방금 닫힌 버킷"에서만 발화해 지나간 날을 영영 안
                 # 채운다. 원장 DB + storage(파티션 PUT·구멍 판정) 필요.
                 "rollup-minute-session",
                 # 세션 수명(ALPHA-698): plan=하루치 session+window 멱등 생성(Premarket),
                 # drain=phase 를 DRAINING 으로(EOD). 둘 다 원장 DB 만 필요하다.
                 "plan-minute-session", "drain-minute-session",
                 # 세션 스케일 오케스트레이션(ALPHA-712): start=거래일 판정+계획+desired 1,
                 # stop=drain+원장 게이트 대기+desired 0. 상주 서비스 3종을 올리고 내리는
                 # 유일한 주체다(terraform 은 desired_count 를 ignore_changes 로 뒀다).
                 "start-minute-session", "stop-minute-session",
                 # 1분 Price Worker(ALPHA-706): 상주 수집 루프(ECS Service). 원장 DB +
                 # 토스 자격증명 + storage(artifact PUT) + --universe(planner 와 동일 파일).
                 "price-worker",
                 # 1분 iNAV Worker(ALPHA-851): 장중 추정 NAV 상주 수집 루프. 원장 DB +
                 # KIS 자격증명(kis_nav) + etf_map(krx_etf) + storage + --universe.
                 # ⚠️ 하위 소비자가 없다 — window 확정에서 멈추고 job·outbox 를 안 만든다.
                 "inav-worker",
                 # 1분 가격 판정 Consumer(ALPHA-711): Price Job SQS 소비 상주 루프.
                 # 원장 DB + 큐 설정 + storage(artifact GET) + --universe(판정 대상).
                 "price-consumer",
                 # 1분 뉴스 추출 Consumer(ALPHA-713): News Job SQS 소비 상주 루프.
                 # 원장 DB + 큐 설정 + storage(결과 PUT) + LLM_* env(tag-news 관례).
                 # realtime·backfill 은 같은 스텝을 큐 URL 만 바꿔 서비스 2개로 띄운다.
                 "news-consumer",
                 # 1분 News Worker(ALPHA-707): BigKinds 매분 폴링 상주 루프(ECS Service).
                 # 원장 DB + storage(raw page·manifest PUT) + [bigkinds_news] 정본.
                 # universe 없음 — 뉴스 세션은 소스 단위다.
                 "news-worker",
                 # 1분 Disclosure Worker(ALPHA-875): 공시 체인 5스텝을 한 tick 에 도는 상주
                 # 루프. 원장 DB + storage + [dart_disclosure] 정본. universe 없음(소스 단위).
                 # ⚠️ 수집만이 아니라 collect→normalize×2→load→assemble 을 한 window 에서 돈다.
                 "disclosure-worker",
                 # 1분 업종지수 Worker(ALPHA-887): KRX 업종지수 45종 분봉 상주 루프.
                 # 원장 DB + storage + [minute_sector_index.index_map] 정본 +
                 # minute_price_worker 의 KIS 자격증명(같은 앱키). universe 없음 —
                 # 기대 집합이 config 다(지수는 ETF 명부에도 구성종목에도 없다).
                 # ⚠️ 하위 소비자가 없다 — window 확정에서 멈추고 job·outbox 를 안 만든다.
                 "sector-index-worker"],
    )
    parser.add_argument("--from", dest="from_date", default=None, help="수집 시작일 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="수집 종료일 YYYY-MM-DD")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--config", default=None, help="설정 파일 경로(기본: 동봉 설정)")
    # 정제 스텝 전용 — 그 수집 런의 raw 만 읽어 canonical 을 적재한다(ALPHA-389). SFN 이 이
    # 경로로 돈다: 정제 비용이 여태 쌓인 raw 전체가 아니라 이번 런에 비례한다. 미지정이면
    # 전체를 읽는다 — **백필·복구 수단**이다(실패한 런의 raw 를 나중에 주워오거나, 정체성
    # 로직 변경을 이미 수집된 구 raw 에 소급할 때). 어느 쪽이든 적재는 멱등이다.
    parser.add_argument("--input-run-id", default=None,
                        help="normalize-* 대상 수집 run_id(미지정=전체 raw 백필). 적재는 멱등")
    # 벤더 선택 — 가격/재무 스텝에서 의미가 있다(미지정=fmp, 기존 동작 보존).
    parser.add_argument("--source", default=None, help="소스 벤더(뉴스: fmp|bigkinds, 가격: fmp|kis, 재무: fmp|dart). 미지정=fmp")
    # 태깅 전용 — 이번 런에서 새로 LLM 을 부를 기사 수 상한(이미 태깅된 건 안 셈). 비용이 호출
    # 수에 비례해서 실수로 큰 금액이 나가는 걸 호출부가 막을 수 있게 둔다. 미지정=대상 전부.
    parser.add_argument("--limit", type=int, default=None,
                        help="tag-news: 이번 런에서 새로 태깅할 기사 수 상한(미지정=전부)")
    parser.add_argument("--window-days", type=int, default=None,
                        help="tag-news·assemble-events·load-disclosure: 대상 파티션을 오늘−N일 창으로"
                             " 제한(미지정: tag-news·load-disclosure=풀스캔, assemble-events=오늘"
                             " 하루). --from/--to 가 우선")
    # iNAV 전용 — 표본 간격(초). 응답이 30행 고정이라 조회 창 = 이 값 × 30 이다(간격을 줄이면
    # 창도 같이 줄어든다). 갱신 주기가 30초 이하인 것까지만 실측됐고 그보다 잘게 의미가 있는지는
    # 미확정이라, 장중에 값을 바꿔가며 재보는 수단으로 플래그를 둔다(ALPHA-556 열린 결정).
    parser.add_argument("--interval-sec", type=int, default=None,
                        help=f"ingest-raw-inav: 표본 간격 초(미지정={DEFAULT_INTERVAL_SEC}). 조회 창 = 간격 × 30")
    parser.add_argument("--max-ticks", type=int, default=None,
                        help="relay·price-worker: 이 횟수만큼만 돌고 종료(미지정=상주). 로컬 확인·일회성 배출용. "
                             "dlq-reconcile: 대사 회차 상한(미지정=보이는 메시지를 다 훑을 때까지)")
    parser.add_argument("--kind", default=None, choices=["news", "price"],
                        help="redrive: 되살릴 job 의 종류(news=news_extraction_job, price=price_window_job)")
    parser.add_argument("--destination", default=None,
                        help="redrive: 새 delivery 를 실을 큐(미지정=직전 event 값 복사). "
                             "배선이 어긋난 채 커밋된 행은 그 값이 컬럼에 박혀 있어 "
                             "여기서 바로잡지 않으면 복구 경로가 없다")
    parser.add_argument("--dataset", default=None,
                        help="세션 dataset. plan-minute-session 은 "
                             # 어휘를 산문으로 베끼지 않는다 — 표(states.SOURCE_GROUPS_BY_
                             # DATASET)가 SSOT 라 dataset 이 늘 때 여기가 조용히 낡는다
                             f"{'|'.join(sorted(MINUTE_DATASETS))}, "
                             "start/stop-minute-session 은 **price_minute 만**"
                             "(공용 서비스 목록을 스케일하므로 구동 레인만 인자가 된다 — "
                             "states.SCALED_DATASETS. news_minute·etf_inav_minute 은 "
                             "자기 워커를 소유해도 인자가 아니라 env 토글로 켜지는 "
                             "승객이다 — session_ops.PASSENGER_LANES), "
                             "rollup-minute-session 도 **price_minute 만**"
                             "(5분 파생은 가격 분봉 canonical 전용 경로다)")
    parser.add_argument("--source-group", default=None,
                        # `--dataset` 과 같은 이유로 표에서 조립한다 — 산문 예시는 dataset 이
                        # 늘 때 낡고, 그게 곧 "어휘 안에서만 받는다"는 안내를 거짓으로 만든다
                        help="세션 source_group(dataset 의 어휘 안에서만 받는다): "
                             + ", ".join(
                                 f"{dataset}={'|'.join(sorted(groups))}"
                                 for dataset, groups in sorted(SOURCE_GROUPS_BY_DATASET.items())
                             ))
    parser.add_argument("--session-date", default=None,
                        help="plan-minute-session·price-worker·rollup-minute-session: "
                             "세션 날짜 YYYY-MM-DD(price-worker·rollup-minute-session "
                             "미지정=오늘 KST — planner 와 같은 값이어야 같은 session_id "
                             "가 유도된다)")
    parser.add_argument("--universe", default=None,
                        help="plan-minute-session·price-worker: 가격 세션의 universe JSON "
                             "경로(둘 다 필수·**같은 파일**). window 범위와 universe_hash 가 "
                             "여기서 나온다 — 갈리면 Worker 가 처리를 거부한다")
    parser.add_argument("--session-id", default=None,
                        help="qc-minute-session·drain-minute-session: 대상 1분 세션(필수). "
                             "둘 다 하루 하나를 지목해서 돈다 — 범위를 열어 두면 살아 "
                             "있는 세션까지 확정하거나 drain 을 건다")
    parser.add_argument("--reason", default=None,
                        help="redrive: 왜 되살리는지(필수). 대체되는 delivery event 행에 "
                             "실행자와 함께 기록된다 — 수동 개입의 유일한 감사 근거다")
    parser.add_argument("--job-id", default=None,
                        help="redrive: 되살릴 job_id(결정적 ID). 대상은 **막힌 것**이다 — "
                             "DEAD job 이거나 Relay 가 발행 불가로 격리한 DEAD delivery event. "
                             "정상 진행 중이거나 SUCCEEDED 인 job 은 거부된다")
    parser.add_argument("--deadline-sec", type=float, default=None,
                        help="수집 루프의 벽시계 상한 초(미지정=무제한). 상한에 닿으면 남은 대상을 "
                             "미시도로 기록하고 **받은 것은 저장한 뒤** 조기 마감한다.")
    args = parser.parse_args(argv)

    # `--deadline-sec` 를 소비하는 스텝에서만 받는다. 조용히 무시하면 운영자가 상한이 걸렸다고
    # 오인하고, SFN 배선 오류(엉뚱한 브랜치에 상한을 준 것)도 안 드러난다(Rule 12).
    if args.deadline_sec is not None:
        # NaN 은 `<= 0` 도 `경과 >= nan` 도 False 라 **상한이 통째로 사라진다** — 값이 있으나
        # 마나인 상태가 가장 나쁘다(있다고 믿는데 안 걸린다).
        if not math.isfinite(args.deadline_sec) or args.deadline_sec <= 0:
            raise SystemExit(f"--deadline-sec 은 0 보다 큰 유한한 값이어야 한다: {args.deadline_sec}")
        if not (args.step == "ingest-raw-etf" and args.source == "krx"):
            raise SystemExit(
                "--deadline-sec 은 현재 `ingest-raw-etf --source krx` 에서만 쓴다 — "
                f"이 조합(step={args.step}, source={args.source})에서는 무시되므로 거부한다"
            )

    if args.max_ticks is not None:
        if args.step not in ("relay", "dlq-reconcile", "price-worker", "price-consumer",
                             "news-consumer", "news-worker", "inav-worker",
                             "disclosure-worker", "sector-index-worker"):
            raise SystemExit(
                "--max-ticks 는 relay·dlq-reconcile·price-worker·price-consumer·"
                "news-consumer·news-worker·inav-worker·disclosure-worker·"
                f"sector-index-worker 에서만 쓴다 — "
                f"이 스텝({args.step})에서는 무시되므로 거부한다"
            )
        if args.max_ticks < 1:
            raise SystemExit(f"--max-ticks 는 1 이상이어야 한다: {args.max_ticks}")

    # redrive 는 되돌리기 어려운 상태 전이(DEAD→RETRY_WAIT + 새 delivery event)라
    # 대상을 추측하지 않는다. 다른 스텝에서 주어지면 조용히 무시하지 말고 거부한다
    # (--deadline-sec·--window-days 와 같은 이유 — 배선 오류가 안 드러난다).
    if args.step == "redrive":
        if not args.kind or not args.job_id or not (args.reason or "").strip():
            raise SystemExit("redrive 는 --kind·--job-id·--reason 이 모두 필요하다")
    elif (args.kind is not None or args.job_id is not None
          or args.reason is not None or args.destination is not None):
        raise SystemExit(
            "--kind·--job-id·--reason·--destination 은 redrive 에서만 쓴다 — "
            f"이 스텝({args.step})에서는 무시되므로 거부한다"
        )

    # QC 전용 인자도 같은 규약이다 — 오배선(`relay --session-id …`)이 조용히 무시되면
    # 운영자는 명령이 먹은 줄 안다.
    # ⚠️ `--session-id` 결손은 여기서 막지 **않는다** — SystemExit 은 프로세스 1 로 나가는데,
    # QC 의 1 은 "원장이 스스로와 모순"이라는 뜻이다. 결손은 판정 불가(2)라 CLI 가 처리한다.
    if args.step not in ("qc-minute-session", "drain-minute-session") \
            and args.session_id is not None:
        raise SystemExit(
            "--session-id 는 qc-minute-session·drain-minute-session 에서만 쓴다 — "
            f"이 스텝({args.step})에서는 무시되므로 거부한다"
        )
    if args.step not in ("plan-minute-session", "start-minute-session",
                         "stop-minute-session", "rollup-minute-session") and (
        args.dataset is not None or args.source_group is not None
    ):
        raise SystemExit(
            "--dataset·--source-group 은 plan-minute-session·start-minute-session·"
            f"stop-minute-session·rollup-minute-session 에서만 쓴다 — "
            f"이 스텝({args.step})에서는 무시되므로 거부한다"
        )
    if args.step in ("price-consumer", "start-minute-session") and args.session_date is not None:
        raise SystemExit(
            f"--session-date 는 {args.step} 에서 쓰지 않는다 — 대상 세션은 job payload"
            "(price-consumer)·오늘 KST(start-minute-session)가 정한다(무시되므로 거부)"
        )
    if args.step == "news-worker" and args.universe is not None:
        raise SystemExit(
            "--universe 는 news-worker 에서 쓰지 않는다 — 뉴스 세션은 소스 단위라 "
            "universe 가 없다(무시되므로 거부)"
        )
    if args.step == "disclosure-worker" and args.universe is not None:
        # 공시도 소스 단위다 — 유니버스는 기대 집합이 아니라 목록 **필터**이고, 그 정본은
        # canonical holdings 파생이라(`universe_from_holdings`) 파일 인자가 아니다.
        raise SystemExit(
            "--universe 는 disclosure-worker 에서 쓰지 않는다 — 공시 세션은 소스 단위이고 "
            "수집 유니버스는 canonical holdings 에서 파생된다(무시되므로 거부)"
        )
    if args.step == "sector-index-worker" and args.universe is not None:
        # 업종지수도 universe 밖이다 — 기대 집합 45종은 config 정본
        # (`[minute_sector_index.index_map]`)이고, universe.json 은 지수를 아예 모른다
        # (ETF 명부에도 구성종목에도 없다). planner 도 같은 조건으로 거부한다.
        raise SystemExit(
            "--universe 는 sector-index-worker 에서 쓰지 않는다 — 기대 집합은 config 의 "
            "index_map 이 준다(무시되므로 거부)"
        )
    if args.step == "rollup-minute-session" and args.universe is not None:
        # 받아서 무시하면 "계획을 이 파일로 다시 세운다"는 오해를 판다 — 배치는 계획을
        # 원장에서 읽는다(마감 후의 universe 파일은 그날 계획과 갈릴 수 있다).
        raise SystemExit(
            "--universe 는 rollup-minute-session 에서 쓰지 않는다 — 계획·커밋은 원장에서 "
            "읽는다(무시되므로 거부)"
        )
    if args.step not in ("plan-minute-session", "price-worker", "price-consumer",
                         "start-minute-session", "news-worker",
                         "rollup-minute-session", "inav-worker",
                         "disclosure-worker", "sector-index-worker") and (
        args.session_date is not None or args.universe is not None
    ):
        raise SystemExit(
            "--session-date·--universe 는 plan-minute-session·price-worker·"
            "price-consumer·start-minute-session·news-worker·rollup-minute-session·"
            "inav-worker·disclosure-worker·sector-index-worker 에서만 쓴다 — "
            f"이 스텝({args.step})에서는 무시되므로 거부한다"
        )

    # `--window-days` 도 소비하는 스텝에서만 받는다(--deadline-sec 과 같은 이유 — 조용히
    # 무시하면 창이 걸렸다고 오인하고 SFN 배선 오류도 안 드러난다, Rule 12).
    if args.window_days is not None:
        if args.step not in ("tag-news", "assemble-events", "load-disclosure"):
            raise SystemExit(
                "--window-days 는 tag-news·assemble-events·load-disclosure 에서만 쓴다 — "
                f"이 스텝({args.step})에서는 무시되므로 거부한다"
            )
        # 음수 창은 역전 창(오늘+N, 오늘)이 되어 전 파티션을 제외한다 — 0건 처리를 exit 0
        # 성공으로 위장하므로, 언제 주어지든(명시 --from/--to 와 함께라 무시될 때조차) 거부.
        if args.window_days < 0:
            raise SystemExit(f"--window-days 는 음수일 수 없다: {args.window_days}")
        # 상한: 창은 최근 파티션 소급 폭이다 — 비상식 값(예 800000)은 date 연산 하한을 넘겨
        # collection_log 도 못 남기고 크래시한다(OverflowError). 과거 전체가 필요하면 창을
        # 키우는 게 아니라 미지정 풀스캔(tag-news)·--from/--to 백필(assemble)이 그 경로다.
        if args.window_days > 3650:
            raise SystemExit(f"--window-days 가 소급 상한(3650일)을 넘는다: {args.window_days}")

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    settings = load_settings(args.config)
    storage = make_storage(settings.storage)
    run_id = args.run_id or make_run_id()

    # 운영 원장(ALPHA-530) — storage·수집창과 무관하게 먼저 분기한다. plan-run 은 실행 전
    # pipeline_run+expected_task 를 남기고 SFN 을 시작하고(Planner), reconcile 은 예정↔실제를
    # 대조한다. 둘 다 원장 DB 필수이며, ledger 미설정 환경에선 각 핸들러가 fail-loud 한다.
    if args.step == "plan-run":
        return ops_entry.plan_run_cli(settings)
    if args.step == "reconcile":
        return ops_entry.reconcile_cli(settings)
    if args.step == "relay":
        return relay_cli(settings, max_ticks=args.max_ticks)
    if args.step == "dlq-reconcile":
        return dlq_reconcile_cli(settings, max_ticks=args.max_ticks)
    if args.step == "qc-minute-session":
        return qc_session_cli(settings, session_id=args.session_id)
    if args.step == "rollup-minute-session":
        return rollup_session_cli(settings, dataset=args.dataset,
                                  source_group=args.source_group,
                                  session_date=args.session_date)
    if args.step == "plan-minute-session":
        return plan_session_cli(
            settings, dataset=args.dataset, source_group=args.source_group,
            session_date=args.session_date, universe=args.universe,
        )
    if args.step == "drain-minute-session":
        return drain_session_cli(settings, session_id=args.session_id)
    if args.step == "start-minute-session":
        return start_session_cli(settings, dataset=args.dataset,
                                 source_group=args.source_group, universe=args.universe)
    if args.step == "stop-minute-session":
        return stop_session_cli(settings, dataset=args.dataset,
                                source_group=args.source_group)
    if args.step == "price-worker":
        return price_worker_cli(settings, session_date=args.session_date,
                                universe=args.universe, max_ticks=args.max_ticks)
    if args.step == "inav-worker":
        return inav_worker_cli(settings, session_date=args.session_date,
                               universe=args.universe, max_ticks=args.max_ticks)
    if args.step == "price-consumer":
        return price_consumer_cli(settings, universe=args.universe,
                                  max_ticks=args.max_ticks)
    if args.step == "news-consumer":
        return news_consumer_cli(settings, max_ticks=args.max_ticks)
    if args.step == "news-worker":
        return news_worker_cli(settings, session_date=args.session_date,
                               max_ticks=args.max_ticks)
    if args.step == "disclosure-worker":
        return disclosure_worker_cli(settings, session_date=args.session_date,
                                     max_ticks=args.max_ticks)
    if args.step == "sector-index-worker":
        return sector_index_worker_cli(settings, session_date=args.session_date,
                                       max_ticks=args.max_ticks)
    if args.step == "redrive":
        return redrive_cli(settings, kind=args.kind, job_id=args.job_id,
                           reason=args.reason, destination=args.destination)

    # 원장 계측은 **dispatch 를 한 번** 감싼다(ALPHA-181). 스텝마다 흩뿌리면 배선 지점이
    # 33개가 되고, 그중 4곳은 `--source` 로 벤더가 갈려 오라벨 지점이 그만큼 늘어난다
    # (원장이 남의 벤더 결과를 그 작업으로 기록한다). 정체성 해소는 task_key_for 한 곳이다.
    task_key = ops_entry.task_key_for(args.step, args.source)
    if task_key is None:
        return _dispatch(args, settings, storage, run_id)   # 미등록 작업 — 계측 없이 그대로
    return ops_entry.instrument(
        settings, storage, task_key, run_id,
        lambda: _dispatch(args, settings, storage, run_id),
    )


def _dispatch(args, settings, storage, run_id) -> int:
    """스텝 하나를 실행해 exit code 를 낸다. 계측은 호출부(main)가 감싼다."""
    # 정제(normalize-price)는 raw 를 읽는 스텝이라 수집 날짜창·소스 벤더가 없다 — 먼저 분기한다.
    # 벤더는 raw 키의 source= 로 판별하고, 대상 범위는 --input-run-id 로만 좁힌다(미지정=전체).
    if args.step == "normalize-price":
        return normalize_price.run(storage, run_id, args.input_run_id)
    # 투자자 수급 정제도 raw 만 읽는 스텝이라 수집 창·벤더 인자가 없다 — 벤더는 raw 키의
    # source= 가 규정하고(현재 kis), 시간축은 레코드의 거래일(stck_bsop_date)이 준다.
    if args.step == "normalize-investor":
        return normalize_investor.run(storage, run_id, args.input_run_id)
    # 장중 투자자 추정 정제(ALPHA-768)도 raw 만 읽는다 — EOD 와 **다른 데이터셋**이라 스텝이
    # 따로다(정체성 키에 슬롯이 붙어 병합 규칙이 다르다). --input-run-id 는 한 슬롯 런의 raw 만
    # 좁히는 축이고, 미지정이면 전체를 다시 훑어도 멱등이라 결과가 같다.
    if args.step == "normalize-investor-estimate":
        return normalize_investor_estimate.run(storage, run_id, args.input_run_id)
    # 뉴스 정제도 raw 를 읽는 스텝이라 수집 날짜창·소스 벤더가 없다 — 벤더는 raw 키의
    # source= 로 판별하고, 대상 범위는 --input-run-id 로만 좁힌다(미지정=전체).
    if args.step == "normalize-news":
        return normalize_news.run(storage, run_id, args.input_run_id,
                                  expected_etfs=ingest_price_raw._krx_expected_etfs(settings))
    # 공시 정제도 raw 를 읽는 스텝이라 수집 날짜창·소스 벤더가 없다 — 벤더는 raw 키의
    # source= 로 판별하고, 대상 범위는 --input-run-id 로만 좁힌다(미지정=전체).
    if args.step == "normalize-disclosure":
        return normalize_disclosure.run(storage, run_id, args.input_run_id)
    if args.step == "normalize-disclosure-segment":
        return normalize_disclosure_segment.run(storage, run_id, args.input_run_id)
    # ETF 구성종목 정제도 raw 를 읽는 스텝이라 수집 날짜창·소스 벤더가 없다 — 벤더는 raw 키의
    # source= 로 판별하고(fmp=US·krx=KR), 대상 범위는 --input-run-id 로만 좁힌다(미지정=전체).
    if args.step == "normalize-etf":
        return normalize_etf.run(storage, run_id, args.input_run_id)
    # NAV 정제도 raw 를 읽는 스텝이라 수집 창·벤더 인자가 없다 — 벤더는 raw 키의 source= 가
    # 규정하고, 시간축은 레코드의 거래일(stck_bsop_date)이 준다.
    if args.step == "normalize-etf-nav":
        return normalize_etf_nav.run(storage, run_id, args.input_run_id)
    # ETF 프로필 정제도 raw 만 읽는다 — 마스터(entity·instrument)의 재료를 만든다(ALPHA-462).
    if args.step == "normalize-etf-profile":
        return normalize_etf_profile.run(storage, run_id, args.input_run_id)

    if args.step == "normalize-instrument-profile":
        return normalize_instrument_profile.run(storage, run_id, args.input_run_id)

    # 적재(load-*)는 canonical 을 읽어 **DB 에 쓰는** 스텝이라 수집 창·벤더가 없다. DB 설정이
    # 없으면 조용히 0건 적재하고 성공으로 끝나지 않게 여기서 fail-loud 한다(Rule 12).
    if args.step == "load-instruments":
        # ⚠️ `_krx_expected_etfs` 는 **KR 전용 정본**(krx_etf.source.etf_map)이다. 이 스텝은
        # LOADED_MARKETS 를 순회하는데 오늘 그 값이 ("KR",)라 맞아떨어진다 — US 를 더하면
        # US 구성종목이 전량 뿌리 밖으로 빠진다(failed_records 에도 안 잡혀 조용하다).
        # 시장을 늘릴 때 이 인자도 시장별로 갈라야 한다. load-etf-holdings 도 같다.
        return load_instruments.run(storage, run_id, db=db_config_from_env(settings.db),
                                    expected_etfs=ingest_price_raw._krx_expected_etfs(settings))

    # corp_code enrichment 은 DB(미충전 KR 회사)를 읽고 OpenDART corpCode.xml 로 채운다 —
    # 마스터 적재도 공시 적재도 아닌 별개 관심사라 별도 스텝(ALPHA-491). db + DART 소스 둘 다 필요.
    if args.step == "enrich-corp-code":
        if settings.dart_disclosure is None:
            raise SystemExit("dart_disclosure.source 설정이 없다 — sources.toml 확인")
        corp_source = DartDisclosureSource(settings.dart_disclosure.source, PoliteClient())
        return enrich_corp_code.run(
            storage, run_id, db=db_config_from_env(settings.db), source=corp_source
        )

    # 문서 마스터도 canonical 을 읽어 DB 에 쓰는 적재 스텝이다. 창(--from/--to)은 수집 창이
    # 아니라 **적재 대상 published_date 파티션**을 좁힌다(미지정=전체, 멱등 skip 이라 재실행
    # 비용은 신규분뿐) — 그래서 아래 증분 기본창 계산을 타지 않게 여기서 분기한다.
    if args.step == "load-documents":
        return load_documents.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # 공시 적재도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (canonical report_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    #
    # `--window-days` 를 받는 유일한 적재 스텝이다(ALPHA-721). 형제 로더들은 하루 1회만 돌아
    # 전체 스캔을 견디지만, 공시는 장중 레인이 붙으면 슬롯마다 그 스캔이 곱해진다
    # (news-load-fullscan-problem 과 같은 축). ASL 은 날짜 연산을 못 해 `--from/--to` 를
    # 만들 수 없으므로 창 **폭**을 받아 여기서 날짜로 편다 — tag-news·assemble-events 와 같은
    # 패턴이고, 명시 `--from/--to` 가 주어지면 그쪽이 이긴다(백필 경로 보존).
    if args.step == "load-disclosure":
        load_from, load_to = args.from_date, args.to_date
        if load_from is None and load_to is None and args.window_days is not None:
            load_from, load_to = default_window(
                datetime.now(window_calendar_tz(args.step, args.source)), args.window_days)
        return load_disclosure.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=load_from, to_date=load_to,
        )

    # 보관 raw 재파싱 백필 — 창 미지정은 전체, 명시 창은 DART 접수일 inclusive 범위다.
    # forward와 같은 normalize/load/assemble 함수를 순서대로 호출한다.
    if args.step == "backfill-normalize-disclosure":
        return backfill_disclosure.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # 가격 적재도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (canonical trade_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    if args.step == "load-price-daily":
        return load_price_daily.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # NAV 적재도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (canonical trade_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    if args.step == "load-etf-nav":
        return load_etf_nav.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # ETF 구성종목 적재도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (canonical as_of_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    if args.step == "load-etf-holdings":
        return load_etf_holdings.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
            expected_etfs=ingest_price_raw._krx_expected_etfs(settings),
        )

    # 투자자 수급 적재도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (canonical trade_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    if args.step == "load-etf-flow":
        return load_etf_flow.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # 장중 투자자 추정 적재(ALPHA-768)도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-etf-flow
    # 와 같다(canonical trade_date 파티션 프루닝, 미지정=전체 + 멱등 skip). 창은 **거래일** 축이라
    # 하루를 지정하면 그날의 슬롯 전부가 대상이다(슬롯 단위 창은 없다).
    if args.step == "load-investor-intraday":
        return load_investor_intraday.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # assertion 적재는 feature 를 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (feature published_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    if args.step == "load-assertions":
        return load_assertions.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # 이벤트 조립은 canonical 뉴스를 분류(LLM)해 DB 에 event 계보를 쓴다(ALPHA-412).
    # LLM 설정은 tag-news 와 같은 관례(LLM_* env), DB 는 적재 스텝 공통. 창 미지정 =
    # **오늘(KST) 하루**(분류 비용이 기사 수 비례 — 전체 스캔 기본이 아니다).
    if args.step == "assemble-events":
        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            # 조용히 0건 조립하고 성공으로 끝나면 이벤트 부재가 안 보인다(Rule 12).
            raise SystemExit("LLM_API_KEY 가 없다 — assemble-events 는 분류 LLM 호출이 필수다")
        complete_fn = openai_compatible_complete_fn(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        )
        # 분류 LLM 병렬도도 tag-news 와 같은 LLM_CONCURRENCY env(미지정=기본, 상한은 스텝이 클램프).
        concurrency = int(os.environ.get("LLM_CONCURRENCY", assemble_events.DEFAULT_CLASSIFY_CONCURRENCY))
        return assemble_events.run(
            storage, run_id, db=db_config_from_env(settings.db), complete_fn=complete_fn,
            from_date=args.from_date, to_date=args.to_date,
            window_days=args.window_days, concurrency=concurrency,
        )

    # 가격변동 트리거도 canonical 을 읽어 DB 에 쓰는 적재 스텝이다. 창(--from/--to)은 수집
    # 창이 아니라 **트리거 계산 대상 trade_date 파티션**을 좁힌다(미지정=전체, 멱등 skip 이라
    # 재실행 비용은 신규분뿐) — 그래서 아래 증분 기본창 계산을 타지 않게 여기서 분기한다.
    if args.step == "load-price-triggers":
        if settings.price_triggers is None:
            # 조용히 0건 성공으로 끝나면 트리거 부재가 안 보인다(Rule 12).
            raise SystemExit("price_triggers 설정이 없다 — sources.toml 확인")
        return load_price_triggers.run(
            storage, run_id, db=db_config_from_env(settings.db),
            config=settings.price_triggers, from_date=args.from_date, to_date=args.to_date,
            expected_etfs=ingest_price_raw._krx_expected_etfs(settings),
        )

    # 태깅은 canonical 을 읽는 스텝이라 수집 날짜창의 의미가 다르다 — raw 를 가져올 창이 아니라
    # **태깅 대상 published_date 파티션**을 좁히는 창이다(미지정=풀스캔). 그래서 아래 증분 기본창
    # (어제~오늘) 자동 계산을 타지 않도록 여기서 분기한다 — 태깅에 기본창을 조용히 씌우면 과거
    # 기사가 영영 태깅되지 않는다(풀스캔이 곧 백로그·정정본 회수 경로다).
    # 대신 일일 SFN 경로는 --window-days 로 창을 **명시 opt-in** 한다: read=O(전체 코퍼스) 스캔이
    # 런타임을 지배하므로(LLM 아님) 창이 곧 속도다. 창 밖 회수는 넓은 창(N일=한 날짜가 N+1회
    # 스캔돼 일시적 llm_error 자가 회복) + 필요 시 풀스캔 수동 실행이 맡는다(ALPHA-540).
    if args.step == "tag-news":
        # LLM 설정은 **여기서** env 로 읽는다. llm.py 는 인자만 받고 env 를 모른다 —
        # DATA_PIPELINE_* 는 수집 설정(load_settings) 네임스페이스인데 LLM 은 수집 소스가
        # 아니라 거기 안 든다. 관례는 analysis-engine analyze_daily.py 와 같은 LLM_* 키다.
        api_key = os.environ.get("LLM_API_KEY", "")
        if not api_key:
            # 조용히 0건 태깅하고 성공으로 끝나면 커버리지 저하가 안 보인다(Rule 12).
            raise SystemExit("LLM_API_KEY 가 없다 — tag-news 는 LLM 호출이 필수다")
        complete_fn = openai_compatible_complete_fn(
            api_key=api_key,
            base_url=os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
            model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        )
        # LLM 호출 병렬도도 LLM_* env 관례로 받는다(미지정=기본, 상한은 tag_news 가 클램프).
        concurrency = int(os.environ.get("LLM_CONCURRENCY", tag_news.DEFAULT_TAG_CONCURRENCY))
        # 음수·비소비 스텝 거부는 파싱 직후의 공통 가드가 맡는다(assemble-events 와 공유).
        # 명시 --from/--to 가 최우선(백필). 없고 --window-days 만 있으면 오늘−N일 창으로 좁힌다.
        from_date, to_date = args.from_date, args.to_date
        if from_date is None and to_date is None and args.window_days is not None:
            from_date, to_date = default_window(
                datetime.now(window_calendar_tz(args.step, args.source)), args.window_days)
        return tag_news.run(storage, run_id, complete_fn=complete_fn,
                            from_date=from_date, to_date=to_date, limit=args.limit,
                            concurrency=concurrency)

    # 재무제표는 point-in-time 폴링이라 날짜창을 쓰지 않는다 — 먼저 분기해 창 계산을 건너뛴다.
    if args.step == "ingest-raw-financial":
        vendor = args.source or "fmp"
        if vendor == "fmp":
            if settings.financial is None:
                # 재무 섹션 미설정은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
                raise SystemExit("financial.source 설정이 없다 — sources.toml 확인")
            source = FmpFinancialSource(settings.financial.source, PoliteClient())
        elif vendor == "dart":
            if settings.dart_financial is None:
                raise SystemExit("dart_financial.source 설정이 없다 — sources.toml 확인")
            source = DartFinancialSource(settings.dart_financial.source, PoliteClient())
        else:
            raise SystemExit(f"알 수 없는 --source: {vendor} (fmp|dart)")
        return ingest_raw_financial.run(settings, storage, source, run_id)

    # ETF 구성종목도 스냅샷(현재 holdings)이라 날짜창을 쓰지 않는다 — 재무와 함께 먼저
    # 분기해 창 계산을 건너뛴다. US=FMP(미지정=fmp), KR=KRX 로그인 게이트 PDF(--source krx).
    if args.step == "ingest-raw-etf":
        vendor = args.source or "fmp"
        if vendor == "fmp":
            if settings.etf is None:
                # 섹션 미설정은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
                raise SystemExit("etf.source 설정이 없다 — sources.toml 확인")
            etf_source = FmpEtfSource(settings.etf.source, PoliteClient())
        elif vendor == "krx":
            if settings.krx_etf is None:
                raise SystemExit("krx_etf.source 설정이 없다 — sources.toml 확인")
            etf_source = KrxEtfSource(
                settings.krx_etf.source,
                PoliteClient(timeout=KRX_ETF_TIMEOUT_SEC),
                deadline_sec=args.deadline_sec,
            )
        else:
            raise SystemExit(f"알 수 없는 --source: {vendor} (fmp|krx)")
        return ingest_raw_etf.run(settings, storage, etf_source, run_id)

    # ETF 프로필도 스냅샷(현재 상품정보)이라 날짜창을 쓰지 않는다. 유니버스는 NAV 와 같은
    # krx_etf.source.etf_map 이다 — 마스터·NAV·구성종목이 다른 목록을 보면 안 된다.
    if args.step == "ingest-raw-etf-profile":
        if settings.kis_nav is None:
            raise SystemExit("kis_nav.source 설정이 없다 — sources.toml 확인")
        if settings.krx_etf is None:
            raise SystemExit("krx_etf.source 설정이 없다(프로필 유니버스 출처) — sources.toml 확인")
        profile_source = KisEtfProfileSource(
            settings.kis_nav.source,
            settings.krx_etf.source.etf_map,
            PoliteClient(min_interval=KIS_MIN_INTERVAL_SEC),
        )
        return ingest_raw_etf.run(
            settings, storage, profile_source, run_id,
            dataset="etf_profile", partition=raw_etf_profile_partition,
            job_name="ingest_raw_etf_profile",
        )

    if args.step == "ingest-raw-instrument":
        # KRX 공식 OpenAPI 종목기본정보(ALPHA-829). `ingest-raw-etf --source krx` 와 **자격증명이
        # 다르다** — 저쪽은 계정 로그인(mbr_id/pw), 이쪽은 AUTH_KEY 헤더다. 설정이 없으면
        # 조용히 0건 수집하고 성공으로 끝나지 않게 fail-loud 한다(Rule 12).
        #
        # 날짜창을 **거부한다**: 이 API 는 기준일 하나만 받고 그 값은 달력이 정한다(당일
        # 조회가 막혀 있어 직전 거래일). 무시하고 돌면 과거를 메우려던 운영자가 직전 거래일
        # 스냅샷을 받고 exit 0 을 보게 되고, 소급된 줄 착각한다 —
        # `ingest-raw-investor-estimate` 와 같은 성질·같은 처방이다(Rule 7: 두 선례 중
        # 거부하는 쪽을 택했다. 무시하는 `ingest-raw-etf-profile` 은 창 개념 자체가 없는
        # 프로필 조회라 착각할 소급이 없다).
        # `or` 가 아니라 `is not None` 이다 — 운영 스크립트가 unset 변수를 `--from "$FROM"`
        # 으로 넘기면 빈 문자열이 되고, falsy 검사면 명시적으로 창을 준 실행이 가드를 지난다.
        if args.from_date is not None or args.to_date is not None:
            raise SystemExit(
                "ingest-raw-instrument 는 --from/--to 를 쓸 수 없다 — 이 API 는 기준일 하나만 "
                "받고 그 값은 직전 거래일로 고정이다(당일·미래 조회 불가). 과거 기준일 백필이 "
                "필요하면 별도 티켓으로 소급 인자를 설계한다."
            )
        if settings.krx_instrument is None:
            raise SystemExit("krx_instrument.source 설정이 없다 — sources.toml 확인")
        return ingest_raw_instrument.run(
            storage, KrxInstrumentSource(settings.krx_instrument.source), run_id)

    if args.step == "ingest-raw-investor-estimate":
        # 장중 투자자 추정(ALPHA-767) — EOD 확정(`ingest-raw-investor`)과 **별개 데이터셋**이다.
        # 날짜창 블록 **앞**에 둔다: 이 API 는 날짜 파라미터가 없어 창을 계산할 이유가 없다
        # (ETF 프로필 분기와 같은 자리·같은 이유).
        #
        # 창을 준 실행은 **거부한다** — 무시하고 돌면 갭을 메우려던 운영자가 오늘치를 받고
        # exit 0 을 보게 되고, 소급이 영구 불가한 구간을 복구한 줄 착각한다(iNAV 와 같은
        # 성질·같은 처방, Rule 12).
        # `or` 가 아니라 `is not None` 이다 — 운영 스크립트가 unset 변수를 `--from "$FROM"`
        # 으로 넘기면 빈 문자열이 되고, falsy 검사면 **명시적으로 창을 준 실행이 가드를 지나
        # 오늘치를 받고 exit 0** 이 된다(소급된 줄 착각). 값의 유무로 판정한다(Rule 12).
        if args.from_date is not None or args.to_date is not None:
            raise SystemExit(
                "ingest-raw-investor-estimate 는 --from/--to 를 쓸 수 없다 — 이 API 는 날짜 "
                "지정이 없고 오늘치 장중 추정만 준다(소급 백필 불가). 갭은 폴링 슬롯으로만 막는다."
            )
        # 자격증명·유니버스를 EOD 와 **공유**한다(같은 KIS 앱키·같은 구성종목 축) — iNAV 가
        # kis_nav 섹션을 재사용하는 선례와 같다. 별도 섹션을 두면 같은 비밀값을 두 env 로
        # 주입해야 해서, 한쪽만 주입된 배포에서 이 소스가 조용히 비활성된다.
        if settings.kis_investor is None:
            raise SystemExit("kis_investor.source 설정이 없다 — sources.toml 확인")
        estimate_source = KisInvestorEstimateSource(
            settings.kis_investor.source, PoliteClient(min_interval=KIS_MIN_INTERVAL_SEC)
        )
        return ingest_raw_investor.run(
            settings, storage, estimate_source, run_id,
            dataset="investor_flow_intraday", partition=raw_investor_estimate_partition,
            job_name="ingest_raw_investor_estimate",
        )

    # 창 미지정 = 스케줄 증분 → 앱이 어제~오늘로 채운다. 하나라도 지정하면 그대로 존중(백필).
    # 소급 일수는 스텝별로 다르다(가격 EOD 는 주말·공휴일 공백 때문에 더 넉넉히).
    from_date, to_date = args.from_date, args.to_date
    if from_date is None and to_date is None:
        # 가격·투자자 수급은 소급을 넉넉히 둔다 — 주말·공휴일엔 EOD 가 없어 소급 1일이면 월요일
        # 런이 직전 거래일(금요일)을 놓친다. 투자자 소스는 창 하한으로 필터하므로(over-fetch 방지)
        # 30일 반환에 기대던 갭 커버가 사라져 가격과 같은 소급이 필요하다.
        lookback = (
            DEFAULT_PRICE_LOOKBACK_DAYS
            if args.step in ("ingest-price-raw", "ingest-raw-investor")
            else DEFAULT_LOOKBACK_DAYS
        )
        from_date, to_date = default_window(
            datetime.now(window_calendar_tz(args.step, args.source)), lookback)

    if args.step == "ingest-raw":
        vendor = args.source or _VENDOR_SPLIT_STEPS["ingest-raw"]
        if vendor == "fmp":
            fmp_config = settings.news.sources.get("fmp")
            if fmp_config is None:
                # 소스 등록 누락은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
                raise SystemExit("news.sources.fmp 설정이 없다 — sources.toml 확인")
            source = FmpNewsSource(fmp_config, PoliteClient())
        elif vendor == "bigkinds":
            if settings.bigkinds_news is None:
                raise SystemExit("bigkinds_news 설정이 없다 — sources.toml 확인")
            # timeout 45s — 1분 레인이 **같은 엔드포인트**에서 실측한 값과 맞춘다(ALPHA-645,
            # `MinuteNewsWorkerConfig.timeout_sec` 주석: "스파이크에서 단발 read 20초 초과를
            # 실측했다 … 기본 10초는 느린 꼬리를 실패로 접는다"). 배치는 기본 10초를 쓰고
            # 있었는데, 창당 페이지가 40 → 최대 126 으로 늘어 그 꼬리를 밟을 기대 횟수가
            # 3배가 됐다 — 재시도까지 소진되면 런이 partial(exit 1)로 끝나고 그 지점에서
            # 중도 이탈해 절단 판정 자체가 안 돈다.
            source = BigKindsNewsSource(
                settings.bigkinds_news, PoliteClient(min_interval=1.0, timeout=45.0)
            )
        else:
            raise SystemExit(f"알 수 없는 --source: {vendor} (fmp|bigkinds)")
        return ingest_raw.run(settings, storage, source, run_id, from_date, to_date)
    if args.step == "ingest-raw-nav":
        # ETF NAV(ALPHA-380). 수집 유니버스는 krx_etf.source.etf_map 을 공유한다 —
        # 구성종목과 NAV 가 서로 다른 ETF 목록을 보면 안 된다(맵 복제 = 드리프트).
        if settings.kis_nav is None:
            raise SystemExit("kis_nav.source 설정이 없다 — sources.toml 확인")
        if settings.krx_etf is None:
            raise SystemExit("krx_etf.source 설정이 없다(NAV 유니버스 출처) — sources.toml 확인")
        nav_source = KisNavSource(
            settings.kis_nav.source,
            settings.krx_etf.source.etf_map,
            PoliteClient(min_interval=KIS_MIN_INTERVAL_SEC),
            from_date,
            to_date,
        )
        return ingest_raw_etf.run(
            settings, storage, nav_source, run_id,
            dataset="etf_nav", partition=raw_etf_nav_partition, job_name="ingest_raw_nav",
        )
    if args.step == "ingest-raw-inav":
        # 장중 iNAV(ALPHA-555). 일별 NAV 와 같은 자격증명·유니버스를 쓰되 dataset 이 다르다 —
        # 거래일 grain 종가 NAV 와 장중 시각 grain 추정 NAV 는 다른 축이다.
        # 날짜창을 넘기지 않는다: 이 API 는 시각·날짜 지정을 무시하고 항상 최근 30행만 준다.
        # 그래서 창을 준 실행은 **거부한다** — 무시하고 돌면 갭을 메우려던 운영자가 최근 30행을
        # 받고 exit 0 을 보게 되고, 소급이 영구 불가한 구간을 복구한 줄 착각한다(Rule 12).
        if args.from_date or args.to_date:
            raise SystemExit(
                "ingest-raw-inav 는 --from/--to 를 쓸 수 없다 — 이 API 는 날짜·시각 지정을 "
                "무시하고 항상 최근 30행만 준다(소급 백필 불가). 갭은 폴링 창 겹침으로만 막는다."
            )
        if settings.kis_nav is None:
            raise SystemExit("kis_nav.source 설정이 없다 — sources.toml 확인")
        if settings.krx_etf is None:
            raise SystemExit("krx_etf.source 설정이 없다(NAV 유니버스 출처) — sources.toml 확인")
        inav_source = KisInavSource(
            settings.kis_nav.source,
            settings.krx_etf.source.etf_map,
            PoliteClient(min_interval=KIS_MIN_INTERVAL_SEC),
            # `or` 로 쓰면 0 이 falsy 라 조용히 기본값이 된다 — 어댑터의 1 미만 가드를
            # CLI 가 우회해 잘못된 입력이 성공으로 기록된다(Rule 12).
            interval_sec=(
                DEFAULT_INTERVAL_SEC if args.interval_sec is None else args.interval_sec
            ),
        )
        return ingest_raw_etf.run(
            settings, storage, inav_source, run_id,
            dataset="etf_inav", partition=raw_etf_inav_partition, job_name="ingest_raw_inav",
        )
    if args.step == "ingest-price-raw":
        # 가격은 뉴스와 별개 심볼맵을 쓴다 — ADR 의 USD 시세를 KR 종목 가격으로 쓰면
        # 통화·거래시간이 어긋난다(price.source.symbol_map 은 거래소-로컬 심볼만).
        # --source 로 벤더를 고른다(미지정=fmp, 기존 동작 보존; kis=국내 일봉; yahoo=지수).
        vendor = args.source or _VENDOR_SPLIT_STEPS["ingest-price-raw"]
        if vendor == "fmp":
            price_source = FmpPriceSource(settings.price.source, PoliteClient())
        elif vendor == "kis":
            if settings.kis_price is None:
                # 섹션 미설정은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
                raise SystemExit("kis_price.source 설정이 없다 — sources.toml 확인")
            if to_date is not None and from_date is None:
                # KIS inquire-daily 는 FID_INPUT_DATE_1(시작일)이 필수다 — 한쪽만 준 창은
                # 빈 시작일로 전 종목이 KIS 오류가 된다. 무의미한 전량 실패 전에 fail-fast.
                # (증분=둘 다 미지정은 위에서 창을 채웠으므로 이 경로로 오지 않는다.)
                raise SystemExit("KIS 가격은 --from 없이 --to 만 지정할 수 없다 — --from 을 함께 지정")
            price_source = KisDailyPriceSource(
                settings.kis_price.source, PoliteClient(min_interval=KIS_MIN_INTERVAL_SEC)
            )
        elif vendor == "yahoo":
            if settings.yahoo_price is None:
                # 섹션 미설정은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
                raise SystemExit("yahoo_price.source 설정이 없다 — sources.toml 확인")
            # 인증이 없어 PoliteClient 도 안 받는다 — yfinance 가 자체 HTTP 를 쓴다.
            price_source = YahooPriceSource(settings.yahoo_price.source)
        else:
            raise SystemExit(f"알 수 없는 --source: {vendor} (fmp|kis|yahoo)")
        return ingest_price_raw.run(settings, storage, price_source, run_id, from_date, to_date)
    if args.step == "ingest-raw-investor":
        # 종목별 투자자 수급(ALPHA-482). KR·KIS 단일 벤더라 --source 분기가 없다. 수집
        # 유니버스는 canonical KR holdings 에서 파생한다(가격과 같은 축, universe_from_holdings).
        if settings.kis_investor is None:
            # 섹션 미설정은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
            raise SystemExit("kis_investor.source 설정이 없다 — sources.toml 확인")
        if to_date is not None and from_date is None:
            # KIS 투자자 수급은 FID_INPUT_DATE_1(창 끝)이 필수라 --to 만으론 창 하한이 없어
            # 무한정 과거로 돈다 — 무의미한 전량 페이지네이션 전에 fail-fast(가격과 동형).
            raise SystemExit("KIS 투자자 수급은 --from 없이 --to 만 지정할 수 없다 — --from 을 함께 지정")
        investor_source = KisInvestorSource(
            settings.kis_investor.source, PoliteClient(min_interval=KIS_MIN_INTERVAL_SEC)
        )
        return ingest_raw_investor.run(settings, storage, investor_source, run_id, from_date, to_date)
    if args.step == "ingest-raw-disclosure":
        # 공시는 KR·단일 벤더(OpenDART)라 --source 분기가 없다. 재무(fnlttSinglAcnt)와 별개
        # API·별개 잡이다. 날짜창은 뉴스와 동형(위에서 증분/백필 창을 채웠다).
        if settings.dart_disclosure is None:
            raise SystemExit("dart_disclosure.source 설정이 없다 — sources.toml 확인")
        disclosure_source = DartDisclosureSource(settings.dart_disclosure.source, PoliteClient())
        return ingest_raw_disclosure.run(
            settings, storage, disclosure_source, run_id, from_date, to_date
        )
    raise AssertionError(f"unreachable step: {args.step}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
