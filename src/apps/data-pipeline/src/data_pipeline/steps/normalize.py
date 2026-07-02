"""Step2 — 정규화·품질검증 (S003).

raw 뉴스(ndjson)를 읽어 품질 게이트를 통과한 항목만
canonical/news/news_articles(메타)와 news_article_bodies(본문)에
article_id 키로 **파티션 병합(멱등)** 한다. canonical 에는 run_id 가 없다 —
같은 raw 를 몇 번 돌려도 결과가 같다. 실패 항목은 사유와 함께
data_quality_logs 에 남는다(AC2).
"""

from __future__ import annotations

import io
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..lake import (
    Storage,
    canonical_news_articles_key,
    canonical_news_bodies_key,
    quality_log_key,
)
from ..parse import make_article_id, normalize_url, parse_datetime, url_hash
from ..quality import check_record

logger = logging.getLogger(__name__)

JOB_NAME = "normalize"
SOURCE_VENDOR = "fmp"
DATASET = "news_articles"
RAW_PREFIX = f"raw/source={SOURCE_VENDOR}/dataset=stock_news/"


def _to_meta(record: dict, article_id: str, published_at: str) -> dict:
    """S003 1차 저장 필드 — 제목·발행시각·언론사·URL(+식별자)."""
    url = record.get("url")
    return {
        "article_id": article_id,
        "source_vendor": SOURCE_VENDOR,
        "title": record["title"].strip(),
        "url": url,
        "normalized_url": normalize_url(url),
        "normalized_url_hash": url_hash(url),
        "published_at": published_at,
        "publisher": record["site"].strip(),
        "summary": None,  # FMP text 는 본문 — 요약 필드는 없음
    }


def _read_parquet(data: bytes | None) -> list[dict]:
    if data is None:
        return []
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _write_parquet(rows: list[dict]) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq

    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows), buf)
    return buf.getvalue()


def _merge_partition(storage: Storage, key: str, new_rows: dict[str, dict]) -> None:
    """기존 파티션과 article_id 키로 병합(새 값 우선) 후 덮어쓴다 — 멱등."""
    merged = {row["article_id"]: row for row in _read_parquet(storage.get_bytes_or_none(key))}
    merged.update(new_rows)
    rows = [merged[article_id] for article_id in sorted(merged)]
    storage.put_bytes(key, _write_parquet(rows))


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw → canonical. input_run_id 지정 시 그 수집 런만, 아니면 raw 전체(멱등)."""
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]

    raw_keys = storage.list_keys(RAW_PREFIX)
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    # 파티션별 article_id → row. dict 라 raw 내 중복도 자연히 마지막 값으로 수렴한다.
    metas: dict[str, dict[str, dict]] = defaultdict(dict)
    bodies: dict[str, dict[str, dict]] = defaultdict(dict)
    failures: list[dict] = []
    read = passed = 0

    for raw_key in raw_keys:
        for line in storage.get_bytes(raw_key).decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            read += 1
            reasons = check_record(record)
            article_id = record.get("article_id") or make_article_id(
                record.get("url"), record.get("title") or "", record.get("publishedDate")
            )
            if reasons:
                failures.append({"article_id": article_id, "raw_key": raw_key,
                                 "reasons": reasons})
                continue
            passed += 1
            published_at = parse_datetime(record["publishedDate"])
            published_date = published_at[:10]
            metas[published_date][article_id] = _to_meta(record, article_id, published_at)
            body_text = (record.get("text") or "").strip()
            if body_text:  # 본문 없는 기사는 메타만 — 빈 본문 행을 만들지 않는다
                bodies[published_date][article_id] = {
                    "article_id": article_id,
                    "published_at": published_at,
                    "body": body_text,
                }

    for published_date, rows in sorted(metas.items()):
        _merge_partition(
            storage, canonical_news_articles_key(published_date, SOURCE_VENDOR), rows
        )
    for published_date, rows in sorted(bodies.items()):
        _merge_partition(
            storage, canonical_news_bodies_key(published_date, SOURCE_VENDOR), rows
        )

    storage.put_bytes(
        quality_log_key(DATASET, checked_date, run_id),
        json.dumps({
            "run_id": run_id,
            "job_name": JOB_NAME,
            "dataset": DATASET,
            "input_run_id": input_run_id,
            "raw_files": len(raw_keys),
            "records_read": read,
            "records_passed": passed,
            "records_failed": len(failures),
            "failures": failures,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False).encode("utf-8"),
    )
    logger.info(
        "normalize 완료: raw_files=%d read=%d passed=%d failed=%d",
        len(raw_keys), read, passed, len(failures),
    )
    return 0
