"""Step1 — 원본저장 (S002. raw 존 저장 S028 은 이 스텝에 흡수).

FMP 에서 신규 뉴스 목록을 수집해, 런 내 중복은 article_id 로 합치고
(같은 기사의 여러 종목 mention 은 병합) (market, published_date) 파티션별
ndjson 으로 raw 존에 append 하고, 실행 결과를 collection_log 로 남긴다.

raw 는 run_id 별 append(재현성) — 런 간 중복은 Step2 canonical 병합이 흡수한다.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from ..config import Settings
from ..lake import Storage, collection_log_key, raw_news_partition
from ..parse import bigkinds_date, news_article_id, parse_datetime
from ..sources import BigKindsNewsSource, FmpNewsSource, StopFetch

logger = logging.getLogger(__name__)

JOB_NAME = "ingest_raw"
DATASET = "stock_news"  # collection_log·raw 파티션의 dataset= 키
NewsSourceAdapter = FmpNewsSource | BigKindsNewsSource


def _partition_date(record: dict, fallback_date: str) -> str:
    """파티션 published_date. 발행시각이 없거나 파싱 불가면 수집시각으로,
    그마저 없으면 런 시작일로 폴백한다(raw 는 전부 보존 — 하나도 못 버림).
    fetched_at 하드 서브스크립트로 한 레코드가 런 전체를 죽이지 않게."""
    published = parse_datetime(record.get("publishedDate"))
    if published is None:
        bk_date = bigkinds_date(record)
        if bk_date:
            return bk_date
    basis = published or record.get("fetched_at") or fallback_date
    return basis[:10]


def run(
    settings: Settings,
    storage: Storage,
    source: NewsSourceAdapter,
    run_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """수집 실행. 성공 0, 중단/실패 비0 반환. 결과는 항상 collection_log 로 남긴다.

    from_date/to_date 는 소스에 넘길 수집 날짜창(YYYY-MM-DD). None 이면 소스 기본
    (최신분) — 스케줄 증분·백필 창은 run 엔트리가 정해 넘긴다.
    """
    started_at = datetime.now(timezone.utc)
    started_date = started_at.isoformat()[:10]
    vendor = source.source_name  # 파티션·로그의 source= 키 (하드코딩 대신 소스가 규정)
    log: dict = {
        "run_id": run_id,
        "job_name": JOB_NAME,
        "source_vendor": vendor,
        "window_from": from_date,
        "window_to": to_date,
        "started_at": started_at.isoformat(),
    }

    if not source.enabled:
        # 키 미주입 환경(로컬 등)은 실패가 아니라 명시적 skip — 로그로 드러낸다.
        # 로그 쓰기 실패는 스토리지 장애라 스케줄러에 비0으로 드러낸다.
        logger.warning("%s 비활성 — 수집 건너뜀", vendor)
        try:
            _write_log(storage, vendor, started_date, run_id, {**log, "status": "skipped",
                                                               "reason": f"{vendor} disabled"})
        except Exception:
            logger.exception("collection_log 기록 실패(skip 경로)")
            return 1
        return 0

    # article_id → 보관 중인 record. 같은 기사가 여러 심볼 질의에 걸려 오면
    # 새 record 를 버리지 않고 그 (market, ticker) mention 을 기존 record 에 병합한다
    # (질의 기반 소스는 어느 종목으로 걸렸는지 알고 있어 — 이 연결을 raw 에서 보존).
    kept_by_id: dict[str, dict] = {}
    partitions: dict[tuple[str, str], list[dict]] = defaultdict(list)
    fetched = duplicates = 0
    status, error, reason = "success", None, None
    exit_code = 0

    try:
        for record in source.fetch(settings.targets.symbols, from_date, to_date):
            fetched += 1
            if getattr(source, "preserve_all_rows", False):
                record["article_id"] = news_article_id(record)
                partitions[(record["market"], _partition_date(record, started_date))].append(record)
                continue
            article_id = news_article_id(record)
            mention = {"market": record["market"], "ticker": record["our_ticker"]}
            existing = kept_by_id.get(article_id)
            if existing is not None:
                duplicates += 1
                if mention not in existing["mentions"]:
                    existing["mentions"].append(mention)
                continue
            record["article_id"] = article_id
            record["mentions"] = [mention]
            kept_by_id[article_id] = record
            partitions[(record["market"], _partition_date(record, started_date))].append(record)
    except StopFetch as exc:
        # 4xx/429 — 부분 수집분은 저장하고 상태로 드러낸다(조용한 성공 금지).
        logger.error("수집 중단(4xx/429): %s", exc)
        status, error, exit_code = "stopped", str(exc), 1
    except Exception as exc:
        # 예기치 못한 실패(재시도 소진 등)도 '결과는 항상 collection_log' 계약을
        # 지킨다 — 부분 수집분 저장 + status=error 로 남기고 비0 종료.
        logger.exception("수집 실패")
        status, error, exit_code = "error", str(exc), 1

    # raw 저장도 계약("결과는 항상 collection_log") 안에 둔다 — put_bytes 가
    # 실패(IAM·네트워크·부분 쓰기)해도 예외를 삼켜 status=error 로 남기고 로그를 쓴다.
    saved = 0
    try:
        for (market, published_date), records in sorted(partitions.items()):
            key = f"{raw_news_partition(vendor, market, published_date, run_id)}/part-00000.ndjson"
            lines = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)
            storage.put_bytes(key, lines.encode("utf-8"))
            saved += len(records)
    except Exception as exc:
        logger.exception("raw 저장 실패")
        status, error, exit_code = "error", str(exc), 1

    # 심볼 단위로 격리한 실패를 런 상태에 반영한다(격리≠은폐 — fail loud).
    #  - 저장분 있고 일부 실패 → partial(성공했지만 온전치 않음)
    #  - 저장분 0인데 실패 있음 → error(수집이 사실상 실패)
    #  - MAX_PAGES 절단(kind=truncation)은 데이터 유효 + 다음 창 이어받음이라 성공으로 본다
    #    (ALPHA-351). 절단도 아래 로그(failed_symbols)엔 남겨 fail-loud 는 유지한다.
    failed_symbols = getattr(source, "fetch_failures", [])
    real_failures = [f for f in failed_symbols if f.get("kind") != "truncation"]
    if status == "success" and real_failures:
        if saved == 0:
            status, exit_code = "error", 1
            error = f"모든 수집 심볼 실패 ({len(real_failures)}건)"
        else:
            status, exit_code = "partial", 1

    # 활성 소스인데 매핑된 대상이 0개면(심볼맵 누락·전 대상 미매핑 KR 등) 수집이
    # 사실상 불가능한 설정 — success(0건)로 위장하지 않고 skip 으로 드러낸다(Rule 12).
    # plan() 규약상 미매핑은 오류가 아니라 정상(후속 소스가 커버)이라 error 가 아닌 skip.
    if status == "success" and getattr(source, "planned_symbols", None) == 0:
        status, reason = "skipped", "no mapped targets"

    # 로그 쓰기도 best-effort — 스토리지가 통째로 죽어 로그마저 못 남기면 최소한
    # 비0 종료로 스케줄러/ECS 에 실패를 알린다(감사 로그 유실은 로거로만 남김).
    try:
        _write_log(storage, vendor, started_date, run_id, {
            **log,
            "status": status,
            "error": error,
            "reason": reason,
            "records_fetched": fetched,
            "records_saved": saved,
            "records_skipped_duplicate": duplicates,
            "records_failed_symbols": len(failed_symbols),
            "failed_symbols": failed_symbols,
            "partitions": len(partitions),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("collection_log 기록 실패 — 스토리지 장애로 감사 로그 유실")
        exit_code = exit_code or 1
    logger.info(
        "ingest_raw 완료: status=%s fetched=%d saved=%d dup=%d failed_symbols=%d partitions=%d",
        status, fetched, saved, duplicates, len(failed_symbols), len(partitions),
    )
    return exit_code


def _write_log(storage: Storage, vendor: str, started_date: str, run_id: str, payload: dict) -> None:
    key = collection_log_key(vendor, DATASET, started_date, run_id)
    storage.put_bytes(key, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
