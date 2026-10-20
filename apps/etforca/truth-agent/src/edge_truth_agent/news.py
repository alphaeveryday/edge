"""기존 뉴스 레인이 레이크에 쌓은 기사를 읽는다. 새로 수집하지 않는다.

- 1분 원본: `raw/source=bigkinds/dataset=news_minute/...` (평일 장중, 1분마다 쌓임)
- 정제본: `canonical/news/news_articles/language=ko/published_date=...` (하루 2회 배치)

키 규약과 기사 정체성은 data-pipeline 의 함수를 그대로 쓴다(`raw_news_minute_page_key`,
`canonical_news_articles_partition`, `news_article_id`, `bigkinds_datetime`). 저장된 본문은 제목과
200자 안팎의 요약뿐이다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import io
import json
import logging

from data_pipeline.lake.storage import canonical_news_articles_partition, raw_news_minute_page_key
from data_pipeline.parse import bigkinds_datetime, news_article_id

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Article:
    """사건 배정·검색에 쓰는 기사 한 건."""

    id: str
    title: str
    lead: str
    url: str
    publisher: str
    published: str  # 'YYYY-MM-DD HH:MM:SS' 또는 'YYYY-MM-DD'

    @property
    def source_id(self) -> str:
        """사건 sources 에 쓰는 출처 id."""
        return f"news-{self.id[:16]}"

    def to_dict(self) -> dict:
        """JSON 으로 저장할 형태."""
        return asdict(self)


def from_bigkinds(record: dict) -> Article | None:
    """BigKinds 원본 한 줄. 제목이 없으면 버린다(data-pipeline 정제도 제목 없는 행을 막는다)."""
    title = " ".join(str(record.get("TITLE") or "").split())
    if not title:
        return None
    return Article(
        id=news_article_id(record), title=title,
        lead=" ".join(str(record.get("CONTENT") or "").split()),
        url=str(record.get("PROVIDER_LINK_PAGE") or ""), publisher=str(record.get("PROVIDER") or ""),
        published=bigkinds_datetime(record) or "",
    )


def from_canonical(row: dict) -> Article | None:
    """정제본 한 행."""
    if not row.get("article_id") or not row.get("title"):
        return None
    return Article(id=row["article_id"], title=row["title"], lead=row.get("lead_text") or "",
                   url=row.get("url") or "", publisher=row.get("publisher") or "", published=row.get("published_at") or "")


def minute_prefix(session_date: str) -> str:
    """하루치 1분 원본 키의 공통 접두어 (data-pipeline 키 함수에서 잘라 쓴다)."""
    key = raw_news_minute_page_key("bigkinds", "KR", session_date, "0000", 0, 0)
    return key.split("/window=")[0] + "/"


class LakeNews:
    """레이크 S3 읽기. 읽기 권한만 있으면 된다."""

    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        if client is None:
            import boto3
            client = boto3.client("s3")
        self.client = client

    def minute_keys(self, session_date: str) -> list[str]:
        """그날 1분 원본 page 키 전체."""
        keys = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=minute_prefix(session_date)):
            keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".ndjson")]
        return keys

    def read_minute(self, key: str) -> list[Article]:
        """1분 원본 page 하나."""
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read().decode("utf-8")
        out = []
        for line in body.split("\n"):
            if line.strip():
                if article := from_bigkinds(json.loads(line)):
                    out.append(article)
        return out

    def canonical(self, published_date: str, known_etag: str | None) -> tuple[list[Article] | None, str | None]:
        """정제본 하루치. 버전이 known_etag 와 같으면 (None, etag) — 다시 읽지 않는다. 파티션이 없으면 ([], None)."""
        key = f"{canonical_news_articles_partition('ko', published_date)}/part-00000.parquet"
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as error:
            if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return [], None
            raise
        if head["ETag"] == known_etag:
            return None, known_etag
        import pyarrow.parquet as pq
        data = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        rows = pq.read_table(io.BytesIO(data), columns=["article_id", "title", "url", "published_at", "publisher", "lead_text"]).to_pylist()
        return [a for a in map(from_canonical, rows) if a], head["ETag"]


def collect(lake, state: dict, today: date, backfill_days: int) -> list[Article]:
    """지난 실행 이후 레이크에 생긴 기사를 모은다. state 에 읽은 위치를 적는다(호출자가 저장).

    1분 원본은 이미 읽은 page 키를, 정제본은 날짜별 버전을 기억해 다시 읽지 않는다.
    처음 실행이면 정제본 backfill_days 일치를 한 번 읽어 검색 말뭉치를 채운다.
    """
    seen_keys: dict[str, list[str]] = state.setdefault("minuteKeys", {})
    etags: dict[str, str] = state.setdefault("canonicalEtags", {})
    found: dict[str, Article] = {}

    days = [today - timedelta(days=i) for i in range(2)]
    for day in days:
        d = day.isoformat()
        known = set(seen_keys.get(d, []))
        fresh = [k for k in lake.minute_keys(d) if k not in known]
        for key in fresh:
            for article in lake.read_minute(key):
                found.setdefault(article.id, article)
        seen_keys[d] = sorted(known | set(fresh))

    span = backfill_days if not state.get("backfilled") else 2
    for i in range(span):
        d = (today - timedelta(days=i)).isoformat()
        articles, etag = lake.canonical(d, etags.get(d))
        if articles:
            for article in articles:
                found.setdefault(article.id, article)
        if etag:
            etags[d] = etag
    state["backfilled"] = True

    keep = {(today - timedelta(days=i)).isoformat() for i in range(max(backfill_days, 3))}
    state["minuteKeys"] = {d: v for d, v in seen_keys.items() if d in keep}
    state["canonicalEtags"] = {d: v for d, v in etags.items() if d in keep}
    log.info(json.dumps({"event": "news_collected", "articles": len(found)}))
    return list(found.values())
