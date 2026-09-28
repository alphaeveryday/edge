#!/usr/bin/env python3
"""일회성 마이그레이션 — 애드혹 FMP 5분봉 업로드 → 표준 raw 경로 (ALPHA-1104).

    raw/kr_intraday/fmp_5min/*.K[SQ].parquet   (preset kr)
    raw/kr_intraday/fmp_5min_gap/*.parquet     (preset kr-gap)
    raw/fmp_5min_us/*.parquet                  (preset us)
    raw/fmp_5min_us_gap/*.parquet              (preset us-gap)
      → raw/source=fmp/dataset=price_5min/market={KR|US}/ingest_date=…/run_id=…/<원래 파일명>

ponytail: server-side copy(boto3 copy_object)만 쓴다 — parquet을 내려받아 ndjson으로
재직렬화하지 않는다. 이유는 storage.py의 raw_price_5min_partition() 문서 참고(종목당
수만 행 x 1271종목을 ndjson으로 다시 쓰는 건 순수 비용, 벤더 원본이 이미 parquet).
그래서 이 스크립트는 데이터를 변형하지 않고 "표준 경로로 옮기기"만 한다 — 값 정합성
검증(행·키 대조)은 canonical 쪽 대조의 몫이다(docs/design/lake-path-transition-ledger.md).

원본은 건드리지 않는다(비파괴 — 복사만, 삭제는 별도 결정).

**멱등·재개.** 목적 키는 입력만으로 정해진다 — `run_id` 는 원본 프리픽스의 해시,
`ingest_date` 는 **원본 객체의 LastModified 날짜**다(복사한 날이 아니다 — raw 의 수신일을
복사 시각으로 바꾸지 않는다). 그래서 재실행은 같은 키를 다시 보고, 이미 같은 바이트가
있으면 복사 없이 `already_identical` 로 넘어간다. 중간에 죽으면 다시 돌리면 된다.
2026-07-29 에 이미 돈 KR 복사(`run_8645…`, ingest_date=복사일)는 `--run-id`·`--ingest-date`
로 그 자리를 짚어 대조할 수 있다 — 같은 바이트는 다시 복사하지 않는다. `--dry-run` 이면
쓰기가 전혀 없고, 빼면 빠진 객체를 채우고 검증 로그를 남긴다.

**체크섬.** 버킷은 SSE-S3(AES256)라 단일 파트 객체의 ETag 가 곧 MD5 다. 원본이 단일
파트면 ETag 를 대조하고, 멀티파트(ETag 에 `-N`)면 원본을 스트리밍해 MD5 를 구해
목적 ETag(copy_object 결과는 단일 파트)와 대조한다. 버킷 버전 관리는 꺼져 있어
version_id 는 기록할 값이 없다(null 로 남긴다).

**부분 실패는 성공이 아니다.** 목적 키에 다른 바이트가 있으면 덮지 않고 `mismatch` 로
남긴다(raw 는 불변). 오류·불일치가 하나라도 있으면 status 가 success 가 아니고 exit 1 이다.
원본 프리픽스의 모든 객체가 로그에 한 줄씩 남는다 — 데이터가 아닌 것(cursor·log·스크립트)은
`excluded` 로 적어, 빠진 것이 "안 옮긴 것"인지 "못 본 것"인지 갈리게 한다.

실행:
    AWS_PROFILE=edge python scripts/migrate_fmp_5min_raw.py --preset us --dry-run
    AWS_PROFILE=edge python scripts/migrate_fmp_5min_raw.py --preset kr-gap
    AWS_PROFILE=edge python scripts/migrate_fmp_5min_raw.py --preset kr \\
        --run-id run_8645481c2c1d4451af227c1633f1030d --ingest-date 2026-07-29 --dry-run  # 기존 복사 대조(쓰기 없음)
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_pipeline.lake.storage import collection_log_key, raw_price_5min_partition  # noqa: E402

SOURCE_VENDOR = "fmp"
DATASET = "price_5min"
# preset → (원본 프리픽스, 시장, 데이터 파일 판정). 프리픽스 바로 아래 파일만 본다.
PRESETS = {
    "kr": ("raw/kr_intraday/fmp_5min/", "KR", lambda f: f.endswith((".KS.parquet", ".KQ.parquet"))),
    "kr-gap": ("raw/kr_intraday/fmp_5min_gap/", "KR", lambda f: f.endswith(".parquet")),
    "us": ("raw/fmp_5min_us/", "US", lambda f: f.endswith(".parquet")),
    "us-gap": ("raw/fmp_5min_us_gap/", "US", lambda f: f.endswith(".parquet")),
}


def default_run_id(source_prefix: str) -> str:
    """원본 프리픽스로 정해지는 run_id — 재실행이 같은 목적지를 보게 한다."""
    return "run_" + hashlib.sha256(f"migrate:{source_prefix}".encode()).hexdigest()[:32]


def plan(objects: list[dict], preset: str, run_id: str, ingest_date: str | None,
         match: str, limit: int) -> tuple[list[dict], list[dict]]:
    """원본 목록 → (복사 대상, 제외). 각 항목에 목적 키를 붙인다. 순수 함수 — 테스트 대상."""
    prefix, market, is_data = PRESETS[preset]
    todo, excluded = [], []
    for o in sorted(objects, key=lambda o: o["Key"]):
        rel = o["Key"][len(prefix):]
        item = {"src_key": o["Key"], "src_etag": o["ETag"].strip('"'), "src_size": o["Size"],
                "src_last_modified": o["LastModified"].isoformat(), "src_version_id": None}
        if "/" in rel or not is_data(rel):
            excluded.append(item | {"status": "excluded", "reason": "데이터 파일 아님"})
            continue
        if match and not fnmatch.fnmatch(rel, match):
            continue
        day = ingest_date or o["LastModified"].astimezone(timezone.utc).date().isoformat()
        dest = raw_price_5min_partition(source=SOURCE_VENDOR, market=market,
                                        ingest_date=day, run_id=run_id)
        todo.append(item | {"dest_key": f"{dest}/{rel}"})
    return (todo[:limit] if limit else todo), excluded


def _md5(s3, bucket: str, key: str) -> str:
    h = hashlib.md5()  # noqa: S324 - S3 ETag 대조용, 보안 용도 아님
    for chunk in s3.get_object(Bucket=bucket, Key=key)["Body"].iter_chunks(1 << 20):
        h.update(chunk)
    return h.hexdigest()


def _dest_etag(s3, bucket: str, key: str) -> str | None:
    try:
        return s3.head_object(Bucket=bucket, Key=key)["ETag"].strip('"')
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def verdict(src_etag: str, dest_etag: str, src_md5) -> bool:
    """목적 바이트가 원본과 같은가. 멀티파트 원본만 MD5 를 계산한다(`src_md5` 는 지연 호출)."""
    return dest_etag == (src_md5() if "-" in src_etag else src_etag)


def migrate_one(s3, bucket: str, item: dict, dry_run: bool) -> dict:
    """한 객체: 있으면 대조, 없으면 복사 후 대조. 예외는 status=error 로 접는다(전체 중단 금지)."""
    src_md5 = lambda: _md5(s3, bucket, item["src_key"])  # noqa: E731
    try:
        existing = _dest_etag(s3, bucket, item["dest_key"])
        if existing is not None:
            ok = verdict(item["src_etag"], existing, src_md5)
            return item | {"dest_etag": existing,
                           "status": "already_identical" if ok else "mismatch"}
        if dry_run:
            return item | {"dest_etag": None, "status": "would_copy"}
        s3.copy_object(Bucket=bucket, Key=item["dest_key"],
                       CopySource={"Bucket": bucket, "Key": item["src_key"]})
        copied = _dest_etag(s3, bucket, item["dest_key"])
        ok = copied is not None and verdict(item["src_etag"], copied, src_md5)
        return item | {"dest_etag": copied, "status": "copied" if ok else "mismatch"}
    except Exception as exc:  # noqa: BLE001 - 개별 실패 격리, 로그에 남긴다
        return item | {"status": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}


def summarize(results: list[dict]) -> str:
    """전체 status. 오류·불일치가 하나라도 있으면 success 가 아니다."""
    bad = [r for r in results if r["status"] in ("error", "mismatch")]
    if not bad:
        return "success"
    return "partial" if len(bad) < len(results) else "error"


def main() -> int:
    """FMP 5분봉 raw 마이그레이션 CLI — 종료 코드 반환."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preset", choices=sorted(PRESETS), required=True)
    parser.add_argument("--bucket", default="edge-dev-pipeline-lake")
    parser.add_argument("--run-id", default="", help="비우면 원본 프리픽스 해시")
    parser.add_argument("--ingest-date", default="",
                        help="비우면 객체별 원본 LastModified 날짜(UTC)")
    parser.add_argument("--match", default="", help="파일명 글롭으로 범위 제한 (예: 'A*.parquet')")
    parser.add_argument("--limit", type=int, default=0, help="앞에서 N개만 (0=전부)")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true", help="복사·로그 PUT 없음. 대조는 한다")
    parser.add_argument("--report", default="", help="로컬에 전체 로그 JSON 을 쓴다")
    args = parser.parse_args()

    import boto3

    s3 = boto3.client("s3")
    prefix, market, _ = PRESETS[args.preset]
    run_id = args.run_id or default_run_id(prefix)
    objects = [o for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=args.bucket, Prefix=prefix) for o in page.get("Contents", [])]
    todo, excluded = plan(objects, args.preset, run_id, args.ingest_date or None,
                          args.match, args.limit)
    print(f"원본 {len(objects)}개 (prefix={prefix}) → 대상 {len(todo)} · 제외 {len(excluded)} "
          f"· run_id={run_id}{' (dry-run)' if args.dry_run else ''}")

    started_at = datetime.now(timezone.utc)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda it: migrate_one(s3, args.bucket, it, args.dry_run), todo))
    status = summarize(results)
    if args.dry_run and status == "success":
        status = "dry_run_ok"   # 옮긴 것이 없다 — 이관 성공으로 읽히면 안 된다
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    finished_at = datetime.now(timezone.utc)

    log = {
        "run_id": run_id,
        "job_name": "migrate_fmp_5min_raw",
        "source_vendor": SOURCE_VENDOR,
        "note": "애드혹 FMP 5분봉 업로드를 표준 raw 경로로 server-side copy 한 일회성 마이그레이션 — "
                "라이브 수집 잡 아님, 원본 미삭제. ingest_date 는 원본 LastModified 날짜.",
        "preset": args.preset, "market": market, "source_prefix": prefix,
        "dry_run": args.dry_run, "filter": {"match": args.match, "limit": args.limit},
        "started_at": started_at.isoformat(), "finished_at": finished_at.isoformat(),
        "status": status, "counts": counts,
        "bytes": sum(r["src_size"] for r in results),
        "records_fetched": len(todo),
        "records_saved": counts.get("copied", 0) + counts.get("already_identical", 0),
        "ops": {"records_out": counts.get("copied", 0),
                "failed_records": counts.get("error", 0) + counts.get("mismatch", 0)},
        "objects": results, "excluded": excluded,
    }
    body = json.dumps(log, ensure_ascii=False, indent=1).encode("utf-8")
    # 두 기록은 서로를 막지 않는다 — 복사는 이미 끝났으므로 어느 한쪽이라도 남아야 재시도를
    # 판단할 수 있다. 어느 쪽이든 실패하면 exit 1.
    recorded = True
    if not args.dry_run:
        # 로그 run_id 에 실행 시각을 붙인다 — 같은 목적지 재실행(검증)이 앞선 로그를 덮지 않게.
        log_key = collection_log_key(source=SOURCE_VENDOR, dataset=DATASET,
                                     started_date=started_at.date().isoformat(),
                                     run_id=f"{run_id}-{started_at:%Y%m%dT%H%M%SZ}")
        try:
            s3.put_object(Bucket=args.bucket, Key=log_key, Body=body)
            print(f"collection_log: {log_key}")
        except Exception as exc:  # noqa: BLE001 - 로컬 사본을 살리고 실패로 끝낸다
            print(f"collection_log 기록 실패: {type(exc).__name__}: {exc}")
            recorded = False
    if args.report:
        try:
            Path(args.report).write_bytes(body)
        except OSError as exc:
            print(f"--report 기록 실패: {exc}")
            recorded = False
    print(f"{status}: {counts}")
    for r in [r for r in results if r["status"] in ("error", "mismatch")][:20]:
        print(f"  {r['status']} {r['src_key']}: {r.get('error', r.get('dest_etag'))}")
    return 0 if recorded and status in ("success", "dry_run_ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
