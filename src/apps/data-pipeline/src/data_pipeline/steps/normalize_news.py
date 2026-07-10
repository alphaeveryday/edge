"""뉴스 정제 Step2 — 정규화 + 필수필드·발행일 게이트 (ALPHA-131 / S030).

raw stock_news(FMP·BigKinds 두 벤더, 이형 스키마)를 읽어 **표준 뉴스 메타행으로
정규화**하고, 필수필드 게이트(quality/news.validate_news_meta)를 통과하는지 검사한다.
검증 결과는 `data_quality_logs` 로 남긴다 — 몇 건 읽고/통과/탈락(blocking)/경고했는지와
사유를 드러내, 분석에 못 쓰는 뉴스가 조용히 새거나 사라지지 않게 한다(AGENTS Rule 12).

이 스토리(ALPHA-131)는 **검증·로깅까지**다. 게이트를 통과한 행을 `canonical/news/
news_articles` 로 article_id 멱등 병합하는 적재는 후속 **ALPHA-132** 소관이다.

정규화가 흡수하는 벤더 이형(raw 무변형으로 보존된 원본):
  - FMP: title/url/site/publishedDate(오프셋 없는 벽시계)
  - BigKinds: TITLE/PROVIDER_LINK_PAGE/PROVIDER/DATE·NEWS_ID(날짜 단위)
벤더 판별은 raw 키의 source= 파티션으로 한다(레코드 내용 아님 — 키가 규약의 SSOT).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from ..lake import Storage, is_raw_news_key, parse_raw_news_key, quality_log_key
# BigKinds 날짜 파생(bigkinds_date)은 parse 의 벤더 date SSOT — ingest 도 같은 함수를 써
# raw 파티션 published_date 와 canonical published_at 이 드리프트하지 않는다.
from ..parse import bigkinds_date, make_article_id, normalize_url, parse_datetime
from ..quality import BLOCKING_REASONS, validate_news_meta

logger = logging.getLogger(__name__)

JOB_NAME = "normalize_news"
DATASET = "news_articles"

# 발행일 상한 여유 — 검증 실행일 기준 이 일수까지의 미래 발행일은 허용(수집 지연·TZ 여유).
_FUTURE_SLACK_DAYS = 2


def _text(record: dict, key: str) -> str | None:
    """문자열 필드 안전 추출 — 비문자열(int·list 등)은 None 으로 정리한다. 정규화 다운스트림
    (strip·normalize_url·parse_datetime)이 비str 에서 크래시하는 걸 막고(crash-before-gate),
    결측은 게이트가 사유로 잡게 한다(Rule 12)."""
    value = record.get(key)
    return value if isinstance(value, str) else None


def _normalize(vendor: str, record: dict) -> dict:
    """벤더 raw 뉴스행 → 표준 메타행. 비문자열/결측은 None 으로 정리(게이트가 사유로 잡음).

    게이트가 보는 필드(title·normalized_url·published_at·publisher)를 벤더 무관 표준행으로
    수렴시킨다 — 정제의 존재 이유(FMP·BigKinds 이형 흡수).
    """
    is_bigkinds = vendor == "bigkinds"
    if is_bigkinds:
        title = _text(record, "TITLE")
        url = _text(record, "PROVIDER_LINK_PAGE")
        publisher = _text(record, "PROVIDER")
        market = "KR"
        # BigKinds 발행시각은 날짜 단위(시각 없음) — bigkinds_date 가 None 이면 가짜 문자열을
        # 조립하지 않고 그대로 None → parse_datetime(None)=None → 게이트가 unparseable 로 잡음.
        published_at = parse_datetime(bigkinds_date(record))
    else:  # fmp
        title = _text(record, "title")
        url = _text(record, "url")
        publisher = _text(record, "site")
        market = _text(record, "market")
        published_at = parse_datetime(_text(record, "publishedDate"))

    # article_id 는 ingest 가 raw 에 이미 심었다(FMP·BigKinds 둘 다) — 없으면(구 raw 등)
    # parse.make_article_id 로 안정 재계산(항상 non-empty).
    article_id = record.get("article_id")
    if not (isinstance(article_id, str) and article_id):
        article_id = make_article_id(url, title or "", published_at)

    return {
        "article_id": article_id,
        "source_vendor": vendor,
        "market": market,
        "title": " ".join(title.split()) if title else None,  # 공백 정규화(제목 dedup 안정)
        "url": url,
        "normalized_url": normalize_url(url),
        "published_at": published_at,
        "publisher": publisher.strip() if publisher else None,
    }


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw stock_news → 정규화 → 필수필드 게이트 → quality_log. 성공 0, 스토리지 장애 시 비0.

    input_run_id 지정 시 그 수집 런의 raw 만, 아니면 raw news 전체를 검증한다(멱등).
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]
    # 발행일 상한 = 실행일 + 여유. 파싱되지만 범위 밖인 미래 날짜를 게이트가 잡는 기준.
    max_published_date = (started_at.date() + timedelta(days=_FUTURE_SLACK_DAYS)).isoformat()

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_news_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = passed = 0
    failures: list[dict] = []  # blocking — canonical 제외 대상(적재는 ALPHA-132)
    warnings: list[dict] = []  # non-blocking — 통과하되 결측을 로깅(url·publisher)
    exit_code = 0

    for raw_key in raw_keys:
        try:
            # 키 파싱도 try 안에 둔다 — 규약 밖 키(source= 누락 등)의 KeyError 가 런 전체를
            # 죽이지 않고 이 파티션만 격리되게(가격 정제와 동일한 격리 의도).
            vendor = parse_raw_news_key(raw_key)["source"]
            lines = storage.get_bytes(raw_key).decode("utf-8").splitlines()
        except Exception as exc:
            logger.exception("raw 읽기/키 파싱 실패: %s", raw_key)
            failures.append({"raw_key": raw_key, "reasons": ["raw_read_error"], "error": str(exc)})
            exit_code = 1
            continue
        for line in lines:
            if not line.strip():
                continue
            read += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                failures.append({"raw_key": raw_key, "reasons": ["unparseable_json"]})
                continue
            if not isinstance(record, dict):
                # 유효 JSON 이지만 객체가 아닌 행(null·배열·스칼라)은 _normalize 의 record.get 에서
                # 런 전체를 죽인다 — 행 단위로 격리해 나머지 검증이 완료되게(격리≠은폐, Rule 12).
                failures.append({"raw_key": raw_key, "reasons": ["non_object_row"]})
                continue
            if vendor not in ("fmp", "bigkinds"):
                # 알 수 없는 뉴스 벤더 — 조용히 통과시키지 않고 사유로 드러낸다(Rule 12).
                failures.append({"raw_key": raw_key, "source_vendor": vendor,
                                 "reasons": ["unsupported_vendor"]})
                continue
            try:
                row = _normalize(vendor, record)
                reasons = validate_news_meta(row, max_published_date=max_published_date)
            except Exception as exc:
                # 예기치 못한 행 단위 크래시도 배치를 죽이지 않게 격리한다(항상 quality_log 를
                # 남긴다 — Rule 12).
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue

            ref = {"article_id": row["article_id"], "source_vendor": vendor,
                   "published_at": row["published_at"], "raw_key": raw_key}
            blocking = [r for r in reasons if r in BLOCKING_REASONS]
            if blocking:
                # blocking 이 있으면 canonical(ALPHA-132) 제외 대상 — 경고까지 포함한 전체
                # 사유를 남겨 소스 품질 문제를 한 번에 파악하게 한다.
                failures.append({**ref, "reasons": reasons})
                continue
            passed += 1
            warn = [r for r in reasons if r not in BLOCKING_REASONS]
            if warn:
                # 통과했지만 url·publisher 결측 — canonical 진입은 시키되 provenance 손실을 드러낸다.
                warnings.append({**ref, "reasons": warn})

    try:
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
                "records_warned": len(warnings),
                "failures": failures,
                "warnings": warnings,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        # 품질 로그마저 못 남기면 검증 결과가 통째로 유실된다 — 최소한 비0 종료로 알린다.
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        exit_code = exit_code or 1

    logger.info(
        "normalize_news 완료: raw_files=%d read=%d passed=%d failed=%d warned=%d",
        len(raw_keys), read, passed, len(failures), len(warnings),
    )
    return exit_code
