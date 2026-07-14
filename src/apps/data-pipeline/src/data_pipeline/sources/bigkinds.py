"""BigKinds 국내 뉴스 소스 어댑터 (stock_news raw).

엔드포인트: POST {base_url} (기본 https://www.bigkinds.or.kr/api/news/search.do)

BigKinds `resultList[]` row 원본을 보존하고 수집 provenance(our_ticker·market·
bigkinds_query·fetched_at)만 붙인다. CONTENT 는 BigKinds 응답 원본 필드다 — 여기서
자르거나 요약하지 않는다. dedup·별칭매칭·본문 전문 크롤은 후속 단계 소관이다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import BigKindsNewsSource as BigKindsNewsSourceConfig
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class BigKindsNewsSource:
    source_name = "bigkinds"
    preserve_all_rows = True  # raw 전량 보존: ingest_raw 의 FMP dedup/mention merge 를 끈다.

    def __init__(self, config: BigKindsNewsSourceConfig, client: PoliteClient):
        self.base_url = config.base_url
        self.config_enabled = config.enabled
        self.page_size = config.page_size
        self.max_pages = config.max_pages
        self.query_map = config.query_map
        self.client = client
        self.fetch_failures: list[dict] = []
        self.planned_symbols: int | None = None

    @property
    def enabled(self) -> bool:
        return self.config_enabled

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, query)]. 매핑 없는 심볼은 BigKinds 로는 제외."""
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            query = self.query_map.get(our_ticker)
            if not query:
                logger.info("bigkinds 검색어 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, query))
        return out

    def _note_failure(
        self, query: str, our_ticker: str, reason: str, *, page: int | None = None,
        kind: str = "failure",
    ) -> None:
        logger.warning("bigkinds 대상 건너뜀: %s page=%s (%s)", query, page, reason)
        self.fetch_failures.append(
            {"symbol": query, "our_ticker": our_ticker, "page": page, "error": reason,
             "kind": kind}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """심볼별 검색어로 날짜창을 페이지네이션해 BigKinds raw row 를 낸다."""
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)
        fetched_at = datetime.now(timezone.utc).isoformat()
        start_date = from_date
        end_date = to_date
        if start_date is None and end_date is None:
            today = datetime.now(timezone.utc).date().isoformat()
            start_date = today
            end_date = today
        for our_ticker, query in plan:
            try:
                yield from self._paginate(our_ticker, query, start_date, end_date, fetched_at)
            except StopFetch:
                raise
            except Exception as exc:
                self._note_failure(query, our_ticker, str(exc))
                continue

    def _paginate(
        self,
        our_ticker: str,
        query: str,
        start_date: str,
        end_date: str,
        fetched_at: str,
    ) -> Iterator[dict]:
        truncated = True
        for page in range(self.max_pages):
            payload = self._search(query, start_date, end_date, page)
            rows = payload.get("resultList")
            if not isinstance(rows, list):
                raise ValueError(f"BigKinds resultList 이상: {type(rows).__name__}")
            if not rows:
                truncated = False
                break
            for row in rows:
                if not isinstance(row, dict):
                    self._note_failure(
                        query, our_ticker, f"malformed row: {type(row).__name__}", page=page
                    )
                    continue
                record = dict(row)
                record["our_ticker"] = our_ticker
                record["market"] = "KR"
                record["bigkinds_query"] = query
                record["fetched_at"] = fetched_at
                yield record
            if payload.get("isLimitPage") or len(rows) < self.page_size:
                truncated = False
                break
        if truncated:
            self._note_failure(
                query,
                our_ticker,
                f"MAX_PAGES({self.max_pages}) 도달 — 창 절단 가능(구간 좁혀 재실행)",
                kind="truncation",
            )

    def _search(
        self, query: str, start_date: str | None, end_date: str | None, page: int
    ) -> dict:
        start_no = page + 1
        body = json.dumps(
            {
                "indexName": "news",
                "searchKey": query,
                "searchFilterType": "1",
                "searchScopeType": "1",
                "searchSortType": "date",
                "sortMethod": "date",
                "startDate": start_date or "",
                "endDate": end_date or "",
                "startNo": start_no,
                "resultNumber": self.page_size,
                "providerCodes": [],
                "categoryCodes": [],
                "incidentCodes": [],
                "dateCodes": [],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        raw = self.client.request(
            "POST",
            self.base_url,
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": UA,
                "Referer": "https://www.bigkinds.or.kr/v2/news/search.do",
                "X-Requested-With": "XMLHttpRequest",
            },
            data=body,
            decode=True,
        )
        if not isinstance(raw, str):
            raise ValueError("BigKinds 응답이 str 이 아님")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"BigKinds 응답이 객체가 아님: {type(payload).__name__}")
        return payload
