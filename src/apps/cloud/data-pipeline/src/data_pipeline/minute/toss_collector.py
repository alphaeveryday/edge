"""토스 분봉 collector — Price Worker 에 끼우는 실 vendor 구현 (계획 §9).

`FakePriceCollector` 와 **같은 계약**(`collect(request, now) -> (result, records, manifest)`)
이라 Worker 는 무엇이 끼워졌는지 모른다(PR 4 가 collector 주입식으로 만든 이유).

unit 4분류가 이 파일의 핵심 판단이다 — 실측 근거는 `.dev/toss-openapi-실측.md`:

| 응답 | 분류 | 근거 |
|---|---|---|
| 그 분의 캔들이 있고 `volume > 0` | **received** | 정상 체결 |
| 그 분의 캔들이 있고 `volume == 0` | **no_trade** | 거래 없어도 캔들은 온다(직전가 flat) |
| 그 분의 캔들이 **없다** | **missing** | 재시도 대상 |
| 종목이 없다(404)·형상 위반 | **missing** + 크게 기록 | 그 window 를 성공으로 접지 않는다 |

⚠️ 토스 timestamp 는 **구간의 끝**이라 `캔들 ts == window_end` 로 고른다(sources/toss.py).
⚠️ 한 window 를 채우는 데 종목당 1콜이고 한도가 **초당 5회**라, 348종이면 약 70초다.
   동시성을 올려도 한도가 같아 줄지 않는다 — 줄이려면 콜 수를 줄여야 한다(그건 별건).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..sources.toss import Candle, TossApiError, TossOpenApiClient
from .models import CollectionRequest, CollectionResult, content_checksum
from .states import WINDOW_INCOMPLETE, WINDOW_VALID, WINDOW_VALID_EMPTY

logger = logging.getLogger(__name__)


@dataclass
class TossPriceCollector:
    """`unit_id` = 토스 symbol(6자리 단축코드) 전제 — 매핑은 유니버스 소관이다."""

    client: TossOpenApiClient
    # 한 window 를 놓쳤을 때 같은 콜로 몇 분까지 거슬러 받을지. 1 이면 딱 그 분만
    # 본다. 값을 올리면 콜 수는 그대로인 채 recovery 가 붙는다(count 는 200 까지).
    lookback: int = 1
    _artifact_uri: str = field(default="pending://artifact", repr=False)

    def collect(
        self, request: CollectionRequest, now: datetime
    ) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
        started = now
        received: list[str] = []
        no_trade: list[str] = []
        missing: list[str] = []
        records: list[dict] = []

        # unit_id 정렬 순회 — 같은 멤버십을 다른 순서로 요청해도 records·checksum 이 같다
        # (FakePriceCollector 와 같은 축: 순서 무관 identity)
        for unit_id in sorted(request.unit_ids):
            candle = self._candle_for(unit_id, request)
            if candle is None:
                missing.append(unit_id)
            elif candle.traded:
                received.append(unit_id)
                records.append(_record(candle))
            else:
                # 거래 없는 분 — **성공**이다. 실패로 세면 한산한 종목이 매분 재시도를
                # 유발하고 window 가 영원히 INCOMPLETE 로 남는다.
                no_trade.append(unit_id)

        manifest = {"received": received, "no_trade": no_trade, "missing": missing}
        # checksum 은 데이터에서만 유도한다 — 실행 시각·세대가 들어가면 값이 같은
        # 재실행이 다른 checksum 이 돼 "같은 checksum → generation 불변"이 깨진다
        result_checksum = content_checksum(
            [request.dataset, request.window_start, request.window_end, records]
        )
        result = CollectionResult(
            status=_status(received, no_trade, missing),
            expected_count=len(request.unit_ids),
            succeeded_count=len(received) + len(no_trade),
            failed_count=len(missing),
            retry_count=0,
            artifact_uri=self._artifact_uri,
            manifest_checksum=content_checksum(manifest),
            result_checksum=result_checksum,
            watermark_before=None,
            watermark_after=request.window_end,
            generation=1,
            stage_timestamps={"collection_started_at": started,
                              "collection_finished_at": now},
        )
        return result, tuple(records), manifest

    def _candle_for(self, unit_id: str, request: CollectionRequest) -> Candle | None:
        """그 window 의 캔들 하나. 없으면 None(=missing).

        예외를 삼키지 않되 **한 종목의 실패가 window 전체를 죽이지 않게** 한다 —
        그 종목만 missing 으로 남기고 나머지는 계속 모은다(부분 성공은 INCOMPLETE 로
        드러나고, 재시도는 원장이 그 window 를 다시 claim 하는 것으로 이뤄진다).
        """
        try:
            candles = self.client.candles(unit_id, interval="1m", count=self.lookback)
        except TossApiError as error:
            logger.error("토스 분봉 실패 %s: %s", unit_id, error)
            return None
        except ValueError:
            # 형상 위반 — 조용히 넘기면 그 자리가 '정상 수집'으로 굳는다
            logger.exception("토스 분봉 형상 위반 %s", unit_id)
            return None
        for candle in candles:
            # ts 는 구간의 끝이라 window_end 와 맞춘다(한 칸 밀림 방지)
            if candle.window_end == request.window_end:
                return candle
        return None


def _record(candle: Candle) -> dict:
    """artifact 에 실리는 bar 한 줄 — FakePriceCollector 와 같은 필드 축.

    Decimal 을 문자열로 낸다: float 로 접으면 정밀도가 깨지고, canonical_json 이
    NaN/Infinity 를 거부하는 규약과도 어긋난다.
    """
    return {
        "unit_id": candle.symbol,
        "ts": candle.window_start,
        "open": str(candle.open),
        "high": str(candle.high),
        "low": str(candle.low),
        "close": str(candle.close),
        "volume": str(candle.volume),
    }


def _status(received: list, no_trade: list, missing: list) -> str:
    """수집 결과 4어휘 중 셋을 쓴다(INVALID 는 형상 판정이라 상위 소관).

    ⚠️ 전 종목이 no_trade 면 VALID_EMPTY 다 — VALID 로 접으면 '데이터가 있다'와
    '없는 게 정상이다'가 같은 상태가 돼 EOD QC 가 둘을 못 가른다.
    """
    if missing:
        return WINDOW_INCOMPLETE
    return WINDOW_VALID if received else WINDOW_VALID_EMPTY
