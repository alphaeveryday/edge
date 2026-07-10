"""뉴스 정제 Step2 — 정규화 + 필수필드 게이트 + canonical 멱등 병합 (ALPHA-131·132).

raw stock_news(FMP·BigKinds 두 벤더, 이형 스키마)를 읽어 **표준 뉴스 메타행으로
정규화**하고, 필수필드 게이트(quality/news.validate_news_meta)를 통과하는지 검사한다.
검증 결과는 `data_quality_logs` 로 남긴다 — 몇 건 읽고/통과/탈락(blocking)/경고했는지와
사유를 드러내, 분석에 못 쓰는 뉴스가 조용히 새거나 사라지지 않게 한다(AGENTS Rule 12).

게이트를 통과한 행은 `canonical/news/news_articles/published_date=…/` 에 **article_id 정체성
키로 멱등 병합**한다(ALPHA-132). 정체성 `article_id = url_hash(원문 URL)` 은 **소스 무관**이라
(FMP `url`/BigKinds `PROVIDER_LINK_PAGE`) canonical 이 소스를 흡수한 **통합 구조**가 된다 —
source_vendor 는 파티션이 아니라 컬럼(provenance)이고 파티션은 published_date 하나다. canonical 은
run_id 가 없어 같은 raw 를 몇 번 정제해도 결과가 같다. 같은 article_id 재적재는 최신 fetched_at 이
메타 대표를 이기되 mentions 는 union 한다. 다른 article_id 가 **같은 정규화 제목**이면 근접중복
신호로 로깅만 한다(URL 충돌은 곧 같은 id 라 자동 병합 → 신호 대상 아님). fuzzy 클러스터는
다운스트림(news_dedup_cluster) 소관이다.

정규화가 흡수하는 벤더 이형(raw 무변형으로 보존된 원본):
  - FMP: title/url/site/publishedDate(오프셋 없는 벽시계)/mentions[]
  - BigKinds: TITLE/PROVIDER_LINK_PAGE/PROVIDER/DATE·NEWS_ID(날짜 단위)/our_ticker
벤더 판별은 raw 키의 source= 파티션으로 한다(레코드 내용 아님 — 키가 규약의 SSOT).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..lake import (
    Storage,
    canonical_news_articles_partition,
    is_raw_news_key,
    parse_raw_news_key,
    quality_log_key,
)
# BigKinds 날짜 파생(bigkinds_date)은 parse 의 벤더 date SSOT — ingest 도 같은 함수를 써
# raw 파티션 published_date 와 canonical published_at 이 드리프트하지 않는다.
from ..parse import bigkinds_date, news_article_id, normalize_url, parse_datetime, url_hash
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


def _mentions(record: dict, market: object) -> list[dict]:
    """canonical 에 보존할 종목 mention 목록(다운스트림 엔티티 링크 씨앗). FMP 는 ingest 가
    병합한 mentions[] 를, BigKinds 는 단일 our_ticker 를 [{market,ticker}] 로 합성한다.
    dict 원소만 취해 오염을 막는다(비객체 mention 은 버린다)."""
    existing = record.get("mentions")
    if isinstance(existing, list):
        return [m for m in existing if isinstance(m, dict)]
    ticker = record.get("our_ticker")
    if isinstance(ticker, str) and ticker.strip() and isinstance(market, str):
        return [{"market": market, "ticker": ticker}]
    return []


def _normalize(vendor: str, record: dict) -> dict:
    """벤더 raw 뉴스행 → 표준 메타행. 비문자열/결측은 None 으로 정리(게이트가 사유로 잡음).

    게이트가 보는 필드(title·normalized_url·published_at·publisher)를 벤더 무관 표준행으로
    수렴시킨다 — 정제의 존재 이유(FMP·BigKinds 이형 흡수). canonical 적재용 필드
    (normalized_url_hash·mentions·fetched_at)도 함께 채운다.
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
    # ingest 와 **같은 SSOT**(parse.news_article_id)로 재계산한다. BigKinds 는 NEWS_ID 를 우선하므로
    # 제목·발행일이 같은 별개 기사가 같은 id 로 붕괴하지 않는다(벤더 정체성 규약 일치).
    article_id = record.get("article_id")
    if not (isinstance(article_id, str) and article_id):
        article_id = news_article_id(record)

    return {
        "article_id": article_id,
        "source_vendor": vendor,
        "market": market,
        "title": " ".join(title.split()) if title else None,  # 공백 정규화(제목 dedup 안정)
        "url": url,
        "normalized_url": normalize_url(url),
        "normalized_url_hash": url_hash(url),
        "published_at": published_at,
        "publisher": publisher.strip() if publisher else None,
        "mentions": _mentions(record, market),
        "fetched_at": _text(record, "fetched_at"),
    }


