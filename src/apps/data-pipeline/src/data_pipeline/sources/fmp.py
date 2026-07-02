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

DEFAULT_LIMIT = 50


def market_for(our_ticker: str) -> str:
    """KR 티커는 6자리 숫자, US 는 알파벳으로 시작한다."""
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

    @property
    def enabled(self) -> bool:
        # 키는 env 로만 주입된다(커밋 금지) — 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.api_key)

    def request_url(self, fmp_symbol: str) -> str:
        # apikey 가 포함되므로 이 URL 을 로그에 남기지 않는다.
        return f"{self.base_url}?symbols={fmp_symbol}&limit={self.limit}&apikey={self.api_key}"

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

    def _note_failure(self, fmp_symbol: str, our_ticker: str, reason: str) -> None:
        """심볼 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("fmp 심볼 건너뜀: %s (%s)", fmp_symbol, reason)
        self.fetch_failures.append(
            {"symbol": fmp_symbol, "our_ticker": our_ticker, "error": reason}
        )

    def fetch(self, symbols: list[str]) -> Iterator[dict]:
        """심볼별로 질의해 raw 항목(dict)을 낸다. 수집 메타를 항목에 덧붙인다."""
        self.fetch_failures = []
        fetched_at = datetime.now(timezone.utc).isoformat()
        for our_ticker, fmp_symbol in self.plan(symbols):
            try:
                body = self.client.get(self.request_url(fmp_symbol))
            except StopFetch:
                raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
            except Exception as exc:
                # 일시 오류 재시도 소진은 심볼 단위로 격리 — 남은 심볼은 계속.
                # 단, 실패는 기록해 스텝이 런 상태(성공/부분/실패)에 반영한다.
                self._note_failure(fmp_symbol, our_ticker, f"request: {exc}")
                continue
            try:
                payload = json.loads(body) if body else []
            except json.JSONDecodeError as exc:
                # 잘못된 200 응답도 실패다 — 기록 없이 넘기면 전 심볼이 깨진 JSON 을
                # 받아도 런이 '성공(0건)'으로 남는다(조용한 성공 금지).
                self._note_failure(fmp_symbol, our_ticker, f"json: {exc}")
                continue
            if not isinstance(payload, list):
                self._note_failure(fmp_symbol, our_ticker, "response not a list")
                continue
            for item in payload:
                # list 안에 null/문자열/숫자가 섞여도 dict(item) 예외로 제너레이터가
                # 죽어 남은 심볼 수집이 끊기지 않게 — 불량 item 은 기록 후 스킵.
                if not isinstance(item, dict):
                    self._note_failure(fmp_symbol, our_ticker, f"malformed item: {type(item).__name__}")
                    continue
                record = dict(item)
                record["our_ticker"] = our_ticker
                record["market"] = market_for(our_ticker)
                record["fetched_at"] = fetched_at
                yield record
