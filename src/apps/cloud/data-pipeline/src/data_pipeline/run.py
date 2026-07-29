"""실행 진입점 — ECS RunTask command 또는 로컬에서 호출한다.

    python -m data_pipeline.run
        {ingest-raw|ingest-price-raw|ingest-raw-financial|ingest-raw-disclosure|ingest-raw-etf|ingest-raw-nav|ingest-raw-etf-profile
         |normalize-price|normalize-news|normalize-disclosure|normalize-disclosure-segment
         |normalize-etf|normalize-etf-nav|normalize-etf-profile|tag-news|load-instruments|enrich-corp-code|load-price-triggers|load-documents|load-disclosure|load-etf-nav
         |load-assertions|assemble-events}
        [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--run-id RUN_ID] [--config PATH]
        [--source VENDOR] [--input-run-id RUN_ID] [--limit N] [--window-days N]

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
import os
import sys
from datetime import datetime, timedelta, timezone

from .config import load_settings
from .db import db_config_from_env
from .lake import make_storage, raw_etf_nav_partition, raw_etf_profile_partition
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
    KisInvestorSource,
    KisNavSource,
    KrxEtfSource,
    PoliteClient,
    YahooPriceSource,
)
from .steps import (
    assemble_events,
    enrich_corp_code,
    ingest_price_raw,
    load_assertions,
    load_disclosure,
    load_documents,
    load_etf_flow,
    load_etf_holdings,
    load_etf_nav,
    load_instruments,
    load_price_daily,
    load_price_triggers,
    ingest_raw,
    ingest_raw_disclosure,
    ingest_raw_etf,
    ingest_raw_financial,
    ingest_raw_investor,
    normalize_disclosure,
    normalize_disclosure_segment,
    normalize_etf,
    normalize_etf_nav,
    normalize_etf_profile,
    normalize_investor,
    normalize_news,
    normalize_price,
    tag_news,
)
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


def default_window(now: datetime, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> tuple[str, str]:
    """증분 기본 창(from, to) = (오늘-소급일, 오늘) UTC 날짜."""
    to_date = now.date()
    from_date = to_date - timedelta(days=lookback_days)
    return from_date.isoformat(), to_date.isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-pipeline")
    parser.add_argument(
        "step",
        choices=["ingest-raw", "ingest-price-raw", "ingest-raw-financial",
                 "ingest-raw-disclosure", "ingest-raw-etf", "ingest-raw-nav", "ingest-raw-etf-profile",
                 "ingest-raw-investor",
                 "normalize-price", "normalize-investor",
                 "normalize-news", "normalize-disclosure", "normalize-disclosure-segment",
                 "normalize-etf", "normalize-etf-nav", "normalize-etf-profile", "tag-news", "load-instruments", "enrich-corp-code", "load-price-triggers",
                 "load-price-daily", "load-documents", "load-disclosure", "load-etf-nav", "load-etf-holdings", "load-etf-flow", "load-assertions", "assemble-events",
                 # 운영 원장(ALPHA-530): plan-run=EventBridge→Planner(원장 기록+SFN 시작),
                 # reconcile=주기 대조. 둘 다 원장 DB 필수, storage/수집창과 무관.
                 "plan-run", "reconcile"],
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
                        help="tag-news: 태깅 대상 파티션을 오늘−N일 창으로 제한(미지정=풀스캔). --from/--to 가 우선")
    args = parser.parse_args(argv)

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

    # 정제(normalize-price)는 raw 를 읽는 스텝이라 수집 날짜창·소스 벤더가 없다 — 먼저 분기한다.
    # 벤더는 raw 키의 source= 로 판별하고, 대상 범위는 --input-run-id 로만 좁힌다(미지정=전체).
    # NORMALIZE_PRICE 는 운영 원장 계측 대상(ALPHA-530) — ledger 없으면 투명 통과.
    if args.step == "normalize-price":
        return ops_entry.instrument(
            settings, storage, "NORMALIZE_PRICE", run_id,
            lambda: normalize_price.run(storage, run_id, args.input_run_id),
        )
    # 투자자 수급 정제도 raw 만 읽는 스텝이라 수집 창·벤더 인자가 없다 — 벤더는 raw 키의
    # source= 가 규정하고(현재 kis), 시간축은 레코드의 거래일(stck_bsop_date)이 준다.
    if args.step == "normalize-investor":
        return normalize_investor.run(storage, run_id, args.input_run_id)
    # 뉴스 정제도 raw 를 읽는 스텝이라 수집 날짜창·소스 벤더가 없다 — 벤더는 raw 키의
    # source= 로 판별하고, 대상 범위는 --input-run-id 로만 좁힌다(미지정=전체).
    if args.step == "normalize-news":
        return normalize_news.run(storage, run_id, args.input_run_id)
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

    # 적재(load-*)는 canonical 을 읽어 **DB 에 쓰는** 스텝이라 수집 창·벤더가 없다. DB 설정이
    # 없으면 조용히 0건 적재하고 성공으로 끝나지 않게 여기서 fail-loud 한다(Rule 12).
    if args.step == "load-instruments":
        return load_instruments.run(storage, run_id, db=db_config_from_env(settings.db))

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
    if args.step == "load-disclosure":
        return load_disclosure.run(
            storage, run_id, db=db_config_from_env(settings.db),
            from_date=args.from_date, to_date=args.to_date,
        )

    # 가격 적재도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (canonical trade_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    # LOAD_PRICE_DAILY 는 운영 원장 계측 대상(ALPHA-530) — 정제→feature 게이트 직후 canonical
    # 가격을 소비하는 독립 ECS 작업. ledger 없으면 투명 통과.
    if args.step == "load-price-daily":
        return ops_entry.instrument(
            settings, storage, "LOAD_PRICE_DAILY", run_id,
            lambda: load_price_daily.run(
                storage, run_id, db=db_config_from_env(settings.db),
                from_date=args.from_date, to_date=args.to_date,
            ),
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
        )

    # 투자자 수급 적재도 canonical 을 읽어 DB 에 쓴다 — 창 의미는 load-documents 와 같다
    # (canonical trade_date 파티션 프루닝, 미지정=전체 + 멱등 skip).
    if args.step == "load-etf-flow":
        return load_etf_flow.run(
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
            from_date=args.from_date, to_date=args.to_date, concurrency=concurrency,
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
        # 음수 창은 언제 주어지든(명시 --from/--to 와 함께라 무시될 때조차) 거부한다 — 잘못된
        # 입력이 조용히 삼켜지지 않게(Rule 12). default_window 가 (오늘+N, 오늘) 역전 창을 만들면
        # _partition_dates 가 전 파티션을 제외해 0건 태깅을 exit 0 성공으로 위장한다.
        if args.window_days is not None and args.window_days < 0:
            raise SystemExit(f"--window-days 는 음수일 수 없다: {args.window_days}")
        # 명시 --from/--to 가 최우선(백필). 없고 --window-days 만 있으면 오늘−N일 창으로 좁힌다.
        from_date, to_date = args.from_date, args.to_date
        if from_date is None and to_date is None and args.window_days is not None:
            from_date, to_date = default_window(datetime.now(timezone.utc), args.window_days)
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
                settings.krx_etf.source, PoliteClient(timeout=KRX_ETF_TIMEOUT_SEC)
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
        from_date, to_date = default_window(datetime.now(timezone.utc), lookback)

    if args.step == "ingest-raw":
        vendor = args.source or "fmp"
        if vendor == "fmp":
            fmp_config = settings.news.sources.get("fmp")
            if fmp_config is None:
                # 소스 등록 누락은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
                raise SystemExit("news.sources.fmp 설정이 없다 — sources.toml 확인")
            source = FmpNewsSource(fmp_config, PoliteClient())
        elif vendor == "bigkinds":
            if settings.bigkinds_news is None:
                raise SystemExit("bigkinds_news 설정이 없다 — sources.toml 확인")
            source = BigKindsNewsSource(settings.bigkinds_news, PoliteClient(min_interval=1.0))
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
    if args.step == "ingest-price-raw":
        # 가격은 뉴스와 별개 심볼맵을 쓴다 — ADR 의 USD 시세를 KR 종목 가격으로 쓰면
        # 통화·거래시간이 어긋난다(price.source.symbol_map 은 거래소-로컬 심볼만).
        # --source 로 벤더를 고른다(미지정=fmp, 기존 동작 보존; kis=국내 일봉; yahoo=지수).
        vendor = args.source or "fmp"
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
        # PRICE_COLLECTION_KIS 만 운영 원장 계측 대상(ALPHA-530) — FMP 가격은 미등록이라 그대로.
        def _collect():
            return ingest_price_raw.run(settings, storage, price_source, run_id, from_date, to_date)
        if vendor == "kis":
            return ops_entry.instrument(settings, storage, "PRICE_COLLECTION_KIS", run_id, _collect)
        return _collect()
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