# ── canonical 적재(ALPHA-132) ────────────────────────────
# 명시 스키마로 고정(전 컬럼 string) — pyarrow 추론에 맡기면 all-None 컬럼이 null 타입으로
# 잡혀 기존 파티션과 병합 시 스키마가 충돌한다. mentions 는 JSON 문자열로 직렬화해 저장한다
# (parquet list-of-struct 의 스키마 복잡성·병합 충돌을 피한다 — 다운스트림이 파싱).
_CANONICAL_COLUMNS = (
    "article_id", "source_vendor", "market", "title", "url", "normalized_url",
    "normalized_url_hash", "published_at", "publisher", "mentions", "fetched_at",
)

_OLDEST = datetime.min.replace(tzinfo=timezone.utc)


def _canonical_schema():
    import pyarrow as pa

    return pa.schema([(c, pa.string()) for c in _CANONICAL_COLUMNS])


def _canonical_row(row: dict) -> dict:
    """표준 메타행 → canonical 직렬화 행. mentions(list)를 JSON 문자열로 고정해, 기존 파티션
    (읽으면 문자열)과 신규 행이 병합 내내 같은 표현을 갖게 한다(이중 인코딩 방지)."""
    out = {c: row.get(c) for c in _CANONICAL_COLUMNS}
    out["mentions"] = json.dumps(row.get("mentions") or [], ensure_ascii=False)
    return out


def _union_mentions(*mentions_values: object) -> str:
    """여러 행의 mentions(JSON 문자열 또는 list)를 (market,ticker) 기준 union → **결정적** JSON
    문자열(정렬). BigKinds 는 종목별 질의라 같은 기사(NEWS_ID)가 여러 추적 종목에 걸려 각기 단일
    mention 으로 온다(ingest 가 mention 병합 안 함) — 병합이 통째 교체하면 종목↔기사 링크가
    유실되므로 union 한다. 정렬로 멱등 재실행이 같은 바이트를 낸다."""
    by_key: dict[tuple, dict] = {}
    for value in mentions_values:
        if isinstance(value, str):
            try:
                items = json.loads(value)
            except ValueError:
                items = []
        else:
            items = value or []
        if not isinstance(items, list):
            continue
        for mention in items:
            if isinstance(mention, dict):
                by_key[(str(mention.get("market")), str(mention.get("ticker")))] = mention
    return json.dumps([by_key[k] for k in sorted(by_key)], ensure_ascii=False)


def _read_parquet_rows(data: bytes) -> list[dict]:
    import io
    import pyarrow.parquet as pq

    return pq.read_table(io.BytesIO(data)).to_pylist()


