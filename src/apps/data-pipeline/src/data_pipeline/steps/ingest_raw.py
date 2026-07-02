"""Step1 — 원본저장 (S002. raw 존 저장 S028 은 이 스텝에 흡수).

FMP 에서 신규 뉴스 목록을 수집해, 런 내 중복 제거(article_id) 후
(market, published_date) 파티션별 ndjson 으로 raw 존에 append 하고,
실행 결과를 collection_log 로 남긴다.

raw 는 run_id 별 append(재현성) — 런 간 중복은 Step2 canonical 병합이 흡수한다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..config import Settings
from ..dedup import Deduper
from ..lake import Storage, collection_log_key, raw_news_partition
from ..parse import make_article_id, parse_datetime
from ..sources import FmpNewsSource, StopFetch

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw"
SOURCE = "fmp"


def _partition_date(record: dict) -> str:
    """파티션 published_date. 발행시각이 없거나 파싱 불가면 수집시각으로 폴백
    (raw 는 전부 보존 — 품질 게이트는 Step2 소관)."""
    published = parse_datetime(record.get("publishedDate"))
    basis = published or record["fetched_at"]
    return basis[:10]


def run(settings: Settings, storage: Storage, source: FmpNewsSource, run_id: str) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다."""
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]
    log: dict = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "source_vendor": SOURCE,
        "started_at": started_at.isoformat(),
    }

    if not source.enabled:
        # 키 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        logger.warning("fmp 비활성(api_key 미주입) — 수집 건너뜀")
        _write_log(storage, started_date, run_id, {**log, "status": "skipped",
                                                   "reason": "fmp disabled or no api_key"})
        return 0

    deduper = Deduper()
    partitions: dict[tuple[str, str], list[dict]] = defaultdict(list)
    fetched = duplicates = 0
    status, error = "success", None
    exit_code = 0

    try:
        for record in source.fetch(settings.targets.symbols):
            fetched += 1
            article_id = make_article_id(
                record.get("url"), record.get("title") or "", record.get("publishedDate")
            )
            if not deduper.is_new(article_id):
                duplicates += 1
                continue
            record["article_id"] = article_id
            partitions[(record["market"], _partition_date(record))].append(record)
    except StopFetch as exc:
        # 4xx/429 — 부분 수집분은 저장하고 상태로 드러낸다(조용한 성공 금지).
        logger.error("수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1

    saved = 0
    for (market, published_date), records in sorted(partitions.items()):
        key = f"{raw_news_partition(SOURCE, market, published_date, run_id)}/part-00000.ndjson"
        lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
        storage.put_bytes(key, lines.encode("utf-8"))
        saved += len(records)

    _write_log(storage, started_date, run_id, {
        **log,
        "status": status,
        "error": error,
        "records_fetched": fetched,
        "records_saved": saved,
        "records_skipped_duplicate": duplicates,
        "partitions": len(partitions),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    })
    logger.info(
        "ingest_raw 완료: status=%s fetched=%d saved=%d dup=%d partitions=%d",
        status, fetched, saved, duplicates, len(partitions),
    )
    return exit_code


def _write_log(storage: Storage, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(SOURCE, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
