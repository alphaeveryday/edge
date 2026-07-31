"""백필 CLI — **포워드 러너(`data_pipeline.run`)와 별도 진입점이다.**

같은 진입점에 붙이면 스케줄러 설정 한 줄 실수로 백필이 프로덕션 파티션에 쓴다. 격리는
쓰기 좌표만이 아니라 실행 경로에서도 지켜야 한다.

    py -m data_pipeline.backfill.run financial --limit 20 --draft
    py -m data_pipeline.backfill.run financial                      전 종목
    py -m data_pipeline.backfill.run verify --run-id <id> --draft
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime

from ..lake import LocalStorage, S3Storage
from .financial import DATASET, SOURCE, backfill_financial, run_id_for
from .manifest import Manifest

DRAFT = "draft"          # 승격 전 초안 접두사. 승격은 접두사 이동이다


def _storage(a):
    if a.local:
        return LocalStorage(a.local)
    if not a.bucket:
        raise SystemExit("--bucket 또는 --local 이 필요하다 (조용히 로컬에 쓰지 않는다)")
    return S3Storage(a.bucket)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="data_pipeline.backfill.run")
    ap.add_argument("cmd", choices=["financial", "verify"])
    ap.add_argument("--bucket", default="")
    ap.add_argument("--local", default="")
    ap.add_argument("--draft", action="store_true",
                    help="draft/ 접두사 아래에만 쓴다 (승격 전 기본 권장)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tickers", default="")
    ap.add_argument("--ingest-date", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--refetch", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # 두 명령이 같은 기본값을 써야 한다. verify 가 날짜를 비워 두면 run_id 가 달라져
    # 매니페스트를 못 찾고 "비었다"로 죽는다 - 실제로 그렇게 한 번 틀렸다.
    a.ingest_date = a.ingest_date or datetime.now(UTC).date().isoformat()

    storage = _storage(a)
    prefix = DRAFT if a.draft else ""

    if a.cmd == "financial":
        log = backfill_financial(
            storage, limit=a.limit,
            tickers=[t.strip() for t in a.tickers.split(",") if t.strip()] or None,
            ingest_date=a.ingest_date, run_id=a.run_id,
            key_prefix=prefix, refetch=a.refetch)
        print(json.dumps(log, ensure_ascii=False, indent=1))
        return 0 if log["failed"] == 0 else 1

    # verify — 재무 백필의 원장을 sha256 으로 대조한다.
    run_id = a.run_id or run_id_for(a.ingest_date)
    man = Manifest.load_or_new(
        storage, source=SOURCE, dataset=DATASET, market="KR", run_id=run_id,
        ingest_date=a.ingest_date, repo="", revision="", folder="", prefix=prefix)
    if not man.items:
        raise SystemExit(f"매니페스트가 비었다 - run_id={run_id} 가 맞나")
    got = man.verify(storage)
    print(json.dumps(got, ensure_ascii=False, indent=1))
    return 0 if got["mismatched"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
