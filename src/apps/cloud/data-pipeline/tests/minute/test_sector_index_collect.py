"""업종지수 window collector 테스트 (ALPHA-887).

의도: 이 모듈이 조용히 틀릴 수 있는 축은 **분류**다. 지수는 `cntg_vol == 0` 인데 OHLC 가
움직이는 봉이 정상인데(실측 3.9%), 가격의 4분류는 그걸 `invalid` 로 접고 `status_of` 가
window 전체를 INVALID 로 만든다. 그러면 이 dataset 은 **단 한 window 도 확정되지 않으면서**
원장에는 "벤더 데이터가 이상하다"로 보인다. 이 파일의 첫 테스트가 그 배선을 막는 표지다.

픽스처에 분해능을 준다 — 업종 2종 × 봉 3종. 한 종목·한 봉이면 "첫 봉을 집는" 구현과
"window 를 맞춰 집는" 구현이 같은 결과를 내 아무것도 보증하지 못한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from data_pipeline.minute.models import KST, CollectionRequest
from data_pipeline.minute.price_collect import Outcome
from data_pipeline.minute.sector_index_collect import (
    KisSectorIndexCollector,
    collect_sector_index_units,
)
from data_pipeline.sources.candle import build_candle
from data_pipeline.sources.kis_minute import KisSourceError, KisUnitError

NOW = datetime(2026, 8, 10, 10, 31, 5, tzinfo=KST)
WINDOW_START = datetime(2026, 8, 10, 10, 30, tzinfo=KST)
WINDOW_END = WINDOW_START + timedelta(minutes=1)


def bar(start: datetime, close: str, *, volume: str = "80", unit: str = "1005",
        flat: bool = False):
    """`parse_index_row` 가 내는 형태 그대로 — 라벨은 구간 **시작**이다.

    OHLC 는 close 중심으로 정합하게 만든다. `flat=True` 는 넷이 같은 값(주식이면
    "거래 없음"으로 읽히는 형상) — 이 dataset 에서 그게 무엇을 뜻하는지가 쟁점이라
    두 형상을 픽스처에서 갈라 둔다.
    """
    value = Decimal(close)
    values = ({"open": value, "high": value, "low": value, "close": value} if flat
              else {"open": value - 1, "high": value + 1, "low": value - 2,
                    "close": value})
    return build_candle(
        unit, window_end=start + timedelta(minutes=1), span_seconds=60,
        values=values, volume=Decimal(volume),
    )


# 업종 2종 × 봉 3종. 값이 전부 달라서 **어느 봉을 집었는지**가 결과에 남는다.
BARS = {
    "1005": tuple(bar(WINDOW_START + timedelta(minutes=n), close, unit="1005")
                  for n, close in ((-1, "100"), (0, "200"), (1, "300"))),
    "2118": tuple(bar(WINDOW_START + timedelta(minutes=n), close, unit="2118")
                  for n, close in ((-1, "400"), (0, "500"), (1, "600"))),
}


def request_for(unit_ids: tuple[str, ...]) -> CollectionRequest:
    return CollectionRequest(
        dataset="sector_index_minute", window_start=WINDOW_START, window_end=WINDOW_END,
        run_id="run-1", session_id="msn-1", execution_mode="resident",
        universe_version="none", unit_ids=unit_ids, failure_injection=None,
    )


def collect(candles_for, unit_ids=("1005", "2118")):
    return collect_sector_index_units(
        request_for(unit_ids), NOW, candles_for=candles_for,
        retry_count=lambda: 0, clock=lambda: NOW, artifact_uri="pending://artifact",
    )


class TestClassification:
    def test_zero_volume_with_moving_price_is_received_not_invalid(self):
        """🔴 이 dataset 이 `price_collect` 를 못 쓰는 **이유**다.

        지수는 자기가 체결되지 않고 구성종목이 체결된다 — 거래량 0 인데 OHLC 가 움직이는
        봉이 실측 4,500 중 175(3.9%)고 저유동 업종(1007)은 상시다. `price_collect` 는
        같은 형상을 `invalid` 로 접고(그 파일 표의 마지막 줄) `status_of` 가 window 를
        통째로 INVALID 로 만든다 — 그러면 매 window 가 죽는다.
        """
        moving_flatless = bar(WINDOW_START, "215.30", volume="0", unit="1007")
        result, records, manifest = collect(
            lambda unit_id: (moving_flatless,), unit_ids=("1007",))

        assert manifest["received"] == ["1007"]
        assert manifest["invalid"] == []
        assert result.status == "VALID"
        assert records[0]["volume"] == "0"   # 관측한 값은 그대로 싣는다

    def test_no_trade_slot_stays_empty_but_present(self):
        """어휘(4키)는 지키고 그 칸만 늘 빈다 — 빼면 `build_window_manifest` 의
        완전분할 검증과 갈리고, EOD QC 가 dataset 마다 다른 어휘를 만나게 된다."""
        _, _, manifest = collect(lambda unit_id: BARS[unit_id])

        assert set(manifest) == {"received", "no_trade", "missing", "invalid"}
        assert manifest["no_trade"] == []

    def test_succeeded_count_counts_only_received(self):
        """`no_trade` 가 없으므로 성공 수 = received 수다. `price_collect` 처럼
        `len(received) + len(no_trade)` 로 두면 안 되는 게 아니라(빈 칸이라 같다),
        **의도를 값으로 남긴다** — 나중에 그 칸을 채우는 사람이 여기서 걸린다."""
        result, _, _ = collect(lambda unit_id: BARS[unit_id])

        assert (result.succeeded_count, result.failed_count) == (2, 0)
        assert result.expected_count == 2


class TestWindowSelection:
    def test_picks_the_bar_of_this_window_not_the_first(self):
        """페이지가 그 거래일 전체라 **라벨 대조가 유일한 선택 근거**다.

        첫 봉을 집는 구현이면 10:29 봉(close=100)이 실린다. 라벨 축이 뒤집혀 있으면
        10:31 봉(close=300)이 실린다. 둘 다 정상으로 커밋되고 소급이 없어 영구적이다.
        """
        _, records, _ = collect(lambda unit_id: BARS[unit_id])

        by_unit = {r["unit_id"]: r for r in records}
        assert by_unit["1005"]["close"] == "200"
        assert by_unit["2118"]["close"] == "500"
        # 라벨이 구간 시작이므로 레코드의 ts 는 window_start 와 같아야 한다
        assert by_unit["1005"]["ts"] == WINDOW_START

    def test_absent_window_is_missing_not_empty_success(self):
        """그 분의 봉이 없으면 missing 이다 — 빈 성공으로 접으면 결손이 사라진다."""
        result, records, manifest = collect(
            lambda unit_id: (bar(WINDOW_START + timedelta(minutes=5), "999"),),
            unit_ids=("1005",))

        assert manifest["missing"] == ["1005"]
        assert records == ()
        assert result.status == "INCOMPLETE"

    def test_two_bars_on_the_same_window_is_invalid(self):
        """같은 분에 봉이 둘이면 어느 쪽이 참인지 우리가 고를 수 없다."""
        _, _, manifest = collect(
            lambda unit_id: (bar(WINDOW_START, "200"), bar(WINDOW_START, "201")),
            unit_ids=("1005",))

        assert manifest["invalid"] == ["1005"]


class TestDeterminism:
    def test_checksum_is_membership_order_independent(self):
        """같은 멤버십을 다른 순서로 요청해도 같은 세대여야 한다 — 순회가 정렬이라서다."""
        forward, _, _ = collect(lambda u: BARS[u], unit_ids=("1005", "2118"))
        backward, _, _ = collect(lambda u: BARS[u], unit_ids=("2118", "1005"))

        assert forward.result_checksum == backward.result_checksum


class TestFailureAxes:
    """축이 셋이다 — 전파/재시도/영구. 섞이면 방향이 늘 같다(원장이 관대해지는 쪽)."""

    def _collector(self, raising):
        class FakeClient:
            retry_count = 0

            def candles(self, unit_id, *, window_end):
                raise raising

        return KisSectorIndexCollector(FakeClient(), clock=lambda: NOW)

    def test_source_wide_failure_propagates(self):
        """자격증명·IP 차단은 45종 전부가 못 나가는 축이다 — unit 실패로 접으면
        전건 missing 인 INCOMPLETE 가 매분 쌓이는데 고칠 것은 설정 하나다."""
        collector = self._collector(KisSourceError("appkey 오류"))

        with pytest.raises(KisSourceError):
            collector.collect(request_for(("1005",)), NOW)

    def test_envelope_fault_is_missing_not_invalid(self):
        """봉투 이상은 **재시도 축**이다(어댑터가 `KisUnitError` 로 되감는 이유).

        INVALID 로 올리면 `status_of` 가 window 전체를 죽이는데 INVALID 는 재청구
        대상이 아니고, 이 소스는 소급이 불가라 그 1분이 45종 전체에 대해 사라진다.
        """
        collector = self._collector(KisUnitError("응답 봉투 이상"))

        _, _, manifest = collector.collect(request_for(("1005",)), NOW)

        assert manifest["missing"] == ["1005"]
        assert manifest["invalid"] == []

    def test_row_shape_violation_is_invalid_not_missing(self):
        """행 형상 위반은 재시도로 **안** 풀린다 — missing 으로 접으면 같은 손상 응답을
        끝없이 다시 부른다. `index_map` 결손도 이 축이다(설정이 원인인데 벤더 탓으로
        보이면 아무도 고치러 안 간다)."""
        collector = self._collector(ValueError("1005 는 index_map 에 없다"))

        _, _, manifest = collector.collect(request_for(("1005",)), NOW)

        assert manifest["invalid"] == ["1005"]
        assert manifest["missing"] == []

    def test_one_unit_failing_does_not_take_the_others(self):
        """한 업종의 실패가 나머지 44종을 데려가면 안 된다."""
        def candles_for(unit_id):
            return Outcome.MISSING if unit_id == "1005" else BARS[unit_id]

        result, records, manifest = collect(candles_for)

        assert manifest == {"received": ["2118"], "no_trade": [],
                            "missing": ["1005"], "invalid": []}
        assert len(records) == 1
        assert result.status == "INCOMPLETE"
