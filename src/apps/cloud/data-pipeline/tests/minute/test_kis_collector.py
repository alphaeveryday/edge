"""KIS 분봉 collector 4분류 (ALPHA-735).

토스 collector 와 판정 코드를 공유하므로(`price_collect`) 여기서 고정하는 건 **KIS 축의
매핑**이다: 어느 예외가 어느 분류로 떨어지고, 30분치 응답에서 어느 봉이 채택되는가.

⚠️ 무거래 분(`cntg_vol=0` + OHLC flat)은 **성공**이다. 실패로 세면 한산한 종목이 매분
재시도를 유발해 window 가 영원히 INCOMPLETE 로 남는다.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from data_pipeline.minute.kis_collector import KisPriceCollector
from data_pipeline.minute.models import CollectionRequest
from data_pipeline.minute.states import (
    WINDOW_INCOMPLETE,
    WINDOW_INVALID,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)
from data_pipeline.sources.candle import Candle
from data_pipeline.sources.kis_minute import KisSourceError, KisUnitError

KST = timezone(timedelta(hours=9))
WINDOW_START = datetime(2026, 8, 3, 10, 29, tzinfo=KST)
WINDOW_END = datetime(2026, 8, 3, 10, 30, tzinfo=KST)


def candle(symbol: str, *, volume: str = "100", close: str = "100",
           end: datetime = WINDOW_END) -> Candle:
    return Candle(
        symbol=symbol, window_start=end - timedelta(minutes=1), window_end=end,
        open=Decimal("100"), high=Decimal(close if close > "100" else "100"),
        low=Decimal("100"), close=Decimal(close),
        volume=Decimal(volume), currency=None,
    )


def flat(symbol: str, end: datetime = WINDOW_END) -> Candle:
    """무거래 분 — 거래량 0 에 OHLC 가 직전가로 flat."""
    return Candle(
        symbol=symbol, window_start=end - timedelta(minutes=1), window_end=end,
        open=Decimal("9000"), high=Decimal("9000"), low=Decimal("9000"),
        close=Decimal("9000"), volume=Decimal("0"), currency=None,
    )


class FakeKisClient:
    """unit_id → 응답(캔들 tuple 또는 raise 할 예외)."""

    def __init__(self, by_unit):
        self.by_unit = by_unit
        self.retry_count = 0
        self.asked: list[datetime] = []

    def candles(self, symbol, *, window_end):
        self.asked.append(window_end)
        outcome = self.by_unit[symbol]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def collect(by_unit, units=None):
    collector = KisPriceCollector(
        client=FakeKisClient(by_unit), clock=lambda: WINDOW_END,
    )
    request = CollectionRequest(
        dataset="price_minute", window_start=WINDOW_START, window_end=WINDOW_END,
        run_id="run-1", session_id="msn_x", execution_mode="resident",
        universe_version="u1", unit_ids=tuple(units or by_unit),
    )
    return collector.collect(request, WINDOW_END)


def test_traded_candle_is_received():
    result, records, manifest = collect({"005930": (candle("005930"),)})
    assert manifest["received"] == ["005930"]
    assert result.status == WINDOW_VALID
    assert records[0]["unit_id"] == "005930"
    # 기록의 ts 는 window_start 다(봉 라벨이 아니라) — 축이 밀리면 여기서 잡힌다
    assert records[0]["ts"] == WINDOW_START


def test_zero_volume_flat_is_no_trade_but_recorded():
    # ⚠️ 기록은 남긴다 — manifest 분류와 artifact 는 다른 축이다. 버리면 한산한 종목이
    # canonical 에서 통째로 사라진다
    result, records, manifest = collect({"439870": (flat("439870"),)})
    assert manifest["no_trade"] == ["439870"]
    assert result.status == WINDOW_VALID_EMPTY
    assert len(records) == 1


def test_zero_volume_non_flat_is_invalid():
    # 거래량 0 인데 가격이 움직였다 = 아는 형상이 아니다. no_trade 로 접으면 그 가격이
    # 조용히 버려진다
    weird = candle("005930", volume="0", close="101")
    result, _, manifest = collect({"005930": (weird,)})
    assert manifest["invalid"] == ["005930"]
    assert result.status == WINDOW_INVALID


def test_absent_row_is_missing():
    # 응답은 왔는데 그 분의 봉이 없다 → 재시도로 풀릴 수 있는 축
    other = candle("005930", end=WINDOW_END - timedelta(minutes=1))
    result, _, manifest = collect({"005930": (other,)})
    assert manifest["missing"] == ["005930"]
    assert result.status == WINDOW_INCOMPLETE


def test_unit_error_is_missing_and_others_continue():
    result, _, manifest = collect({
        "005930": KisUnitError("종목 오류"),
        "000660": (candle("000660"),),
    })
    assert (manifest["missing"], manifest["received"]) == (["005930"], ["000660"])
    assert (result.succeeded_count, result.failed_count) == (1, 1)


def test_shape_violation_is_invalid():
    result, _, manifest = collect({"005930": ValueError("형상 위반")})
    assert manifest["invalid"] == ["005930"]


def test_source_level_failure_propagates():
    # 자격증명 하나가 틀렸는데 400종 missing 인 INCOMPLETE 가 매분 쌓이면 아무도
    # 그 하나를 고치러 가지 않는다 — 전파해서 window 를 세운다
    with pytest.raises(KisSourceError):
        collect({"005930": KisSourceError("권한 없음"), "000660": (candle("000660"),)})


def test_duplicate_window_candle_is_invalid():
    # 같은 분이 두 번 오면 어느 쪽이 참인지 우리가 못 고른다 — 첫 건 채택은 세대가 흔들린다
    _, _, manifest = collect({"005930": (candle("005930"), candle("005930", close="200"))})
    assert manifest["invalid"] == ["005930"]


def test_requests_the_requested_window_not_latest():
    # 400종을 도는 사이 '최신'이 넘어가면 뒤쪽 종목이 통째로 missing 이 된다
    collector = KisPriceCollector(client=FakeKisClient({"005930": (candle("005930"),)}))
    request = CollectionRequest(
        dataset="price_minute", window_start=WINDOW_START, window_end=WINDOW_END,
        run_id="r", session_id="s", execution_mode="resident",
        universe_version="u1", unit_ids=("005930",),
    )
    collector.collect(request, WINDOW_END)
    assert collector.client.asked == [WINDOW_END]


def test_result_and_manifest_agree():
    # Worker 가 이 둘을 대조한다 — 어긋나면 '성공 위장'이 커밋된다
    result, _, manifest = collect({
        "005930": (candle("005930"),), "439870": (flat("439870"),),
        "000660": KisUnitError("x"), "035420": ValueError("y"),
    })
    assert result.succeeded_count == len(manifest["received"]) + len(manifest["no_trade"]) == 2
    assert result.failed_count == len(manifest["missing"]) + len(manifest["invalid"]) == 2
    assert result.expected_count == 4
