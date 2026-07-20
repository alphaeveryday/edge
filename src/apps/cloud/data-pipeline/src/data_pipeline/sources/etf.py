"""FMP ETF 구성종목(holdings) 소스 어댑터 (ALPHA-337 — US ETF Step1 원본저장).

엔드포인트: {base_url}?symbol={fmp_symbol}&apikey={key}
(base_url 은 설정 etf.source.base_url — /stable/etf/holdings.)

가격 어댑터(fmp_price.py)와 대칭이되, ETF holdings 는 스냅샷이라 날짜창이 없다 —
심볼(ETF)당 한 번의 호출로 현재 구성종목 배열을 통째로 돌려준다(뉴스처럼 페이지네이션
하지 않는다). 응답 항목(라이브 실측): {symbol(=ETF), asset(=구성종목), name, isin,
securityCusip, sharesNumber, weightPercentage, marketValue, updatedAt}. raw 존에는 항목
원본에 수집 메타(our_etf_id/market/fetched_at)만 붙여 그대로 둔다 — 필드 선별·정규화는
후속(canonical/etf_holdings, ALPHA-343) 소관이다. 벤더 기준일(updatedAt)도 무변형 보존한다.

수집 대상은 종목 유니버스(targets)가 아니라 **ETF 목록**이라, etf_map(우리 별개 맵)의 키가
곧 수집 유니버스다 — 가격/재무의 symbol_map(종목)과 공유하지 않는다. 매핑 없는 ETF 는 수집
하지 않는다(생략 = 제외). KR ETF 는 FMP 커버리지 밖이라 여기 두지 않는다(후속 별도 벤더).
api_key 는 etf.source 자체 env 로 주입한다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import EtfSource
from .fmp import market_for  # KR/US 분류는 FMP 벤더 무관 공통 규약 — 재정의하지 않는다
from .http import PoliteClient, StopFetch

logger = logging.getLogger(__name__)


class FmpEtfSource:
    source_name = "fmp"

    def __init__(self, config: EtfSource, client: PoliteClient):
        self.base_url = config.base_url
        self.api_key = config.api_key
        self.config_enabled = config.enabled
        # our_etf_id → FMP ETF 심볼. 종목맵과 별개 — 이 맵의 키가 곧 수집 유니버스다.
        self.etf_map = config.etf_map
        self.client = client
        # ETF 단위로 격리한 실패를 여기 쌓아 스텝이 런 로그에 반영한다(격리≠은폐).
        self.fetch_failures: list[dict] = []
        # 직전 fetch 가 계획한 (매핑된) 대상 수. 활성 소스인데 0이면 etf_map 누락 —
        # 스텝이 success(0건)로 위장하지 않고 skip 으로 드러낸다.
        self.planned_etfs: int | None = None

    @property
    def enabled(self) -> bool:
        # 키는 env 로만 주입된다(커밋 금지) — 없으면 이 소스는 건너뛴다.
        return self.config_enabled and bool(self.api_key)

    def request_url(self, fmp_symbol: str) -> str:
        # apikey 가 포함되므로 이 URL 을 로그에 남기지 않는다.
        return f"{self.base_url}?symbol={fmp_symbol}&apikey={self.api_key}"

    def plan(self) -> list[tuple[str, str]]:
        """수집 대상 → [(our_etf_id, fmp_symbol)]. etf_map 이 곧 유니버스다.

        가격/재무는 targets(종목)와 symbol_map(번역)이 분리되지만, ETF 는 수집 대상이
        곧 ETF 목록이라 etf_map 의 키가 유니버스다 — 별도 targets 개념이 없다.
        """
        return sorted(self.etf_map.items())

    def _note_failure(self, fmp_symbol: str, our_etf_id: str, reason: str) -> None:
        """ETF 단위 실패를 로그로 남기고 fetch_failures 에 기록(격리≠은폐)."""
        logger.warning("fmp ETF 건너뜀: %s (%s)", fmp_symbol, reason)
        self.fetch_failures.append(
            {"symbol": fmp_symbol, "our_etf_id": our_etf_id, "error": reason}
        )

    def fetch(self) -> Iterator[dict]:
        """ETF 별로 현재 구성종목(holdings) 행을 낸다(ETF 당 1콜, 스냅샷).

        ETF 단위 실패(요청 실패·깨진 JSON·비배열/에러객체 응답·빈 holdings)는 격리·기록
        하고 남은 ETF 를 계속 수집한다(격리≠은폐). StopFetch(4xx/429)만 소스 전체를 중단
        한다(키·쿼터 문제라 재시도·격리 대상이 아니다).
        """
        self.fetch_failures = []
        plan = self.plan()
        self.planned_etfs = len(plan)  # 빈 plan(매핑 대상 0)을 스텝이 감지하게
        fetched_at = datetime.now(timezone.utc).isoformat()
        for our_etf_id, fmp_symbol in plan:
            try:
                yield from self._fetch_etf(our_etf_id, fmp_symbol, fetched_at)
            except StopFetch:
                raise  # 4xx/429 는 소스 전체 문제(키·쿼터) — 중단이 맞다
            except Exception as exc:
                # 요청 실패·깨진 JSON·비배열/빈 응답은 ETF 단위로 격리 — 남은 ETF 계속.
                self._note_failure(fmp_symbol, our_etf_id, str(exc))
                continue

    def _fetch_etf(
        self, our_etf_id: str, fmp_symbol: str, fetched_at: str
    ) -> Iterator[dict]:
        """한 ETF 의 구성종목을 한 번 호출해 holdings 행을 낸다.

        실패는 예외로 올려 호출부가 ETF 단위로 격리한다. FMP holdings 는 평평한 배열로
        온다 — dict(에러객체) 나 빈 배열은 정상 holdings 가 아니라 실패로 올린다(ETF 는
        정의상 구성종목이 있으므로 0건은 오류다 — 종목의 '뉴스 없음'과 다르다).
        """
        body = self.client.get(self.request_url(fmp_symbol))
        try:
            payload = json.loads(body) if body else []
        except json.JSONDecodeError as exc:
            raise ValueError(f"json: {exc}") from exc  # → ETF 단위 실패
        if not isinstance(payload, list):
            # HTTP 200 에러 객체({"Error Message": ...}·쿼터 초과)를 조용히 0행 처리하면
            # 런이 success(0건)로 위장한다 — ETF 실패로 올린다.
            raise ValueError(f"response not a list: {type(payload).__name__}")
        if not payload:
            # 빈 holdings 는 정상 ETF 로는 나올 수 없다(잘못된 심볼·플랜 게이팅·미수록) —
            # 조용히 0행으로 넘기지 않고 fail-loud(격리해 partial 로 드러냄).
            raise ValueError("empty holdings")
        market = market_for(our_etf_id)
        for row in payload:
            # 배열 안에 null/문자열/숫자가 섞여도 한 행이 남은 수집을 끊지 않게 —
            # 불량 행은 기록 후 스킵(raw 는 정상 행을 최대한 보존).
            if not isinstance(row, dict):
                self._note_failure(
                    fmp_symbol, our_etf_id, f"malformed row: {type(row).__name__}"
                )
                continue
            record = dict(row)
            record["our_etf_id"] = our_etf_id
            record["market"] = market
            record["fetched_at"] = fetched_at
            yield record