def _write_parquet_rows(rows: list[dict]) -> bytes:
    import io
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pylist(
        [{c: r.get(c) for c in _CANONICAL_COLUMNS} for r in rows], schema=_canonical_schema()
    )
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def _fetched_at(row: dict) -> datetime:
    """'최신 우선' 정렬 키 — 실제 시각으로 비교한다(문자열 비교는 오프셋이 다르면 어긋난다).
    파싱 불가·결측·naive 는 각각 가장 오래된 것/UTC 로 안전 처리(가격 정제와 동형)."""
    text = row.get("fetched_at")
    if not isinstance(text, str):
        return _OLDEST
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return _OLDEST
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _merge_partition(existing: list[dict], new_rows: list[dict]) -> list[dict]:
    """한 (published_date, source_vendor) 파티션을 article_id 키로 멱등 병합. 같은 article_id
    재적재는 최신 fetched_at 이 이기고(정정 반영), 동률이면 신규(멱등 재실행). source_vendor 가
    파티션을 갈라 교차벤더 충돌이 없으므로 가격의 fail-loud 분기는 없다."""
    acc: dict[str, dict] = {}
    for row in [*existing, *new_rows]:
        article_id = row["article_id"]
        prev = acc.get(article_id)
        if prev is None:
            acc[article_id] = row
            continue
        # 최신 fetched_at 이 메타데이터 대표를 이기되, **mentions 는 양쪽 union** 한다 — BigKinds 는
        # 같은 기사가 여러 종목 질의에 걸려 각기 다른 단일 mention 으로 오므로(ingest 가 병합 안 함),
        # 통째 교체하면 종목↔기사 링크가 유실된다. fetched_at 동률이면 신규(멱등).
        winner = row if _fetched_at(row) >= _fetched_at(prev) else prev
        merged = dict(winner)
        merged["mentions"] = _union_mentions(prev.get("mentions"), row.get("mentions"))
        acc[article_id] = merged
    return [acc[a] for a in sorted(acc)]


def _duplicate_signals(rows: list[dict], published_date: str) -> list[dict]:
    """파티션 내 근접중복 신호 — **다른 article_id** 가 같은 **정규화 제목**을 가지면 로깅한다
    (exact 병합은 안 함 — 서로 다른 기사를 붕괴시키면 유실이므로 신호만). URL 은 이제 정체성이라
    같은 URL→같은 article_id→이미 병합이므로 다른 id 로 갈릴 수 없다 → 제목 근접중복만 신호 대상.
    통합 파티션이라 벤더가 섞여도 제목 충돌을 함께 감지한다(교차벤더 near-dup 도 신호로 드러남)."""
    signals: list[dict] = []
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        title = row.get("title")
        if isinstance(title, str) and title.strip():
            groups[title].append(row["article_id"])
    for title, article_ids in groups.items():
        distinct = sorted(set(article_ids))
        if len(distinct) > 1:
            signals.append({
                "published_date": published_date, "basis": "normalized_title",
                "title": title, "article_ids": distinct,
            })
    return signals


def _write_canonical(storage: Storage, passing: list[dict], signals: list[dict]) -> tuple[int, int]:
    """통과 행을 published_date 파티션별로 기존 canonical 과 article_id(원문 URL 해시) 키로 멱등
    병합해 쓴다. source_vendor 는 컬럼이라 벤더가 한 파티션에 섞인다(통합 canonical). 근접중복
    신호는 signals 에 append(호출부가 quality_log 에 반영). 반환: (쓴 파티션 수, 행 수)."""
    by_partition: dict[str, list[dict]] = defaultdict(list)
    for row in passing:
        # 게이트 통과행은 published_at 이 유효(결측·범위밖 아님)라 [:10] 파티션이 결정적(멱등).
        published_date = row["published_at"][:10]
        by_partition[published_date].append(_canonical_row(row))

    parts_written = rows_written = 0
    for published_date, new_rows in sorted(by_partition.items()):
        prefix = canonical_news_articles_partition(published_date)
        # 파티션의 기존 parquet 을 전부 읽어 병합한다. 이 스텝은 항상 part-00000 하나로 되써
        # 멱등을 지킨다(canonical 은 이 스텝만 쓰므로 part-00000 만 존재).
        existing: list[dict] = []
        for key in storage.list_keys(prefix + "/"):
            if key.endswith(".parquet"):
                existing.extend(_read_parquet_rows(storage.get_bytes(key)))
        merged = _merge_partition(existing, new_rows)
        signals.extend(_duplicate_signals(merged, published_date))
        storage.put_bytes(f"{prefix}/part-00000.parquet", _write_parquet_rows(merged))
        parts_written += 1
        rows_written += len(merged)
    return parts_written, rows_written


