"""업종지수 1분봉 window collector — KIS 단독 (ALPHA-887).

형식 선례는 `inav_collect.py` 다(`price_collect.py` 가 **아니다**). 벤더가 하나뿐이라
어댑터를 따로 떼지 않고 이 파일이 순회와 KIS 결합을 함께 진다.

가격과 갈리는 곳은 셋이다:

1. 🔴 **`no_trade` 축이 없다.** `price_collect` 는 `volume == 0` 인데 OHLC 가 flat 이
   아니면 **invalid** 로 접는데(그 파일 표의 마지막 줄), 지수에서는 그게 정상이다 —
   지수는 자기가 체결되지 않고 구성종목이 체결된다. 실측 45종 × 100봉 = 4,500봉 중
   **175봉(3.9%)**이 거래량 0 인데 OHLC 가 움직인다(저유동 업종 1007 은 상시).
   `status_of` 가 invalid 하나로 window 전체를 INVALID 로 만들므로, 4분류를 그대로
   물리면 이 dataset 은 **단 한 window 도 확정되지 않는다**. iNAV 처럼 그 칸을 늘
   비워 둔다 — 어휘(4키)는 지켜서 `build_window_manifest` 의 완전분할 검증과 갈리지 않는다.
   ⚠️ 그래서 `Candle.traded`(volume > 0)를 이 dataset 에서 "거래 있었나"로 읽지 마라.
   레코드에는 싣되(관측한 값이다) 유동성으로 해석할 소비자를 붙이면 안 된다.
2. **기대 집합이 universe 가 아니라 config 다.** 지수 45종은 ETF 명부에도 구성종목에도
   없어 universe.json 이 모른다 — 무엇을 기대할지는 호출자(Worker)가
   `request.unit_ids` 로 정하고, 그 출처는 `[minute_sector_index.index_map]` 이다.
3. **1콜이 그 거래일 전체(최근 100봉)다.** 한 window 를 채우는 데 필요한 건 그중 한
   행이고 나머지는 버린다 — 어댑터의 `candles()` 가 창을 못 고르기 때문이다(그 함수
   도크스트링). 겹침 복구는 `_process(claim)` = window 1 계약 밖이다.

⭐ **라벨 축이 주식과 반대다** — 업종지수 `stck_cntg_hour` 는 구간의 **시작**이고 주식
당일 TR 은 **끝**이다(어댑터가 실측으로 확정). 그 변환은 `parse_index_row` 안에서 끝나
여기 도착한 `Candle` 은 이미 `[window_start, window_end)` 축이다 — 그래서 가격과 같은
`select_window_candle` 을 그대로 쓴다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ..sources.candle import Candle
from ..sources.kis_minute import KisSourceError, KisUnitError
from .models import CollectionRequest, CollectionResult, content_checksum
from .price_collect import Outcome, record_of, select_window_candle, status_of

logger = logging.getLogger(__name__)


def collect_sector_index_units(
    request: CollectionRequest,
    now: datetime,
    *,
    candles_for: Callable[[str], tuple[Candle, ...] | str],
    retry_count: Callable[[], int],
    clock: Callable[[], datetime],
    artifact_uri: str,
) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
    """unit 전체를 순회해 `(result, records, manifest)` 를 낸다 — collector 계약 그대로."""
    started = now
    received: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    records: list[dict] = []
    retries_before = retry_count()

    # unit_id 정렬 순회 — 같은 멤버십을 다른 순서로 요청해도 records·checksum 이 같다
    for unit_id in sorted(request.unit_ids):
        outcome = candles_for(unit_id)
        if outcome is Outcome.MISSING:
            missing.append(unit_id)
            continue
        if outcome is Outcome.INVALID:
            invalid.append(unit_id)
            continue
        candle = select_window_candle(outcome, request.window_end, unit_id)
        if candle is Outcome.MISSING:
            missing.append(unit_id)
            continue
        if candle is Outcome.INVALID:
            invalid.append(unit_id)
            continue
        # 🔴 여기서 `candle.traded` 를 보지 않는다 — 모듈 도크스트링 1번. 거래량 0 이어도
        # 그 분의 지수 값은 관측된 것이고, 그것이 이 dataset 이 싣는 값이다.
        records.append(record_of(candle))
        received.append(unit_id)

    # `no_trade` 는 이 dataset 에 없는 축이다 — 빈 칸으로 남겨 4분류 어휘를 지킨다.
    manifest = {"received": received, "no_trade": [],
                "missing": missing, "invalid": invalid}
    # checksum 은 데이터에서만 유도한다 — 실행 시각·세대가 들어가면 값이 같은 재실행이
    # 다른 checksum 이 돼 "같은 checksum → generation 불변"이 깨진다
    result_checksum = content_checksum(
        [request.dataset, request.window_start, request.window_end, records]
    )
    result = CollectionResult(
        status=status_of(received, [], missing, invalid),
        expected_count=len(request.unit_ids),
        succeeded_count=len(received),
        failed_count=len(missing) + len(invalid),
        retry_count=retry_count() - retries_before,
        artifact_uri=artifact_uri,
        manifest_checksum=content_checksum(manifest),
        result_checksum=result_checksum,
        watermark_before=None,
        watermark_after=request.window_end,
        generation=1,
        stage_timestamps={"collection_started_at": started,
                          "collection_finished_at": clock()},
    )
    return result, tuple(records), manifest


class KisSectorIndexCollector:
    """어댑터를 window 계약에 물린다 — 새 HTTP 코드는 없다.

    토큰 발급·`EGW00201` 재시도·`rt_cd` 판정·센티넬 격리·**KRX↔KIS 코드 번역**이 전부
    `KisSectorIndexClient` 에 있어서, 여기서는 **어느 업종을 언제 부르는가**만 정한다.
    🔴 코드 번역을 여기서 하려 들지 마라 — `candles(unit_id)` 도 `Candle.symbol` 도 KRX
    업종코드다. 여기서 번역하면 남의 지수를 받거나 canonical `unit_id` 가 벤더 코드가 돼
    일봉 `sector_index` 와 조인이 안 된다(어댑터 도크스트링 6번).
    """

    def __init__(self, client, *, clock: Callable[[], datetime]):
        self.client = client
        self.clock = clock

    def collect(
        self, request: CollectionRequest, now: datetime
    ) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
        return collect_sector_index_units(
            request, now,
            candles_for=lambda unit_id: self._candles_for(unit_id, request.window_end),
            retry_count=lambda: self.client.retry_count,
            clock=self.clock,
            artifact_uri="pending://artifact",
        )

    def _candles_for(self, unit_id: str, window_end: datetime) -> tuple[Candle, ...] | str:
        """그 unit 의 봉들, 또는 `Outcome.MISSING`/`Outcome.INVALID`.

        축은 `kis_collector._candle_for` 와 **같다** — 어댑터가 `KisMinuteClient` 하위라
        같은 예외 계약을 쓴다. 한 업종의 실패가 window 전체를 죽이지 않게 하되 **소스
        전역 실패는 전파한다**(자격증명 하나가 틀렸을 때 45종 missing 인 INCOMPLETE 가
        매분 쌓이면 아무도 그 하나를 고치러 가지 않는다 — Rule 12).
        """
        try:
            return self.client.candles(unit_id, window_end=window_end)
        except KisSourceError:
            logger.error("KIS 업종지수 소스 전역 실패 — 수집 중단", exc_info=True)
            raise
        except KisUnitError as error:
            # 봉투 이상·전송 사고는 missing 이다. ⚠️ **근거는 "다음에 다시 받는다"가
            # 아니다** — 이 dataset 은 `recovery_budget_per_tick=0` 이고 `claim_due_window`
            # 는 DUE·만료 CLAIMED 만 집으므로, 한 번 INCOMPLETE 로 확정된 window 는 다시
            # 안 온다(어댑터·iNAV 가 이 축을 "재시도 축"이라 부르지만 여기선 그 말이
            # 성립하지 않는다 — 리뷰 라운드 2).
            # 근거는 **블라스트 반경**이다: INVALID 로 올리면 `status_of` 가 window 를
            # 통째로 INVALID 로 만들어 나머지 44종의 정상 값까지 그 판정 아래 묻힌다.
            # missing 이면 그 44종은 received 로 남고 결손이 그 unit 하나로 국한된다.
            logger.error("KIS 업종지수 분봉 실패 %s: %s", unit_id, error)
            return Outcome.MISSING
        except ValueError:
            # 행 형상 위반은 **재시도로 안 풀린다** — missing 으로 접으면 같은 손상 응답을
            # 끝없이 다시 부른다. 판정 가능한 분류(invalid)로 남긴다.
            logger.exception("KIS 업종지수 분봉 형상 위반 %s", unit_id)
            return Outcome.INVALID
