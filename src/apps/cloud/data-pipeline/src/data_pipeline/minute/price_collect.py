"""분봉 collector 의 **벤더 무관부** — 4분류 순회·결과 조립 (ALPHA-735).

토스 collector 에서 떼어냈다. 벤더가 둘이 되면서 이 판정이 두 벌이 되면 한쪽만 고쳐지는데,
여기서 틀리면 방향이 늘 같다 — **원장이 관대해지는 쪽**(실패가 성공으로 보인다). 벤더가 갖는
건 "그 window 의 봉 하나를 어떻게 얻는가"(`candle_for`)뿐이다.

unit 4분류(실측 근거는 `.dev/toss-openapi-실측.md`·ALPHA-644 KIS 프로브):

| 응답 | 분류 | 근거 |
|---|---|---|
| 그 분의 봉이 있고 `volume > 0` | **received** | 정상 체결 |
| 그 분의 봉이 있고 `volume == 0` | **no_trade** (기록은 남긴다) | 거래 없어도 봉은 온다(직전가 flat) — 소급 TR 만 예외라 어댑터가 합성한다(ALPHA-846) |
| 그 분의 봉이 **없다** | **missing** | 재시도로 풀릴 수 있다 |
| 인증·IP 차단 등 **소스 전역** 실패 | **전파(중단)** | unit 실패로 접으면 고칠 설정 하나가 안 보인다 |
| 형상 위반·같은 분 중복·volume 0 인데 OHLC 가 안 flat | **invalid** | 재시도로 **안** 풀린다 |
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from ..sources.candle import Candle
from .models import CollectionRequest, CollectionResult, content_checksum
from .states import (
    WINDOW_INCOMPLETE,
    WINDOW_INVALID,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)

logger = logging.getLogger(__name__)


class Outcome:
    """봉이 아닌 결과 두 가지 — None 하나로 접으면 재시도로 풀리는 것(missing)과
    안 풀리는 것(invalid)이 같은 상태가 된다."""

    MISSING = "missing"
    INVALID = "invalid"


def select_window_candle(candles, window_end: datetime, unit_id: str):
    """그 window 의 봉 하나, 또는 `Outcome.MISSING`/`Outcome.INVALID`.

    두 벤더 다 한 콜에 여러 분을 준다(토스 count·KIS 30분치) — 그중 이 window 것만 고른다.
    """
    matched = [c for c in candles if c.window_end == window_end]
    if not matched:
        return Outcome.MISSING
    if len(matched) > 1:
        # 같은 분이 두 번 오면 어느 쪽이 참인지 우리가 고를 수 없다 — 첫 건을 조용히
        # 채택하면 벤더가 순서를 바꾸는 것만으로 값과 세대가 흔들린다.
        logger.error("%s 가 window %s 에 봉 %d건을 줬다 — 유일성 위반",
                     unit_id, window_end.isoformat(), len(matched))
        return Outcome.INVALID
    return matched[0]


def collect_units(
    request: CollectionRequest,
    now: datetime,
    *,
    candle_for: Callable[[str], Candle | str],
    retry_count: Callable[[], int],
    clock: Callable[[], datetime],
    artifact_uri: str,
) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
    """unit 전체를 순회해 `(result, records, manifest)` 를 낸다 — collector 계약 그대로."""
    started = now
    received: list[str] = []
    no_trade: list[str] = []
    missing: list[str] = []
    invalid: list[str] = []
    records: list[dict] = []
    retries_before = retry_count()

    # unit_id 정렬 순회 — 같은 멤버십을 다른 순서로 요청해도 records·checksum 이 같다
    # (FakePriceCollector 와 같은 축: 순서 무관 identity)
    for unit_id in sorted(request.unit_ids):
        outcome = candle_for(unit_id)
        if outcome is Outcome.MISSING:
            missing.append(unit_id)
        elif outcome is Outcome.INVALID:
            invalid.append(unit_id)
        elif outcome.traded:
            received.append(unit_id)
            records.append(record_of(outcome))
        elif outcome.open == outcome.high == outcome.low == outcome.close:
            # 거래 없는 분 — **성공**이다(직전가 flat). 실패로 세면 한산한 종목이
            # 매분 재시도를 유발하고 window 가 영원히 INCOMPLETE 로 남는다.
            no_trade.append(unit_id)
            # ⚠️ **기록은 남긴다.** manifest 분류(무슨 일이 있었나)와 artifact(무엇을
            # 관측했나)는 다른 축이다. 벤더가 준 flat 봉을 버리면 한산한 종목은
            # 하루 390분 중 376분이 canonical 에서 사라진다(001527 실측).
            records.append(record_of(outcome))
        else:
            # 거래량 0 인데 가격이 움직였다 = 우리가 아는 형상이 아니다. no_trade 로
            # 접으면 그 가격 데이터가 조용히 버려진다.
            logger.error("%s: volume 0 인데 OHLC 가 flat 이 아니다 — invalid", unit_id)
            invalid.append(unit_id)

    manifest = {"received": received, "no_trade": no_trade,
                "missing": missing, "invalid": invalid}
    # checksum 은 데이터에서만 유도한다 — 실행 시각·세대가 들어가면 값이 같은
    # 재실행이 다른 checksum 이 돼 "같은 checksum → generation 불변"이 깨진다
    result_checksum = content_checksum(
        [request.dataset, request.window_start, request.window_end, records]
    )
    result = CollectionResult(
        status=status_of(received, no_trade, missing, invalid),
        expected_count=len(request.unit_ids),
        succeeded_count=len(received) + len(no_trade),
        failed_count=len(missing) + len(invalid),
        # 실제 재시도 수를 싣는다 — 0 으로 고정하면 유량 압력이 관측에서 사라진다
        retry_count=retry_count() - retries_before,
        artifact_uri=artifact_uri,
        manifest_checksum=content_checksum(manifest),
        result_checksum=result_checksum,
        watermark_before=None,
        watermark_after=request.window_end,
        generation=1,
        # 시작·종료를 같은 값으로 두면 70초짜리 수집이 0초로 보인다(SLA 검증 불가)
        stage_timestamps={"collection_started_at": started,
                          "collection_finished_at": clock()},
    )
    return result, tuple(records), manifest


def record_of(candle: Candle) -> dict:
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


def status_of(received: list, no_trade: list, missing: list, invalid: list) -> str:
    """수집 결과 어휘 판정.

    ⚠️ 전 종목이 no_trade 면 VALID_EMPTY 다 — VALID 로 접으면 '데이터가 있다'와
    '없는 게 정상이다'가 같은 상태가 돼 EOD QC 가 둘을 못 가른다.
    ⚠️ invalid 는 재시도로 안 풀리는 축이라 INCOMPLETE(재시도 대상)와 섞지 않는다.
    """
    if invalid:
        return WINDOW_INVALID
    if missing:
        return WINDOW_INCOMPLETE
    return WINDOW_VALID if received else WINDOW_VALID_EMPTY
