"""BigKinds 국내 뉴스 소스 어댑터 (stock_news raw).

엔드포인트: POST {base_url} (기본 https://www.bigkinds.or.kr/api/news/search.do)

**카테고리 주도 전체 수집(ALPHA-417)**: 검색어 없이(`searchKey=""`) 설정된 카테고리(경제
대분류)의 그날 뉴스 전체를 날짜창 페이지네이션으로 받는다. 종목별 검색어 수집은 유니버스와
어긋나고(질의 맵에 있는 종목만) 유니버스가 바뀌면 과거 뉴스로 소급이 안 됐다 — 종목 매핑은
정규화의 종목명 탐지(ALPHA-416)가 담당하므로, 수집의 일은 "그날의 경제 뉴스 전체"를 raw 로
보존하는 것이다.

BigKinds `resultList[]` row 원본을 보존하고 수집 provenance(market·bigkinds_query·
fetched_at)만 붙인다. CONTENT 는 BigKinds 응답 원본 필드다 — 여기서 자르거나 요약하지
않는다. dedup·본문 전문 크롤은 후속 단계 소관이다.
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
        self.category_codes = list(config.category_codes)
        self.client = client
        self.fetch_failures: list[dict] = []
        # 수집 대상 라벨 — 질의가 아니라 카테고리가 대상이다. provenance(bigkinds_query)와
        # 실패 기록(symbol)에 같은 라벨을 써 "무엇을 받았는지"가 raw 에서 재구성된다.
        self.query_label = "category:" + ",".join(self.category_codes)

    @property
    def enabled(self) -> bool:
        return self.config_enabled

    def _note_failure(self, reason: str, *, page: int | None = None,
                      kind: str = "failure") -> None:
        logger.warning("bigkinds 수집 실패 기록: %s page=%s (%s)", self.query_label, page, reason)
        self.fetch_failures.append(
            {"symbol": self.query_label, "page": page, "error": reason, "kind": kind}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """설정 카테고리 전체 뉴스를 날짜창 페이지네이션으로 낸다. `symbols` 는 받되 쓰지
        않는다(소스 공통 시그니처 — 수집 대상은 심볼이 아니라 카테고리다)."""
        self.fetch_failures = []
        fetched_at = datetime.now(timezone.utc).isoformat()
        start_date = from_date
        end_date = to_date
        if start_date is None and end_date is None:
            today = datetime.now(timezone.utc).date().isoformat()
            start_date = today
            end_date = today
        try:
            yield from self._paginate(start_date, end_date, fetched_at)
        except StopFetch:
            raise
        except Exception as exc:
            # 단일 대상(카테고리)이라 격리할 다음 대상은 없지만, ingest_raw 의 상태 판정
            # (real_failures → saved==0 이면 error, 있으면 partial)에 태우려 기록으로 남긴다.
            self._note_failure(str(exc))

    def _paginate(
        self,
        start_date: str,
        end_date: str,
        fetched_at: str,
    ) -> Iterator[dict]:
        truncated = True
        for page in range(self.max_pages):
            payload = self._search(start_date, end_date, page)
            rows = payload.get("resultList")
            if not isinstance(rows, list):
                raise ValueError(f"BigKinds resultList 이상: {type(rows).__name__}")
            if not rows:
                truncated = False
                break
            for row in rows:
                if not isinstance(row, dict):
                    self._note_failure(f"malformed row: {type(row).__name__}", page=page)
                    continue
                record = dict(row)
                record["market"] = "KR"
                record["bigkinds_query"] = self.query_label
                record["fetched_at"] = fetched_at
                yield record
            # len(rows) < page_size 조기종료는 쓰지 않는다 — page_size 가 API 상한(100)에
            # 붙어 있어, 서버가 요청 수보다 적게 채워 주는 순간(soft cap·서버측 dedup) 뒤
            # 페이지가 있는데도 조용히 멈춰 success 로 위장된다(미수집 은폐). 종료는 명시
            # 신호(isLimitPage)나 빈 페이지로만 판정한다 — 비용은 창당 요청 1개 추가뿐.
            if payload.get("isLimitPage"):
                truncated = False
                break
        if truncated:
            self._note_failure(
                f"MAX_PAGES({self.max_pages}) 도달 — 창 절단 가능(구간 좁혀 재실행)",
                kind="truncation",
            )

    def _search(self, start_date: str | None, end_date: str | None, page: int) -> dict:
        start_no = page + 1
        body = json.dumps(
            {
                "indexName": "news",
                # 검색어 없음 — 카테고리가 수집 범위를 정한다(config 가 비카테고리 로드를 거부).
                "searchKey": "",
                "searchFilterType": "1",
                "searchScopeType": "1",
                "searchSortType": "date",
                "sortMethod": "date",
                "startDate": start_date or "",
                "endDate": end_date or "",
                "startNo": start_no,
                "resultNumber": self.page_size,
                "providerCodes": [],
                # 카테고리 대분류 필터(설정). 언론사(providerCodes)는 안 좁힌다 — 전체 언론사.
                "categoryCodes": self.category_codes,
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
