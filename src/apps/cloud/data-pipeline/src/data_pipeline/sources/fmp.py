"""FMP 뉴스 소스 어댑터 (S002 — 유일하게 구현된 소스).

엔드포인트: {base_url}?symbols={fmp_symbol}&limit={n}&apikey={key}
(base_url 은 설정 news.sources.fmp.base_url — /stable/news/stock. v3/v4 는 폐기됨.)

응답 배열 항목: {symbol, publishedDate, title, site, url, text, ...}.
raw 존에는 항목 원본에 수집 메타(our_ticker/market/fetched_at)만 붙여 그대로 둔다 —
필드 선별·품질검증은 Step2(normalize) 소관.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import NewsSource
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 100
# 심볼·창당 페이지 안전 상한 — 응답이 계속 limit 을 꽉 채워도 무한 순회를 막는다.
MAX_PAGES = 50


def market_for(our_ticker: str) -> str:
    """KR 티커는 숫자로, US 는 알파벳으로 시작한다.

    KR 6자리가 전부 숫자인 건 아니다 — 신규 상장분 단축코드에는 문자가 섞인다(0093A0 등).
    선두만 보는 이 판정은 그 코드도 KR 로 옳게 분류한다(형태 판정은 `parse.krx_short_code`).
    """
    return "KR" if our_ticker[:1].isdigit() else "US"


class FmpNewsSource:
    source_name = "fmp"

    def __init__(
        self, config: NewsSource, client: PoliteClient, limit: int = DEFAULT_LIMIT
    ):
        self.base_url = config.base_url
        self.api_key = config.api_key
        self.config_enabled = config.enabled
        self.symbol_map = config.symbol_map  # our_ticker → FMP 심볼 (설정에서)
        self.client = client
        self.limit = limit
        # fetch 중 심볼 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다.
        # (격리로 남은 심볼은 계속 수집하되, 실패가 조용히 묻히지 않게 — fail loud.)
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한 (매핑된) 대상 수. 활성 소스인데 0이면 심볼맵 누락·
        # 전 대상 미매핑 — 스텝이 success(0건)로 위장하지 않고 skip 으로 드러낸다.
        self.planned_symbols: int | None = None

    @property
    def enabled(self) -> bool:
        # 키는 env 로만 주입된다(커밋 금지) — 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.api_key)

    def request_url(
        self,
        fmp_symbol: str,
        *,
        page: int = 0,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> str:
        # apikey 가 포함되므로 이 URL 을 로그에 남기지 않는다.
        url = f"{self.base_url}?symbols={fmp_symbol}&page={page}&limit={self.limit}"
        if from_date:
            url += f"&from={from_date}"
        if to_date:
            url += f"&to={to_date}"
        return f"{url}&apikey={self.api_key}"

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, fmp_symbol)]. 매핑 없는 심볼은 FMP 로는 제외.

        매핑 없음은 오류가 아니라 정상(검증 안 된 KR ADR 등은 후속 소스가 커버).
        """
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            fmp_symbol = self.symbol_map.get(our_ticker)
            if not fmp_symbol:
                logger.info("fmp 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, fmp_symbol))
        return out

    def _note_failure(
        self, fmp_symbol: str, our_ticker: str, reason: str, *, kind: str = "failure"
    ) -> None:
        """심볼 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("fmp 심볼 건너뜀: %s (%s)", fmp_symbol, reason)
        self.fetch_failures.append(
            {"symbol": fmp_symbol, "our_ticker": our_ticker, "error": reason, "kind": kind}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """심볼별로 [from_date, to_date] 창을 페이지 끝까지 순회해 raw 항목을 낸다.

        날짜창을 안 주면(둘 다 None) 창 필터 없이 순회한다(run 엔트리는 항상 증분
        창을 채우므로 이 경로는 사실상 테스트용). 스케줄 실행은 run 엔트리가 증분
        창(어제~오늘)을, 백필은 명시 창을 넘긴다. 심볼 단위 실패는 격리·기록
        (격리≠은폐), StopFetch(4xx/429)만 전체 중단.
        """
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        fetched_at = datetime.now(timezone.utc).isoformat()
        for our_ticker, fmp_symbol in plan:
            try:
                yield from self._paginate(
                    our_ticker, fmp_symbol, from_date, to_date, fetched_at
                )
            except StopFetch:
                raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
            except Exception as exc:
                # 요청 실패·깨진 JSON·비배열 응답은 심볼 단위로 격리 — 남은 심볼 계속.
                self._note_failure(fmp_symbol, our_ticker, str(exc))
                continue

    def _paginate(
        self,
        our_ticker: str,
        fmp_symbol: str,
        from_date: str | None,
        to_date: str | None,
        fetched_at: str,
    ) -> Iterator[dict]:
        """한 심볼의 창을 page 0..N 순회. 마지막 페이지(빈/limit 미만)에서 멈춘다.

        페이지 실패는 예외로 올려 호출부가 심볼 단위로 격리한다(앞 페이지 수집분은
        보존). MAX_PAGES 안전 상한으로 무한 페이지를 막되, 상한에 걸려 창이 절단되면
        조용히 버리지 않고 실패로 기록한다(fail loud)."""
        for page in range(MAX_PAGES):
            body = self.client.get(
                self.request_url(fmp_symbol, page=page, from_date=from_date, to_date=to_date)
            )
            try:
                payload = json.loads(body) if body else []
            except json.JSONDecodeError as exc:
                raise ValueError(f"json: {exc}") from exc  # → 심볼 단위 실패
            if not isinstance(payload, list):
                raise ValueError("response not a list")
            if not payload:
                return  # 마지막 페이지(빈 응답)
            for item in payload:
                # list 안에 null/문자열/숫자가 섞여도 dict(item) 예외로 남은 수집이
                # 끊기지 않게 — 불량 item 은 기록 후 스킵.
                if not isinstance(item, dict):
                    self._note_failure(
                        fmp_symbol, our_ticker, f"malformed item: {type(item).__name__}"
                    )
                    continue
                record = dict(item)
                record["our_ticker"] = our_ticker
                record["market"] = market_for(our_ticker)
                record["fetched_at"] = fetched_at
                yield record
            if len(payload) < self.limit:
                return  # 마지막 페이지(limit 미만)
        # 루프가 MAX_PAGES 를 다 돌았는데도 early return 이 없었다 = 마지막 페이지가
        # 꽉 참 → 창에 더 남았을 수 있다. 조용히 버리지 않고 실패로 기록해 런을 partial
        # 로 드러낸다(백필 창을 좁히거나 MAX_PAGES 를 올려 재실행하라는 신호).
        self._note_failure(
            fmp_symbol, our_ticker, f"MAX_PAGES({MAX_PAGES}) 도달 — 창 절단 가능(구간 좁혀 재실행)",
            kind="truncation",
        )