def run(storage: Storage, run_id: str, input_run_id: str | None = None) -> int:
    """raw stock_news → 정규화 → 게이트 → canonical 멱등 병합 + quality_log. 성공 0, 장애 시 비0.

    input_run_id 지정 시 그 수집 런의 raw 만 **재검증**한다(canonical 은 안 씀 — 전체 런이
    authoritative). 미지정이면 raw news 전체를 검증하고 canonical 을 멱등 적재한다.
    """
    started_at = datetime.now(timezone.utc)
    checked_date = started_at.isoformat()[:10]
    # 발행일 상한 = 실행일 + 여유. 파싱되지만 범위 밖인 미래 날짜를 게이트가 잡는 기준.
    max_published_date = (started_at.date() + timedelta(days=_FUTURE_SLACK_DAYS)).isoformat()

    raw_keys = [k for k in storage.list_keys("raw/") if is_raw_news_key(k)]
    if input_run_id is not None:
        raw_keys = [k for k in raw_keys if f"/run_id={input_run_id}/" in k]

    read = 0
    failures: list[dict] = []  # blocking — canonical 제외 대상
    warnings: list[dict] = []  # non-blocking — 통과하되 결측을 로깅(url·publisher)
    passing: list[dict] = []  # 게이트 통과 행 — 루프 뒤 canonical 로 멱등 병합
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
                # 예기치 못한 행 단위 크래시도 배치를 죽이지 않게 격리한다(항상 quality_log — Rule 12).
                logger.exception("행 정규화 실패(격리): %s", raw_key)
                failures.append({"raw_key": raw_key, "reasons": ["row_error"], "error": str(exc)})
                continue

            ref = {"article_id": row["article_id"], "source_vendor": vendor,
                   "published_at": row["published_at"], "raw_key": raw_key}
            blocking = [r for r in reasons if r in BLOCKING_REASONS]
            if blocking:
                # blocking 이 있으면 canonical 제외 대상 — 경고까지 포함한 전체 사유를 남겨
                # 소스 품질 문제를 한 번에 파악하게 한다.
                failures.append({**ref, "reasons": reasons})
                continue
            passing.append(row)
            warn = [r for r in reasons if r not in BLOCKING_REASONS]
            if warn:
                # 통과했지만 url·publisher 결측 — canonical 진입은 시키되 provenance 손실을 드러낸다.
                warnings.append({**ref, "reasons": warn})

    # 통과 행을 canonical 로 멱등 병합 — **전체 런(input_run_id=None)만** 쓴다. 스코프 실행은
    # 재검증(quality_log)만 하고 canonical 은 전체 raw 를 보는 멱등 런이 authoritative 하게 쓴다
    # (가격 정제와 동형 — 스코프가 부분 파티션을 덮어써 멱등성을 흔들지 않게).
    duplicate_signals: list[dict] = []
    parts_written = canonical_rows = 0
    canonical_written = input_run_id is None
    if canonical_written:
        try:
            parts_written, canonical_rows = _write_canonical(storage, passing, duplicate_signals)
        except Exception:
            logger.exception("canonical 적재 실패")
            exit_code = 1
    else:
        logger.info("스코프(--input-run-id) 실행 — 재검증만, canonical 은 전체 런이 쓴다")

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
                "records_passed": len(passing),
                "records_failed": len(failures),
                "records_warned": len(warnings),
                "failures": failures,
                "warnings": warnings,
                "canonical_written": canonical_written,
                "canonical_partitions_written": parts_written,
                "canonical_rows_written": canonical_rows,
                "duplicate_signals": duplicate_signals,
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False).encode("utf-8"),
        )
    except Exception:
        # 품질 로그마저 못 남기면 검증 결과가 통째로 유실된다 — 최소한 비0 종료로 알린다.
        logger.exception("quality_log 기록 실패 — 검증 결과 유실")
        exit_code = exit_code or 1

    logger.info(
        "normalize_news 완료: raw_files=%d read=%d passed=%d failed=%d warned=%d "
        "canonical_parts=%d canonical_rows=%d dup_signals=%d",
        len(raw_keys), read, len(passing), len(failures), len(warnings),
        parts_written, canonical_rows, len(duplicate_signals),
    )
    return exit_code
