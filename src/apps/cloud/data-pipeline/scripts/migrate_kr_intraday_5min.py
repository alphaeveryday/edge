#!/usr/bin/env python3
"""일회성 마이그레이션 — raw/kr_intraday/fmp_5min/ (애드혹 업로드) →
raw/source=fmp/dataset=price_5min/market=KR/ingest_date=.../run_id=.../ (표준 raw 경로).

ponytail: server-side copy(boto3 copy_object)만 쓴다 — parquet을 내려받아 ndjson으로
재직렬화하지 않는다. 이유는 storage.py의 raw_price_5min_partition() 문서 참고(종목당
수만 행 x 1271종목을 ndjson으로 다시 쓰는 건 순수 비용, 벤더 원본이 이미 parquet).
그래서 이 스크립트는 데이터를 변형하지 않고 "표준 경로로 옮기기"만 한다 — 값 정합성
검증(합계·행수 대조)은 정제(normalize) 스텝이 생길 때의 몫이다(지금은 없음).

원본(raw/kr_intraday/fmp_5min/)은 건드리지 않는다(비파괴 — 복사만, 삭제는 별도 결정).

실행: AWS_PROFILE=work python scripts/migrate_kr_intraday_5min.py [--bucket edge-dev-pipeline-lake] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_pipeline.lake.storage import collection_log_key, raw_price_5min_partition  # noqa: E402

SOURCE_PREFIX = "raw/kr_intraday/fmp_5min/"
SOURCE_VENDOR = "fmp"
MARKET = "KR"


def _is_ticker_parquet(key: str) -> bool:
    fname = key.rsplit("/", 1)[-1]
    return fname.endswith(".KS.parquet") or fname.endswith(".KQ.parquet")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default="edge-dev-pipeline-lake")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import boto3

    s3 = boto3.client("s3")
    bucket = args.bucket

    paginator = s3.get_paginator("list_objects_v2")
    source_keys = [
        obj["Key"]
        for page in paginator.paginate(Bucket=bucket, Prefix=SOURCE_PREFIX)
        for obj in page.get("Contents", [])
        if _is_ticker_parquet(obj["Key"])
    ]
    print(f"원본 티커 parquet {len(source_keys)}개 발견 (prefix={SOURCE_PREFIX})")

    run_id = f"run_{uuid.uuid4().hex}"
    ingest_date = datetime.now(timezone.utc).date().isoformat()
    started_at = datetime.now(timezone.utc)
    dest_prefix = raw_price_5min_partition(
        source=SOURCE_VENDOR, market=MARKET, ingest_date=ingest_date, run_id=run_id
    )
    print(f"목적지: {dest_prefix}/ (run_id={run_id})")

    if args.dry_run:
        print("--dry-run: 실제 복사 없음. 첫 3개 예시:")
        for k in source_keys[:3]:
            fname = k.rsplit("/", 1)[-1]
            print(f"  {k}  ->  {dest_prefix}/{fname}")
        return

    failures: list[dict] = []
    copied = 0

    def _copy_one(src_key: str) -> tuple[str, str | None]:
        fname = src_key.rsplit("/", 1)[-1]
        dest_key = f"{dest_prefix}/{fname}"
        try:
            s3.copy_object(
                Bucket=bucket,
                CopySource={"Bucket": bucket, "Key": src_key},
                Key=dest_key,
            )
            return fname, None
        except Exception as exc:  # noqa: BLE001 - 개별 실패 격리, 전체 중단 금지
            return fname, str(exc)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_copy_one, k): k for k in source_keys}
        for i, fut in enumerate(as_completed(futures), 1):
            fname, error = fut.result()
            if error is None:
                copied += 1
            else:
                failures.append({"file": fname, "error": error})
            if i % 200 == 0 or i == len(source_keys):
                print(f"  [{i}/{len(source_keys)}] copied={copied} failed={len(failures)}")

    finished_at = datetime.now(timezone.utc)
    status = "success" if not failures else ("partial" if copied else "error")

    log = {
        "run_id": run_id,
        "job_name": "migrate_kr_intraday_5min",
        "source_vendor": SOURCE_VENDOR,
        "note": "애드혹 업로드(raw/kr_intraday/fmp_5min/)를 표준 raw 경로로 server-side copy한"
        " 일회성 마이그레이션 — 라이브 수집 잡 아님, 원본 미삭제.",
        "source_prefix": SOURCE_PREFIX,
        "dest_prefix": dest_prefix,
        "window_from": None,
        "window_to": None,
        "started_at": started_at.isoformat(),
        "status": status,
        "error": None,
        "reason": None,
        "records_fetched": len(source_keys),
        "records_saved": copied,
        "records_skipped_duplicate": 0,
        "records_failed_symbols": len(failures),
        "failed_symbols": failures,
        "partitions": 1,
        "finished_at": finished_at.isoformat(),
        "ops": {"records_out": copied, "failed_records": len(failures)},
    }

    log_key = collection_log_key(
        source=SOURCE_VENDOR, dataset="price_5min", started_date=ingest_date, run_id=run_id
    )
    s3.put_object(Bucket=bucket, Key=log_key, Body=json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))

    print(f"완료: copied={copied} failed={len(failures)} status={status}")
    print(f"collection_log: {log_key}")
    if failures:
        print("실패 목록:")
        for f in failures[:20]:
            print(f"  {f['file']}: {f['error']}")


if __name__ == "__main__":
    main()
