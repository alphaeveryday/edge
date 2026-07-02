"""실행 진입점 — ECS RunTask command 또는 로컬에서 호출한다.

    python -m data_pipeline.run ingest-raw [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                                           [--run-id RUN_ID] [--config PATH]

수집 날짜창(--from/--to):
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
from .sources import FmpNewsSource, PoliteClient
from .steps import ingest_raw

# 증분 기본 창의 소급 일수 — 어제부터(런 간 경계 겹침을 dedup 이 흡수하도록) 오늘까지.
DEFAULT_LOOKBACK_DAYS = 1


def make_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def default_window(now: datetime) -> tuple[str, str]:
    """증분 기본 창(from, to) = (오늘-소급일, 오늘) UTC 날짜."""
    to_date = now.date()
    from_date = to_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    return from_date.isoformat(), to_date.isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-pipeline")
    parser.add_argument("step", choices=["ingest-raw"])
    parser.add_argument("--from", dest="from_date", default=None, help="수집 시작일 YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="수집 종료일 YYYY-MM-DD")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--config", default=None, help="설정 파일 경로(기본: 동봉 설정)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    settings = load_settings(args.config)
    storage = make_storage(settings.storage)
    run_id = args.run_id or make_run_id()

    # 창 미지정 = 스케줄 증분 → 앱이 어제~오늘로 채운다. 하나라도 지정하면 그대로 존중(백필).
    from_date, to_date = args.from_date, args.to_date
    if from_date is None and to_date is None:
        from_date, to_date = default_window(datetime.now(timezone.utc))

    if args.step == "ingest-raw":
        fmp_config = settings.news.sources.get("fmp")
        if fmp_config is None:
            # 소스 등록 누락은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
            raise SystemExit("news.sources.fmp 설정이 없다 — sources.toml 확인")
        source = FmpNewsSource(fmp_config, PoliteClient())
        return ingest_raw.run(settings, storage, source, run_id, from_date, to_date)
    raise AssertionError(f"unreachable step: {args.step}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
