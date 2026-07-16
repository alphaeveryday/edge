"""문서 마스터 적재 — canonical 뉴스 → document (ALPHA-374).

`document_assertion.document_id` FK 의 선행이다 — assertion 적재(ALPHA-376)는 여기가
풀리지 않으면 INSERT 자체가 불가능하다. canonical `news/news_articles/language=…/
published_date=…` 를 읽어 기사마다 `document` 행(document_type='NEWS')을 만든다.

**멱등**: 자연키 = `uq_document_source (source_code, source_document_id)` =
canonical 의 (source_vendor, article_id). `ON CONFLICT DO NOTHING` 으로 제약 자체가
멱등의 근거다 — 이미 있으면(rowcount 0) 기존 행을 건드리지 않아, 재실행이 ID 를 바꿔
이 문서를 참조할 assertion FK 를 끊는 일이 없다(ADR-0027). 사전 스냅샷 조회 방식
(load-instruments)과 달리 동시 실행에도 원자적이다.

**창(from/to) 미지정 = published_date 전체 스캔** — 멱등 skip 이라 재실행 비용은
신규분뿐이고, 놓친 날짜도 다음 런이 자연 회복한다(load-price-triggers 와 같은 모델).

`available_at`(NOT NULL)은 canonical `fetched_at`(수집 시각)이고, 결측이면
`published_at` 으로 대신한다 — "우리가 이 문서를 쓸 수 있게 된 시각"의 가장 보수적인
근사가 수집 시각이다. 둘 다 없으면 그 행은 적재하지 않고 결손으로 센다.

공시(document_type='DISCLOSURE')·`news_document`(lead_text)·`document_entity` 는
범위 밖(별건) — 뉴스 경로를 먼저 세운다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from ..config import DbConfig
from ..db import connect, domain_id
from ..lake import Storage, canonical_news_articles_partition, quality_log_key

logger = logging.getLogger(__name__)

JOB_NAME = "load_documents"
DATASET = "document"

# canonical 뉴스의 언어 파티션 축(ALPHA-352, 벤더 고정: bigkinds=ko·fmp=en).
# 문서 마스터는 언어 무관 레지스트리라 둘 다 싣는다 — tag-news 가 ko 만 태깅하는 것과
# 별개로, en 문서도 assertion 이 생기는 순간 FK 대상이 돼야 한다.
LANGUAGES = ("ko", "en")

# quality log 의 created_rows 는 표본만 남긴다 — 백필 런은 수천 행이라 로그가 데이터가
# 된다. 몇 건 만들었는지(created)는 전수, 어떤 행인지는 표본이 답한다. 자르는 걸 숨기지
# 않도록 키 이름에 sample 을 박는다.
_CREATED_SAMPLE_LIMIT = 50


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _partition_dates(storage: Storage, language: str) -> list[str]:
    """이 언어의 canonical published_date 목록(오름차순). 경로는 빌더로만 만든다(레이크 규약)."""
    marker = canonical_news_articles_partition(language, "")  # ".../published_date="
    dates: set[str] = set()
    for key in storage.list_keys(marker):
        date = key[len(marker):].split("/", 1)[0]
        if date:
            dates.add(date)
    return sorted(dates)


def run(
    storage: Storage,
    run_id: str,
    *,
    db: DbConfig,
    from_date: str | None = None,
    to_date: str | None = None,
) -> int:
    """canonical 뉴스 → document 적재. 성공 0, 장애 시 비0."""
    started_at = datetime.now(timezone.utc)
    read = skipped_missing_identity = skipped_no_available_at = 0
    already = created = 0
    created_sample: list[dict] = []
    failures: list[dict] = []
    exit_code = 0

    try:
        # (source_code, source_document_id) → 적재 후보. 같은 기사가 여러 파티션에 오면
        # (드묾 — 같은 URL 재게시) 첫 행으로 접는다: 자연키가 하나면 문서도 하나다.
        candidates: dict[tuple[str, str], dict] = {}
        for language in LANGUAGES:
            dates = [d for d in _partition_dates(storage, language)
                     if (from_date is None or d >= from_date) and (to_date is None or d <= to_date)]
            for date in dates:
                prefix = canonical_news_articles_partition(language, date)
                for key in storage.list_keys(prefix + "/"):
                    if not key.endswith(".parquet"):
                        continue
                    for row in _read_parquet_rows(storage.get_bytes(key)):
                        read += 1
                        source_code = row.get("source_vendor")
                        article_id = row.get("article_id")
                        if not source_code or not article_id:
                            # 자연키 결손 — 넣으면 NOT NULL 위반이거나(즉시 실패) 멱등의
                            # 근거가 사라진다(같은 기사가 매 런 새 행). 세고 뺀다.
                            skipped_missing_identity += 1
                            continue
                        available_at = row.get("fetched_at") or row.get("published_at")
                        if not available_at:
                            # available_at 은 NOT NULL — 시간 축이 없는 문서는 적재 불가.
                            skipped_no_available_at += 1
                            continue
                        candidates.setdefault((source_code, article_id), {
                            "language_code": language,
                            "title": row.get("title"),
                            "published_at": row.get("published_at"),
                            "available_at": available_at,
                            "source_uri": row.get("url"),
                        })

        with connect(db) as conn:
            for (source_code, article_id), doc in sorted(candidates.items()):
                # 멱등의 근거는 사전 스냅샷이 아니라 자연키 제약 자체다 — DO NOTHING 이라
                # 동시 실행이 같은 키를 넣어도 늦은 쪽이 already 로 세어질 뿐 배치가 죽지
                # 않는다. rowcount 0 = 이미 있었다(분석엔진이 먼저 쓴 행 포함).
                document_id = domain_id("doc")
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO document (document_id, document_type, source_code,"
                        " source_document_id, title, language_code, published_at,"
                        " available_at, source_uri)"
                        " VALUES (%s, 'NEWS', %s, %s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT (source_code, source_document_id) DO NOTHING",
                        (
                            document_id,
                            source_code,
                            article_id,
                            doc["title"],
                            doc["language_code"],
                            doc["published_at"],
                            doc["available_at"],
                            doc["source_uri"],
                        ),
                    )
                    if cur.rowcount == 0:
                        already += 1
                        continue
                created += 1
                if len(created_sample) < _CREATED_SAMPLE_LIMIT:
                    created_sample.append({"document_id": document_id,
                                           "source_code": source_code,
                                           "source_document_id": article_id})
    except Exception as exc:
        # 커밋 경계는 런 전체다 — connect() 가 예외면 롤백이라 부분 적재가 없다. 트레이스백으로
        # 죽는 대신 사유를 로그 계약("결과는 항상 로그")에 태운다(Rule 12).
        logger.exception("문서 적재 실패(롤백)")
        failures.append({"reasons": ["load_error"], "error": str(exc)})
        created, created_sample = 0, []
        exit_code = 1

    log = {
        "job": JOB_NAME, "run_id": run_id, "dataset": DATASET,
        "started_at": started_at.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "languages": list(LANGUAGES), "from_date": from_date, "to_date": to_date,
        "articles_read": read,
        "skipped_missing_identity": skipped_missing_identity,
        "skipped_no_available_at": skipped_no_available_at,
        "already_present": already, "created": created,
        "created_rows_sample": created_sample,
        "failures": failures, "exit_code": exit_code,
    }
    try:
        storage.put_bytes(quality_log_key(DATASET, started_at.isoformat()[:10], run_id),
                          json.dumps(log, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.exception("적재 로그 기록 실패")
        exit_code = 1

    logger.info(
        "load_documents: read=%d skipped_identity=%d skipped_available_at=%d already=%d"
        " created=%d failures=%d",
        read, skipped_missing_identity, skipped_no_available_at, already, created, len(failures),
    )
    return exit_code
