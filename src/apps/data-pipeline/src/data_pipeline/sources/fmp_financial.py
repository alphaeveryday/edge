"""FMP 재무제표 소스 어댑터 (S035 — 재무제표 Step1 원본저장).

엔드포인트(3종, base_url 은 /stable 베이스):
    {base_url}/income-statement?symbol=&period=&limit=&apikey=
    {base_url}/balance-sheet-statement?symbol=&period=&limit=&apikey=
    {base_url}/cash-flow-statement?symbol=&period=&limit=&apikey=

가격 어댑터(fmp_price.py)와 같은 인터페이스(enabled·plan·fetch·fetch_failures·
planned_symbols)를 따르되, 재무제표는 심볼·문서·주기의 3중 팬아웃이다(심볼당 3문서 ×
2주기 = 6콜). 각 응답은 최근 N개 회계기간 행의 배열이고, 한 행 = 한 공시 명세다.

raw 존에는 행 원본에 수집 메타 + 공시 정체성(statement_type·period_type·
fiscal_period_end·filing_date)만 붙여 그대로 낸다 — 스텝이 그 정체성으로 객체 키를
만들어 존재검사→신규만 저장한다(중복 없이 매일 폴링). 필드 선별·정규화는 후속 소관.

심볼맵은 financial.source 자체 맵(가격과 같은 정책 — US 거래소-로컬 심볼만, KR 은
FMP 재무 커버리지가 약해 후속 DART 등이 커버). api_key 는 financial.source env 주입.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import FinancialSource
from .fmp import market_for  # KR/US 분류는 FMP 벤더 무관 공통 규약 — 재정의하지 않는다
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)

# 내부 문서 코드 → FMP 엔드포인트 경로. 내부 코드는 raw 키·로그의 statement_type= 이 된다.
STATEMENT_ENDPOINTS = {
    "income_statement": "income-statement",
    "balance_sheet": "balance-sheet-statement",
    "cash_flow": "cash-flow-statement",
}

# 수집 주기별 조회 개수(최근 N기). 분기는 4개 분기×2년치, 연간은 4년치를 기본으로 둔다.
DEFAULT_QUARTERLY_LIMIT = 8
DEFAULT_ANNUAL_LIMIT = 4


class FmpFinancialSource:
    source_name = "fmp"

    def __init__(
        self,
        config: FinancialSource,
        client: PoliteClient,
        quarterly_limit: int = DEFAULT_QUARTERLY_LIMIT,
        annual_limit: int = DEFAULT_ANNUAL_LIMIT,
    ):
        self.base_url = config.base_url.rstrip("/")  # 엔드포인트를 뒤에 붙이므로 정규화
        self.api_key = config.api_key
        self.config_enabled = config.enabled
        self.symbol_map = config.symbol_map  # our_ticker → FMP 심볼 (재무 전용 맵)
        self.client = client
        # (주기, limit) — 어떤 것도 조용히 빠지지 않게 명시. annual + quarter 둘 다 수집.
        self.periods: list[tuple[str, int]] = [
            ("annual", annual_limit),
            ("quarter", quarterly_limit),
        ]
        # 대상(심볼·문서·주기) 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다.
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한 (매핑된) 심볼 수. 활성 소스인데 0이면 심볼맵 누락·전 대상
        # 미매핑 — 스텝이 success(0건)로 위장하지 않고 skip 으로 드러낸다.
        self.planned_symbols: int | None = None

    @property
    def enabled(self) -> bool:
        # 키는 env 로만 주입된다(커밋 금지) — 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.api_key)

    def request_url(self, endpoint: str, fmp_symbol: str, period: str, limit: int) -> str:
        # apikey 가 포함되므로 이 URL 을 로그에 남기지 않는다.
        return (
            f"{self.base_url}/{endpoint}?symbol={fmp_symbol}"
            f"&period={period}&limit={limit}&apikey={self.api_key}"
        )

    def plan(self, symbols: list[str]) -> list[tuple[str, str]]:
        """수집 대상 → [(our_ticker, fmp_symbol)]. 매핑 없는 심볼은 FMP 로는 제외.

        매핑 없음은 오류가 아니라 정상(검증 안 된 KR 등은 후속 소스가 커버).
        """
        out: list[tuple[str, str]] = []
        for our_ticker in symbols:
            fmp_symbol = self.symbol_map.get(our_ticker)
            if not fmp_symbol:
                logger.info("fmp 재무 매핑 없음 — 이 소스는 건너뜀: %s", our_ticker)
                continue
            out.append((our_ticker, fmp_symbol))
        return out

    def _note_failure(
        self, fmp_symbol: str, our_ticker: str, statement_type: str, period: str, reason: str
    ) -> None:
        """대상(심볼·문서·주기) 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning(
            "fmp 재무 대상 건너뜀: %s %s/%s (%s)", fmp_symbol, statement_type, period, reason
        )
        self.fetch_failures.append(
            {
                "symbol": fmp_symbol,
                "our_ticker": our_ticker,
                "statement_type": statement_type,
                "period": period,
                "error": reason,
            }
        )

    def fetch(self, symbols: list[str]) -> Iterator[dict]:
        """심볼 × 문서(3) × 주기(2)로 최근 회계기간 명세 행을 낸다.

        대상(심볼·문서·주기) 단위 실패(요청 실패·깨진 JSON·비배열 응답)는 격리·기록하고
        남은 대상을 계속 수집한다(격리≠은폐). StopFetch(4xx/429)만 소스 전체를 중단한다
        (키·쿼터 문제라 재시도·격리 대상이 아니다).
        """
        self.fetch_failures = []
        plan = self.plan(symbols)
        self.planned_symbols = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        fetched_at = datetime.now(timezone.utc).isoformat()
        for our_ticker, fmp_symbol in plan:
            market = market_for(our_ticker)
            for statement_type, endpoint in STATEMENT_ENDPOINTS.items():
                for period, limit in self.periods:
                    try:
                        yield from self._fetch_statement(
                            our_ticker, fmp_symbol, market, statement_type,
                            endpoint, period, limit, fetched_at,
                        )
                    except StopFetch:
                        raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
                    except Exception as exc:
                        # 요청 실패·깨진 JSON·비배열 응답은 대상 단위로 격리 — 남은 대상 계속.
                        self._note_failure(fmp_symbol, our_ticker, statement_type, period, str(exc))
                        continue

    def _fetch_statement(
        self,
        our_ticker: str,
        fmp_symbol: str,
        market: str,
        statement_type: str,
        endpoint: str,
        period: str,
        limit: int,
        fetched_at: str,
    ) -> Iterator[dict]:
        """한 (심볼·문서·주기)를 한 번 호출해 회계기간 행을 낸다.

        실패는 예외로 올려 호출부가 대상 단위로 격리한다. FMP 재무 엔드포인트는 행 배열을
        준다 — dict 응답은 200 에러 객체({"Error Message": ...}·쿼터 초과)라 조용한 0행
        처리(success 위장) 대신 실패로 올린다.
        """
        body = self.client.get(self.request_url(endpoint, fmp_symbol, period, limit))
        try:
            payload = json.loads(body) if body else []
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc  # → 대상 단위 실패
        if isinstance(payload, dict):
            # 배열이 정상 — dict 는 HTTP 200 에러 객체다(키·쿼터). 조용히 넘기지 않는다.
            raise ValueError(f"response object (error?): {list(payload)[:3]}")
        if not isinstance(payload, list):
            raise ValueError("response not a list")
        for row in payload:
            # 배열 안에 null/문자열이 섞여도 한 행이 남은 수집을 끊지 않게 — 기록 후 스킵.
            if not isinstance(row, dict):
                self._note_failure(
                    fmp_symbol, our_ticker, statement_type, period,
                    f"malformed row: {type(row).__name__}",
                )
                continue
            fiscal_period_end = row.get("date")
            # FMP 필드명은 'fillingDate'(오타가 실제 필드) — 'filingDate' 도 방어.
            filing_date = row.get("fillingDate") or row.get("filingDate")
            # 공시 정체성(회계기간·공시일)이 없으면 raw 객체 키를 만들 수 없다 — raw 존은 이
            # 키로 멱등·시점보존을 하므로, 키를 못 만드는 행은 조용히 버리지 않고 실패로 남긴다.
            if not fiscal_period_end or not filing_date:
                self._note_failure(
                    fmp_symbol, our_ticker, statement_type, period,
                    "missing date/fillingDate — 공시 정체성 키 불가",
                )
                continue
            record = dict(row)
            record["our_ticker"] = our_ticker
            record["market"] = market
            record["fmp_symbol"] = fmp_symbol
            record["statement_type"] = statement_type
            record["period_type"] = period  # annual|quarter (row["period"]=FY/Q1 과 별개)
            record["fiscal_period_end"] = fiscal_period_end
            record["filing_date"] = filing_date
            record["fetched_at"] = fetched_at
            yield record
