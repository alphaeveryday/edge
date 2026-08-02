"""토스 분봉 collector — Price Worker 에 끼우는 실 vendor 구현 (계획 §9).

`FakePriceCollector` 와 **같은 계약**(`collect(request, now) -> (result, records, manifest)`)
이라 Worker 는 무엇이 끼워졌는지 모른다(PR 4 가 collector 주입식으로 만든 이유).

unit 4분류가 이 파일의 핵심 판단이다 — 실측 근거는 `.dev/toss-openapi-실측.md`:

| 응답 | 분류 | 근거 |
|---|---|---|
| 그 분의 캔들이 있고 `volume > 0` | **received** | 정상 체결 |
| 그 분의 캔들이 있고 `volume == 0` | **no_trade** (기록은 남긴다) | 거래 없어도 캔들은 온다(직전가 flat) |
| 그 분의 캔들이 **없다**·404 | **missing** | 재시도로 풀릴 수 있다 |
| 인증·IP 차단 등 **소스 전역** 실패 | **전파(중단)** | unit 실패로 접으면 고칠 설정 하나가 안 보인다 |
| 형상 위반·같은 분 중복·volume 0 인데 OHLC 가 안 flat | **invalid** | 재시도로 **안** 풀린다 |

⚠️ 토스 timestamp 는 **구간의 끝**이라 `캔들 ts == window_end` 로 고른다(sources/toss.py).
⚠️⚠️ **처리량이 window 주기를 못 따라간다.** 종목당 1콜 × 348종 ÷ 초당 5회 = **약 70초**인데
   window 는 **60초마다** 새로 생긴다. 동시성을 올려도 한도가 같아 줄지 않는다 — backlog 가
   구조적으로 쌓인다는 뜻이라, 운영 투입 전에 콜 수 자체를 줄이는 결정이 필요하다
   (한 콜에 여러 window 를 받거나 유니버스를 줄이거나 한도를 올리거나). 이 어댑터는
   그 결정 전까지 **shadow·백필 용도**로만 쓴다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..sources.toss import Candle, TossApiError, TossAuthError, TossOpenApiClient
from .models import CollectionRequest, CollectionResult, content_checksum
from .states import (
    WINDOW_INCOMPLETE,
    WINDOW_INVALID,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)

logger = logging.getLogger(__name__)


class _Outcome:
    """캔들이 아닌 결과 두 가지 — None 하나로 접으면 재시도로 풀리는 것(missing)과
    안 풀리는 것(invalid)이 같은 상태가 된다."""

    MISSING = "missing"
    INVALID = "invalid"


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
        return "index" if unit_id in self.index_symbols else "stock"

    def collect(
        self, request: CollectionRequest, now: datetime
    ) -> tuple[CollectionResult, tuple[dict, ...], dict[str, list[str]]]:
        started = now
        received: list[str] = []
        no_trade: list[str] = []
        missing: list[str] = []
        invalid: list[str] = []
        records: list[dict] = []
        retries_before = self.client.retry_count

        # unit_id 정렬 순회 — 같은 멤버십을 다른 순서로 요청해도 records·checksum 이 같다
        # (FakePriceCollector 와 같은 축: 순서 무관 identity)
        for unit_id in sorted(request.unit_ids):
            outcome = self._candle_for(unit_id, request)
            if outcome is _Outcome.MISSING:
                missing.append(unit_id)
            elif outcome is _Outcome.INVALID:
                invalid.append(unit_id)
            elif outcome.traded:
                received.append(unit_id)
                records.append(_record(outcome))
            elif outcome.open == outcome.high == outcome.low == outcome.close:
                # 거래 없는 분 — **성공**이다(직전가 flat). 실패로 세면 한산한 종목이
                # 매분 재시도를 유발하고 window 가 영원히 INCOMPLETE 로 남는다.
                no_trade.append(unit_id)
                # ⚠️ **기록은 남긴다.** manifest 분류(무슨 일이 있었나)와 artifact(무엇을
                # 관측했나)는 다른 축이다. 벤더가 준 flat 봉을 버리면 한산한 종목은
                # 하루 390분 중 376분이 canonical 에서 사라진다(001527 실측).
                records.append(_record(outcome))
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
            status=_status(received, no_trade, missing, invalid),
            expected_count=len(request.unit_ids),
            succeeded_count=len(received) + len(no_trade),
            failed_count=len(missing) + len(invalid),
            # 실제 재시도 수를 싣는다 — 0 으로 고정하면 429 압력이 관측에서 사라진다
            retry_count=self.client.retry_count - retries_before,
            artifact_uri=self._artifact_uri,
            manifest_checksum=content_checksum(manifest),
            result_checksum=result_checksum,
            watermark_before=None,
            watermark_after=request.window_end,
            generation=1,
            # 시작·종료를 같은 값으로 두면 70초짜리 수집이 0초로 보인다(SLA 검증 불가)
            stage_timestamps={"collection_started_at": started,
                              "collection_finished_at": self.clock()},
        )
        return result, tuple(records), manifest

    def _candle_for(self, unit_id: str, request: CollectionRequest):
        """그 window 의 캔들 하나, 또는 `_Outcome.MISSING`/`_Outcome.INVALID`.

        예외를 삼키지 않되 **한 종목의 실패가 window 전체를 죽이지 않게** 한다 —
        그 종목만 분류로 남기고 나머지는 계속 모은다(부분 실패는 결과 status 로
        드러나고, 재시도는 원장이 그 window 를 다시 claim 하는 것으로 이뤄진다).
        """
        try:
            # ⚠️ **요청 window 를 `before` 로 고정한다.** 기본값(최신)으로 부르면 348종
            # 수집에 약 70초가 걸리는 사이 최신 캔들이 다음 분으로 넘어가, 뒤쪽 종목이
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
            return _Outcome.MISSING
        except ValueError:
            # 형상 위반은 **재시도로 안 풀린다** — missing 으로 접으면 같은 손상 응답을
            # 끝없이 다시 부른다. 판정 가능한 분류(invalid)로 남긴다.
            logger.exception("토스 분봉 형상 위반 %s", unit_id)
            return _Outcome.INVALID
        matched = [c for c in candles if c.window_end == request.window_end]
        if not matched:
            return _Outcome.MISSING
        if len(matched) > 1:
            # 같은 분이 두 번 오면 어느 쪽이 참인지 우리가 고를 수 없다 — 첫 건을
            # 조용히 채택하면 벤더가 순서를 바꾸는 것만으로 값과 세대가 흔들린다.
            logger.error("%s 가 window %s 에 캔들 %d건을 줬다 — 유일성 위반",
                         unit_id, request.window_end.isoformat(), len(matched))
            return _Outcome.INVALID
        return matched[0]


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


def _status(received: list, no_trade: list, missing: list, invalid: list) -> str:
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
