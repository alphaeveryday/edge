"""실행 진입점 — ECS RunTask command 또는 로컬에서 호출한다.

    python -m data_pipeline.run {ingest-raw|ingest-price-raw|ingest-raw-financial}
                                [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                [--run-id RUN_ID] [--config PATH]

수집 날짜창(--from/--to) — 뉴스·가격만 사용(재무제표는 point-in-time 폴링이라 창 없음):
  - 미지정(스케줄 증분): 어제~오늘 UTC 창을 앱이 계산한다. EventBridge Scheduler 는
    정적 입력만 넣어 '어제/오늘'을 못 만들므로, 창은 이 엔트리가 런타임 시계로 정한다.
  - 명시(백필): 일회성 RunTask 로 --from/--to 를 넘겨 과거 구간을 적재한다.
run_id 는 미지정 시 UTC 타임스탬프. 같은 run_id·창으로 재실행하면 raw 파티션 파일을
같은 키에 다시 써 겹쳐쓰기된다(재현 실행).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from .config import load_settings
from .lake import make_storage
from .sources import (
    BigKindsNewsSource,
    DartFinancialSource,
    FmpFinancialSource,
    FmpNewsSource,
    FmpPriceSource,
    KisDailyPriceSource,
    PoliteClient,
)
from .steps import ingest_price_raw, ingest_raw, ingest_raw_financial

# KIS 시세 TR 초당 한도(EGW00201) 방어용 최소 간격 — 실측 안전값(프로브 MIN_INTERVAL).
KIS_MIN_INTERVAL_SEC = 0.5

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
    parser.add_argument("step", choices=["ingest-raw", "ingest-price-raw", "ingest-raw-financial"])
    parser.add_argument("--from", dest="from_date", default=None, help="수집 시작일 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="수집 종료일 YYYY-MM-DD")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--config", default=None, help="설정 파일 경로(기본: 동봉 설정)")
    # 벤더 선택 — 가격/재무 스텝에서 의미가 있다(미지정=fmp, 기존 동작 보존).
    parser.add_argument("--source", default=None, help="소스 벤더(뉴스: fmp|bigkinds, 가격: fmp|kis, 재무: fmp|dart). 미지정=fmp")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    settings = load_settings(args.config)
    storage = make_storage(settings.storage)
    run_id = args.run_id or make_run_id()

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

    # 창 미지정 = 스케줄 증분 → 앱이 어제~오늘로 채운다. 하나라도 지정하면 그대로 존중(백필).
    # 소급 일수는 스텝별로 다르다(가격 EOD 는 주말·공휴일 공백 때문에 더 넉넉히).
    from_date, to_date = args.from_date, args.to_date
    if from_date is None and to_date is None:
        lookback = (
            DEFAULT_PRICE_LOOKBACK_DAYS if args.step == "ingest-price-raw" else DEFAULT_LOOKBACK_DAYS
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
    if args.step == "ingest-price-raw":
        # 가격은 뉴스와 별개 심볼맵을 쓴다 — ADR 의 USD 시세를 KR 종목 가격으로 쓰면
        # 통화·거래시간이 어긋난다(price.source.symbol_map 은 거래소-로컬 심볼만).
        # --source 로 벤더를 고른다(미지정=fmp, 기존 동작 보존; kis=국내 일봉).
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
        else:
            raise SystemExit(f"알 수 없는 --source: {vendor} (fmp|kis)")
        return ingest_price_raw.run(settings, storage, price_source, run_id, from_date, to_date)
    raise AssertionError(f"unreachable step: {args.step}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
