"""실행 진입점 — ECS RunTask command 또는 로컬에서 호출한다.

    python -m data_pipeline.run ingest-raw [--run-id RUN_ID] [--config PATH]
    python -m data_pipeline.run normalize  [--run-id RUN_ID] [--input-run-id RAW_RUN_ID]

run_id 는 미지정 시 UTC 타임스탬프로 만든다. 같은 run_id 로 재실행하면
raw 파티션 파일을 같은 키에 다시 써 결과가 겹쳐쓰기된다(재현 실행).
normalize 는 --input-run-id 로 특정 수집 런만 처리할 수 있다(기본: raw 전체 — 멱등).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from .config import load_settings
from .lake import make_storage
from .sources import FmpNewsSource, PoliteClient
from .steps import ingest_raw, normalize


def make_run_id(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data-pipeline")
    parser.add_argument("step", choices=["ingest-raw", "normalize"])
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--input-run-id", default=None,
                        help="normalize: 처리할 수집 런 run_id (기본: raw 전체)")
    parser.add_argument("--config", default=None, help="설정 파일 경로(기본: 동봉 설정)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    settings = load_settings(args.config)
    storage = make_storage(settings.storage)
    run_id = args.run_id or make_run_id()

    if args.step == "ingest-raw":
        fmp_config = settings.news.sources.get("fmp")
        if fmp_config is None:
            # 소스 등록 누락은 설정 오류 — 조용한 skip 이 아니라 명시적 실패.
            raise SystemExit("news.sources.fmp 설정이 없다 — sources.toml 확인")
        source = FmpNewsSource(fmp_config, PoliteClient())
        return ingest_raw.run(settings, storage, source, run_id)
    if args.step == "normalize":
        return normalize.run(storage, run_id, input_run_id=args.input_run_id)
    raise AssertionError(f"unreachable step: {args.step}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
