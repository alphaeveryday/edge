"""토스 분봉 어댑터 테스트 — **녹화 fixture**(실호출 응답)로만 검증한다.

fixture 는 `tests/fixtures/toss/`(2026-08-01 dev VPC 에서 실호출 녹화). 손으로 쓴 형상이
아니라 실제 응답이라, 이 테스트가 초록이면 그 형상에 대해서는 실제로 도는 것이다.

여기서 고정하는 계약 셋:
1. **timestamp 는 구간의 끝** — `window_start = ts − 1분`. 뒤집히면 전 구간이 한 칸 밀린
   채 커밋되는데 캔들 수는 그대로라 어떤 게이트도 안 걸린다.
2. **거래량 0 은 성공(no_trade)** — 행이 없는 것(missing)과 다르다. 실패로 세면 한산한
   종목이 매분 재시도를 유발해 window 가 영원히 INCOMPLETE 다.
3. **한 종목의 실패가 window 를 죽이지 않는다** — 그 종목만 missing 으로 남는다.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from data_pipeline.minute.models import KST, CollectionRequest
from data_pipeline.minute.states import (
    WINDOW_INCOMPLETE,
    WINDOW_VALID,
    WINDOW_VALID_EMPTY,
)
from data_pipeline.minute.toss_collector import TossPriceCollector
from data_pipeline.sources.toss import (
    MAX_COUNT,
    SUPPORTED_INTERVALS,
    TossApiError,
    TossOpenApiClient,
    parse_candle,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "toss"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def candle_rows(name: str) -> list[dict]:
    return fixture(name)["body"]["result"]["candles"]


class FakeResponse:
    def __init__(self, payload: dict, headers: dict | None = None, status: int = 200):
        self._body = json.dumps(payload).encode()
        self.headers = headers or {}
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def make_client(responses, **overrides):
    """응답을 순서대로 돌려주는 client — 벽시계 대기 없이 간격 규칙만 관찰한다."""
    slept: list[float] = []
    clock = {"now": 0.0}
    queue = list(responses)
    calls: list[str] = []

    def opener(request, timeout=None):
        if not queue:
            raise AssertionError("예상보다 많은 호출")
        calls.append(request.full_url)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(item)

    def sleep(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    client = TossOpenApiClient(
        client_id="id", client_secret="secret", opener=opener, sleep=sleep,
        monotonic=lambda: clock["now"], **overrides,
    )
    return client, slept, clock, calls


TOKEN = {"access_token": "t-1", "token_type": "Bearer", "expires_in": 86399}


class TestCandleParsing:
    def test_timestamp_is_the_end_of_the_window(self):
        # ⚠️ 이 한 줄이 뒤집히면 전 구간이 한 칸 밀린 채 조용히 커밋된다
        row = candle_rows("candles_stock_1m")[0]
        candle = parse_candle(row, "005930")
        assert candle.window_end == datetime.fromisoformat("2026-07-31T20:00:00.000+09:00")
        assert candle.window_start == candle.window_end - timedelta(minutes=1)

    def test_prices_keep_precision(self):
        # 문자열로 오는 값을 float 로 받으면 정밀도가 깨진다
        candle = parse_candle(candle_rows("candles_stock_1m")[0], "005930")
        assert (candle.open, candle.close) == (Decimal("258500"), Decimal("259000"))
        assert candle.volume == Decimal("290415") and candle.traded

    def test_zero_volume_is_a_valid_candle(self):
        # 저유동 종목은 하루 390개 중 376개가 이 모양이었다(실측)
        flat = [r for r in candle_rows("candles_lowliquidity_1m") if r["volume"] == "0"][0]
        candle = parse_candle(flat, "001527")
        assert candle.volume == Decimal("0") and candle.traded is False
        assert candle.open == candle.close  # 직전가로 채운 flat 캔들

    def test_index_candle_has_no_currency(self):
        # 지수 응답엔 currency 가 없다 — 필수로 다루면 지수 수집이 통째로 깨진다
        candle = parse_candle(candle_rows("candles_index_1m")[0], "KOSPI")
        assert candle.currency is None

    @pytest.mark.parametrize("mutation", [
        {"timestamp": "2026-07-31T20:00:00.000"},        # 오프셋 없음 — 시간대 추측 금지
        {"timestamp": "not-a-time"},
        {"volume": "-1"},
        {"closePrice": None},
        {"closePrice": "nope"},
        {"highPrice": "1"},                               # OHLC 정합 위반(low>high)
    ])
    def test_broken_rows_raise(self, mutation):
        row = dict(candle_rows("candles_stock_1m")[0]) | mutation
        with pytest.raises(ValueError):
            parse_candle(row, "005930")


class TestClient:
    def test_token_is_reused_until_close_to_expiry(self):
        client, _, clock, calls = make_client([TOKEN, {"result": {"candles": []}},
                                        {"result": {"candles": []}}])
        client.candles("005930")
        clock["now"] += 100          # 24시간짜리 토큰이라 아직 유효하다
        client.candles("005930")     # 토큰 재발급 없이 조회만(응답 2개면 충분)

    def test_rate_limit_is_respected_between_calls(self):
        # 처리량은 RTT 가 아니라 **간격**에 묶인다 — 이 규칙이 빠지면 429 를 맞는다
        client, slept, _, calls = make_client(
            [TOKEN] + [{"result": {"candles": []}}] * 3, min_interval=0.2
        )
        for _ in range(3):
            client.candles("005930")
        assert slept and all(0 < s <= 0.2 for s in slept)

    def test_rate_limited_call_is_retried(self):
        import urllib.error

        def http_error(status, body):
            return urllib.error.HTTPError(
                "u", status, "err", {}, __import__("io").BytesIO(json.dumps(body).encode())
            )

        client, slept, _, calls = make_client([
            TOKEN,
            http_error(429, fixture("error_rate_limited")["body"]),
            {"result": {"candles": candle_rows("candles_stock_1m")[:1]}},
        ])
        assert len(client.candles("005930")) == 1
        assert slept, "429 를 맞고 물러나지 않았다"

    def test_permanent_errors_are_not_retried(self):
        # 없는 종목을 다시 물어도 같은 답이다 — 재시도는 한도만 먹고 window 를 늦춘다
        import io
        import urllib.error

        error = urllib.error.HTTPError(
            "u", 404, "err", {},
            io.BytesIO(json.dumps(fixture("error_stock_not_found")["body"]).encode()),
        )
        client, slept, _, calls = make_client([TOKEN, error, error, error])
        with pytest.raises(TossApiError) as caught:
            client.candles("999999")
        assert caught.value.code == "stock-not-found" and not caught.value.retryable
        # 토큰 1 + 조회 1 — 재시도했다면 3~4가 된다(한도만 먹고 window 가 늦어진다)
        assert len(calls) == 2

    @pytest.mark.parametrize("kwargs", [
        {"interval": "5m"}, {"interval": "1h"},           # API 가 알려준 어휘 밖
        {"count": 0}, {"count": MAX_COUNT + 1},           # 400 constraint 와 같은 범위
    ])
    def test_out_of_contract_requests_are_refused_before_calling(self, kwargs):
        client, _, _, calls = make_client([])                     # 호출이 없어야 통과한다
        with pytest.raises(ValueError):
            client.candles("005930", **kwargs)

    def test_supported_intervals_match_what_the_api_advertises(self):
        # 400 응답의 data.allowedValues 가 근거다(추측 아님)
        advertised = fixture("error_bad_interval")["body"]["error"]["data"]["allowedValues"]
        assert list(SUPPORTED_INTERVALS) == advertised


WINDOW_END = datetime.fromisoformat("2026-07-31T20:00:00.000+09:00")


def make_request(unit_ids):
    return CollectionRequest(
        dataset="price_minute", window_start=WINDOW_END - timedelta(minutes=1),
        window_end=WINDOW_END, run_id="run-1", session_id="msn_x",
        execution_mode="resident", universe_version="v1", unit_ids=tuple(unit_ids),
    )


class StubClient:
    def __init__(self, by_symbol):
        self.by_symbol = by_symbol
        self.calls = []

    def candles(self, symbol, *, interval="1m", count=1, before=None):
        self.calls.append(symbol)
        outcome = self.by_symbol[symbol]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def parsed(name, symbol, index=0):
    return parse_candle(candle_rows(name)[index], symbol)


class TestCollector:
    def test_classifies_traded_no_trade_and_missing(self):
        traded = parsed("candles_stock_1m", "005930")
        flat = [c for c in
                (parse_candle(r, "001527") for r in candle_rows("candles_lowliquidity_1m"))
                if not c.traded][0]
        # 거래 없는 종목의 캔들을 이 window 로 옮겨 같은 분을 보게 한다
        flat = flat.__class__(**{**flat.__dict__,
                                 "window_end": WINDOW_END,
                                 "window_start": WINDOW_END - timedelta(minutes=1)})
        client = StubClient({
            "005930": (traded,),
            "001527": (flat,),
            "000660": (),                       # 그 분의 캔들이 없다 → missing
        })
        collector = TossPriceCollector(client=client)

        result, records, manifest = collector.collect(
            make_request(["005930", "001527", "000660"]), WINDOW_END
        )

        assert manifest == {"received": ["005930"], "no_trade": ["001527"],
                            "missing": ["000660"]}
        assert result.status == WINDOW_INCOMPLETE
        # 거래 없는 분은 **성공**으로 센다 — 실패로 세면 매분 재시도가 붙는다
        assert (result.succeeded_count, result.failed_count) == (2, 1)
        assert [r["unit_id"] for r in records] == ["005930"]
        assert records[0]["ts"] == WINDOW_END - timedelta(minutes=1)

    def test_all_no_trade_is_valid_empty_not_valid(self):
        # '데이터가 있다'와 '없는 게 정상이다'가 같은 상태가 되면 EOD QC 가 못 가른다
        flat = [c for c in
                (parse_candle(r, "001527") for r in candle_rows("candles_lowliquidity_1m"))
                if not c.traded][0]
        flat = flat.__class__(**{**flat.__dict__, "window_end": WINDOW_END,
                                 "window_start": WINDOW_END - timedelta(minutes=1)})
        collector = TossPriceCollector(client=StubClient({"001527": (flat,)}))
        result, records, manifest = collector.collect(make_request(["001527"]), WINDOW_END)
        assert result.status == WINDOW_VALID_EMPTY and records == ()

    def test_other_window_candle_is_not_accepted(self):
        # 응답 최신 캔들이 우리가 원한 분이 아닐 수 있다 — ts 대조 없이 받으면 한 칸
        # 밀린 값이 그 window 의 데이터로 커밋된다
        other = parsed("candles_stock_1m", "005930", index=2)   # 19:58 캔들
        collector = TossPriceCollector(client=StubClient({"005930": (other,)}))
        _result, records, manifest = collector.collect(make_request(["005930"]), WINDOW_END)
        assert manifest["missing"] == ["005930"] and records == ()

    def test_one_symbol_failure_does_not_kill_the_window(self):
        collector = TossPriceCollector(client=StubClient({
            "005930": (parsed("candles_stock_1m", "005930"),),
            "999999": TossApiError(404, "stock-not-found", "종목을 찾을 수 없습니다."),
        }))
        result, records, manifest = collector.collect(
            make_request(["005930", "999999"]), WINDOW_END
        )
        assert manifest["received"] == ["005930"] and manifest["missing"] == ["999999"]
        assert result.status == WINDOW_INCOMPLETE

    def test_checksum_is_order_independent(self):
        # 같은 멤버십을 다른 순서로 요청해도 같은 checksum — 아니면 재실행마다 세대가 오른다
        candles = {"005930": (parsed("candles_stock_1m", "005930"),),
                   "000660": ()}
        first, _, _ = TossPriceCollector(client=StubClient(candles)).collect(
            make_request(["005930", "000660"]), WINDOW_END)
        second, _, _ = TossPriceCollector(client=StubClient(candles)).collect(
            make_request(["000660", "005930"]), WINDOW_END)
        assert first.result_checksum == second.result_checksum

    def test_full_window_is_valid(self):
        collector = TossPriceCollector(client=StubClient({
            "005930": (parsed("candles_stock_1m", "005930"),)}))
        result, records, _ = collector.collect(make_request(["005930"]), WINDOW_END)
        assert result.status == WINDOW_VALID and len(records) == 1
        assert result.watermark_after == WINDOW_END
