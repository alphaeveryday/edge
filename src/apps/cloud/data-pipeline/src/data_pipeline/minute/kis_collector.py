"""KIS 분봉 collector — 1분 가격 레인의 기본 vendor 구현 (ALPHA-735).

토스 collector 와 **같은 계약**이고 4분류 판정도 같은 코드(`price_collect`)를 쓴다.
갈리는 건 두 가지뿐이다:

1. 콜 형태 — KIS 는 `window_end` 로 끝나는 30분치를 준다(토스는 count 만큼 거슬러).
   둘 다 그중 `window_end` 가 일치하는 봉 하나만 채택한다.
2. 실패 축 — 토스는 오류 코드로 소스 전역/unit 을 가르지만, KIS 는 어댑터가 예외 타입으로
   이미 갈라서 올린다(`KisSourceError` / `KisUnitError`).

⚠️ 토스와 달리 `lookback` 이 없다 — KIS 는 한 콜이 늘 30분치라 recovery 여유가 콜 수와
무관하게 붙는다(창을 넓히는 노브가 없어도 과거 window 재청구가 그대로 된다).

⚠️ 이 collector 는 클라이언트를 **둘** 받는다(ALPHA-846). 지난 거래일이면
`KisHistoricalMinuteClient` 가 오는데 그쪽은 30분치가 아니라 **하루를 한 번에** 받아
캐시하고 window 별로 나눠 준다 — 첫 window 에 콜이 몰리고(362종 × 4페이지) 그 뒤는
벤더 호출이 0이다. 계약(그 window 의 봉 0~1개)은 같아서 이 파일은 안 갈린다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..sources.kis_minute import KisMinuteClient, KisSourceError, KisUnitError
from .models import CollectionRequest, CollectionResult
from .price_collect import Outcome, collect_units, select_window_candle

logger = logging.getLogger(__name__)


@dataclass
class KisPriceCollector:
    """`unit_id` = KRX 단축코드(6자리) 전제 — KIS `FID_INPUT_ISCD` 가 같은 값이다."""

    client: KisMinuteClient
    clock: object = field(default=lambda: datetime.now(timezone.utc), repr=False)
    _artifact_uri: str = field(default="pending://artifact", repr=False)

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

        한 종목의 실패가 window 전체를 죽이지 않게 하되, **소스 전역 실패는 전파한다** —
        자격증명 하나가 틀렸을 때 400종 missing 인 INCOMPLETE 가 매분 쌓이면 아무도
        그 하나를 고치러 가지 않는다(Rule 12).
        """
        try:
            candles = self.client.candles(unit_id, window_end=request.window_end)
        except KisSourceError:
            logger.error("KIS 소스 전역 실패 — 수집 중단", exc_info=True)
            raise
        except KisUnitError as error:
            logger.error("KIS 분봉 실패 %s: %s", unit_id, error)
            return Outcome.MISSING
        except ValueError:
            # 형상 위반은 **재시도로 안 풀린다** — missing 으로 접으면 같은 손상 응답을
            # 끝없이 다시 부른다. 판정 가능한 분류(invalid)로 남긴다.
            logger.exception("KIS 분봉 형상 위반 %s", unit_id)
            return Outcome.INVALID
        return select_window_candle(candles, request.window_end, unit_id)
