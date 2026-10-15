"""토스 분봉 collector — Price Worker 에 끼우는 vendor 구현 (계획 §9).

`FakePriceCollector` 와 **같은 계약**(`collect(request, now) -> (result, records, manifest)`)
이라 Worker 는 무엇이 끼워졌는지 모른다(PR 4 가 collector 주입식으로 만든 이유).

4분류 판정·결과 조립은 벤더 무관이라 `price_collect` 하나에 산다(ALPHA-735 — KIS collector 와
공유). 여기 남은 건 **토스 응답에서 그 window 의 봉 하나를 얻는 방법**뿐이다.

⚠️ 토스 timestamp 는 **구간의 끝**이라 `봉 ts == window_end` 로 고른다(sources/toss.py).
⚠️⚠️ **처리량이 window 주기를 못 따라간다.** 종목당 1콜 × 348종 ÷ 초당 5회 = **약 70초**인데
   window 는 **60초마다** 새로 생긴다. 이 제약 때문에 1분 레인 기본 벤더는 KIS 로 옮겼다
   (ALPHA-735, 실측 14.8 req/s) — 토스 경로는 대체 소스로 남는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..sources.toss import TossApiError, TossAuthError, TossOpenApiClient
from .models import CollectionRequest, CollectionResult
from .price_collect import Outcome, collect_units, select_window_candle

logger = logging.getLogger(__name__)


@dataclass
class TossPriceCollector:
    """`unit_id` = 토스 symbol(6자리 단축코드) 전제 — 매핑은 유니버스 소관이다."""

    client: TossOpenApiClient
    # 한 window 를 놓쳤을 때 같은 콜로 몇 분까지 거슬러 받을지. 1 이면 딱 그 분만
    # 본다. 값을 올리면 콜 수는 그대로인 채 recovery 가 붙는다(count 는 200 까지).
    lookback: int = 1
    # 지수 심볼 집합 — 지수는 전용 경로라(실측) 주식 경로로 부르면 404 다
    index_symbols: frozenset = frozenset()
    clock: object = field(default=lambda: datetime.now(timezone.utc), repr=False)
    _artifact_uri: str = field(default="pending://artifact", repr=False)

    def kind_of(self, unit_id: str) -> str:
        """토스 호출 경로 분기 — 지수 심볼이면 "index", 아니면 "stock"."""
        return "index" if unit_id in self.index_symbols else "stock"

    def collect(
        self, request: CollectionRequest, now: datetime
    ) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
        """collector 계약 — `(result, records, manifest)`. 판정·조립은 벤더 공통
        `collect_units` 에 위임하고, 이 벤더가 정하는 건 `_candle_for` 뿐이다."""
        return collect_units(
            request, now,
            candle_for=lambda unit_id: self._candle_for(unit_id, request),
            retry_count=lambda: self.client.retry_count,
            clock=self.clock,
            artifact_uri=self._artifact_uri,
        )

    def _candle_for(self, unit_id: str, request: CollectionRequest):
        """그 window 의 봉 하나, 또는 `Outcome.MISSING`/`Outcome.INVALID`.

        예외를 삼키지 않되 **한 종목의 실패가 window 전체를 죽이지 않게** 한다 —
        그 종목만 분류로 남기고 나머지는 계속 모은다(부분 실패는 결과 status 로
        드러나고, 재시도는 원장이 그 window 를 다시 claim 하는 것으로 이뤄진다).
        """
        try:
            # ⚠️ **요청 window 를 `before` 로 고정한다.** 기본값(최신)으로 부르면 348종
            # 수집에 약 70초가 걸리는 사이 최신 봉이 다음 분으로 넘어가, 뒤쪽 종목이
            # 통째로 missing 이 된다. 과거 window 재시도도 영영 복구되지 않는다.
            # `before` 는 inclusive 라(실측) window_end 를 그대로 주면 그 분이 온다.
            candles = self.client.candles(
                unit_id, interval="1m", count=self.lookback,
                before=request.window_end, kind=self.kind_of(unit_id),
            )
        except TossAuthError:
            # 토큰 단계 실패 = 그 계정의 모든 종목이 못 나간다. 전파한다.
            logger.error("토스 인증 실패 — 수집 중단", exc_info=True)
            raise
        except TossApiError as error:
            if error.source_level:
                # ⚠️ **전파한다.** 자격증명·IP 허용 목록 같은 소스 전역 실패를 unit
                # missing 으로 접으면 348종 전부가 missing 인 INCOMPLETE window 가
                # 매분 쌓이는데, 고칠 것은 설정 하나다. 재시도 대상처럼 보이면 아무도
                # 그걸 고치러 가지 않는다(Rule 12 — 드러내기).
                logger.error("토스 소스 전역 실패 — 수집 중단: %s", error)
                raise
            logger.error("토스 분봉 실패 %s: %s", unit_id, error)
            return Outcome.MISSING
        except ValueError:
            # 형상 위반은 **재시도로 안 풀린다** — missing 으로 접으면 같은 손상 응답을
            # 끝없이 다시 부른다. 판정 가능한 분류(invalid)로 남긴다.
            logger.exception("토스 분봉 형상 위반 %s", unit_id)
            return Outcome.INVALID
        return select_window_candle(candles, request.window_end, unit_id)
