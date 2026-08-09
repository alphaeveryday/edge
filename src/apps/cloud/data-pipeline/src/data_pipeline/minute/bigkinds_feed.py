"""BigKinds 1분 feed adapter (ALPHA-707, 계획 §3).

`NewsWorker` 의 feed 계약(`fetch_page(poll_index, page, page_size)`)에 BigKinds 실호출을
붙이는 첫 프로덕션 구현이다 — 지금까지 유일한 구현은 `FakeNewsFeed` 였다.

요청 형상은 `sources.bigkinds.search_page` 가 정본이다(배치와 공유 — UA·필드가 한쪽만
고쳐지는 드리프트 방지). 이 모듈이 더하는 건 둘뿐이다:

- **feed 계약 변환** — 1-base page → 0-base, `resultList` → `NewsPage(rows, is_last)`.
  날짜창은 **세션 날짜 하루**로 고정한다(세션 축과 조회 축이 갈리면 자정 부근에
  어제 기사가 오늘 세션 관측으로 섞인다 — 배치의 창 겹침과 달리 여긴 원장이 세션별이다).
- **차단 분류(fail loud)** — ALPHA-645 실측 시그니처(400+HTML 본문·403·429)를
  `BlockedFeedError` 로 가른다. 일반 실패와 차단은 처방이 다르다: 일반 실패는 lease
  만료 재시도로 낫지만, 차단은 재시도가 차단을 **연장**하므로 운영자가 pacing 을
  낮추거나 서비스를 내려야 한다(기존 배치 레인은 같은 IP 라 폴백이 아니다 — 티켓).

pacing 은 여기 없다 — `PoliteClient.min_interval` 이 요청 간격의 정본이고, 그 값은
설정(`minute_news_worker.min_interval_sec`)에서 온다(배포 없이 env 로 되돌림).

⚠️ **정렬·시각 실측(2026-08-03 스파이크, ALPHA-645)**: 행의 `DATE` 는 일 단위지만
`NEWS_ID` 에 초 단위 등록시각이 박혀 있고(`{provider}.{YYYYMMDDHHMMSS}{seq}`) 응답은
그 시각 내림차순이다. 다만 이것은 관측이지 벤더 계약이 아니다 — 신규 판정의 권위는
여전히 원장(`NewsSourceLedger.observe`)이고, 이 adapter 는 순서에 아무 가정도 얹지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..sources.bigkinds import search_page
from ..sources.http import StopFetch
from .news_overlap import NewsPage

logger = logging.getLogger(__name__)

# 차단으로 분류하는 HTTP 상태 — 400 은 본문이 HTML 일 때만(파라미터 오류도 400 이라,
# 전부 차단으로 접으면 코드 결함이 "벤더 차단"으로 오진돼 고칠 사람이 pacing 만 낮춘다).
_BLOCK_STATUSES = frozenset({403, 429})
_HTML_MARKERS = ("<html", "<!doctype")


class BlockedFeedError(RuntimeError):
    """벤더 차단 시그니처 — 재시도로 낫지 않는다. pacing 하향·중지가 처방이다."""


@dataclass
class BigKindsMinuteFeed:
    """세션 날짜 하루짜리 최신순 페이지 조회. 상태 없음 — poll_index 는 쓰지 않는다
    (anchor·따라잡기는 Worker/원장 소관이고, BigKinds 에는 시점 파라미터가 없다)."""

    client: object  # PoliteClient — min_interval(pacing)·timeout 이 설정에서 주입된다
    base_url: str
    category_codes: tuple[str, ...]
    session_date: str  # YYYY-MM-DD — 세션 축과 같은 날짜만 조회

    def fetch_page(self, poll_index: int, page: int, page_size: int) -> NewsPage:
        if page < 1 or page_size < 1:
            raise ValueError("page 는 1-base, page_size 는 양수다")
        try:
            payload = search_page(
                self.client, base_url=self.base_url,
                category_codes=list(self.category_codes),
                start_date=self.session_date, end_date=self.session_date,
                page=page - 1, page_size=page_size,
            )
        except StopFetch as error:
            body = (error.body or "").lstrip().lower()
            if error.status in _BLOCK_STATUSES or (
                error.status == 400 and body.startswith(_HTML_MARKERS)
            ):
                # 크게 가른다 — Worker 는 이 poll 을 실패로 접고 lease 재시도하지만,
                # 로그의 이 이름이 운영자에게 "재시도가 아니라 pacing" 을 말한다.
                raise BlockedFeedError(
                    f"BigKinds 차단 시그니처(HTTP {error.status}) — 재시도 금지, "
                    f"pacing 하향 또는 news-worker 중지: {body[:200]}"
                ) from error
            raise
        rows = payload.get("resultList")
        if not isinstance(rows, list):
            # 없는 것과 빈 것을 가른다 — 형상 밖 응답을 빈 페이지로 접으면 그 poll 이
            # "그 시각엔 기사 없음" 으로 원장에 남는다(안 본 것이 0건이 되는 자리).
            raise ValueError(
                f"BigKinds resultList 가 목록이 아니다: {type(rows).__name__}"
            )
        return NewsPage(
            rows=tuple(rows),
            # 벤더 신호(isLimitPage) 또는 **빈 페이지**만 마지막이다 — 미달(short) 페이지는
            # 아니다. 배치 `_paginate` 의 명시 계약과 동일: BigKinds 가 soft cap·서버측
            # dedup 으로 100 미만을 주고도 다음 페이지에 기사가 더 있을 수 있어, 미달을
            # 마지막으로 접으면 그 뒤 기사가 이번 poll 관측에서 영구히 빠진다.
            is_last=bool(payload.get("isLimitPage")) or not rows,
        )
