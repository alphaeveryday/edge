"""FMP 가격(OHLCV) 소스 어댑터 (S004 — 가격 Step1 원본저장).

엔드포인트: {base_url}?symbol={fmp_symbol}&from={from}&to={to}&apikey={key}
(base_url 은 설정 price.source.base_url — /stable/historical-price-eod/full.)

뉴스 어댑터(fmp.py)와 대칭이되, 가격 EOD 는 심볼·창당 한 번의 호출로 해당 구간의
일봉 배열을 통째로 돌려준다(뉴스처럼 페이지네이션하지 않는다). 응답 항목은
{symbol, date, open, high, low, close, volume, adjClose, ...}. raw 존에는 항목
원본에 수집 메타(our_ticker/market/fmp_symbol/fetched_at)만 붙여 그대로 둔다 —
필드 선별·정규화는 후속(canonical/market_data) 소관.

심볼맵은 price.source 자체 맵을 쓴다(뉴스와 공유하지 않는다) — 뉴스는 ADR 매핑도
'그 회사 뉴스'라 맞지만, 가격은 ADR 의 USD 시세를 KR 종목 가격으로 쓰면 통화·거래
시간이 어긋나 다운스트림이 오염된다. 거래소-로컬 심볼만 두고 없으면 건너뛴다.
api_key 는 price.source 자체 env 로 주입한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import PriceSource
from .fmp import market_for  # KR/US 분류는 FMP 벤더 무관 공통 규약 — 재정의하지 않는다
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)


class FmpPriceSource:
    source_name = "fmp"

    def __init__(self, config: PriceSource, client: PoliteClient):
        self.base_url = config.base_url
        self.api_key = config.api_key
        self.config_enabled = config.enabled
        # our_ticker → FMP 심볼. 가격 전용 맵(뉴스와 별개 — 위 모듈 주석 참고).
        self.symbol_map = config.symbol_map
        self.client = client
        # 심볼 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다(격리≠은폐).
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한 (매핑된) 대상 수. 활성 소스인데 0이면 심볼맵 누락·
        # 전 대상 미매핑 — 스텝이 success(0건)로 위장하지 않고 skip 으로 드러낸다.
        self.planned_symbols: int | None = None

    @property
    def enabled(self) -> bool:
        # 키는 env 로만 주입된다(커밋 금지) — 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.api_key)

    def request_url(
        self, fmp_symbol: str, *, from_date: str | None = None, to_date: str | None = None
    ) -> str:
        # apikey 가 포함되므로 이 URL 을 로그에 남기지 않는다.
        url = f"{self.base_url}?symbol={fmp_symbol}"
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
                logger.info("fmp 가격 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, fmp_symbol))
        return out

    def _note_failure(self, fmp_symbol: str, our_ticker: str, reason: str) -> None:
        """심볼 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("fmp 가격 심볼 건너뜀: %s (%s)", fmp_symbol, reason)
        self.fetch_failures.append(
            {"symbol": fmp_symbol, "our_ticker": our_ticker, "error": reason}
        )

    def fetch(
        self,
        symbols: list[str],
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> Iterator[dict]:
        """심볼별로 [from_date, to_date] 창의 일봉을 낸다(심볼당 1콜).

        심볼 단위 실패(요청 실패·깨진 JSON·비배열 응답)는 격리·기록하고 남은 심볼을
        계속 수집한다(격리≠은폐). StopFetch(4xx/429)만 소스 전체를 중단한다
        (키·쿼터 문제라 재시도·격리 대상이 아니다).
        """
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        fetched_at = datetime.now(timezone.utc).isoformat()
        for our_ticker, fmp_symbol in plan:
            try:
                yield from self._fetch_symbol(
                    our_ticker, fmp_symbol, from_date, to_date, fetched_at
                )
            except StopFetch:
                raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
            except Exception as exc:
                # 요청 실패·깨진 JSON·비배열 응답은 심볼 단위로 격리 — 남은 심볼 계속.
                self._note_failure(fmp_symbol, our_ticker, str(exc))
                continue

    def _fetch_symbol(
        self,
        our_ticker: str,
        fmp_symbol: str,
        from_date: str | None,
        to_date: str | None,
        fetched_at: str,
    ) -> Iterator[dict]:
        """한 심볼의 창을 한 번 호출해 일봉 행을 낸다.

        실패는 예외로 올려 호출부가 심볼 단위로 격리한다. FMP EOD 는 평평한 배열
        또는 {symbol, historical:[...]} 두 형태로 오므로 둘 다 방어한다.
        """
        body = self.client.get(self.request_url(fmp_symbol, from_date=from_date, to_date=to_date))
        try:
            payload = json.loads(body) if body else []
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc  # → 심볼 단위 실패
        if isinstance(payload, dict):
            # {symbol, historical:[...]} 형태만 정상. historical 키가 없는 dict 는 HTTP
            # 200 에러 객체({"Error Message": ...}·쿼터 초과)다 — get(…, []) 로 조용히
            # 0행 처리하면 전 심볼이 그래도 run 이 success(0건)로 위장한다. 심볼 실패로 올린다.
            if "historical" not in payload:
                raise ValueError("response object without 'historical'")
            rows = payload["historical"]
        elif isinstance(payload, list):
            rows = payload
        else:
            raise ValueError("response not a list/object")
        market = market_for(our_ticker)
        for row in rows:
            # 배열 안에 null/문자열/숫자가 섞여도 한 행이 남은 수집을 끊지 않게 —
            # 불량 행은 기록 후 스킵(raw 는 정상 행을 최대한 보존).
            if not isinstance(row, dict):
                self._note_failure(
                    fmp_symbol, our_ticker, f"malformed row: {type(row).__name__}"
                )
                continue
            record = dict(row)
            record["our_ticker"] = our_ticker
            record["market"] = market
            record["fmp_symbol"] = fmp_symbol
            record["fetched_at"] = fetched_at
            yield record
