"""KIS 수집기 제한 동시 요청(ALPHA-1087) — 업무 결과는 순차와 같아야 한다.

동시성은 HTTP 응답 대기를 겹치려는 것이지 업무 의미를 바꾸려는 게 아니다: 완료 순서가 달라도
records·checksum·분류가 같고, 소스 전역 실패는 그대로 window 를 죽이며, 이번 window 의 재시도 수가
사라지지 않고, 토큰 첫 발급은 병렬 시작 전에 한 번이다.
"""

from __future__ import annotations

import random
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_kis_collector import WINDOW_END, WINDOW_START, candle, flat  # noqa: E402

from data_pipeline.minute.kis_collector import KisPriceCollector
from data_pipeline.minute.models import CollectionRequest
from data_pipeline.sources.kis_minute import KisSourceError, KisUnitError


class Auth:
    def __init__(self, events):
        self.events = events

    def token(self):
        self.events.append("token")
        return "t"


class SlowClient:
    """응답 순서를 섞는 가짜 클라이언트 — 완료 순서가 결과에 새면 checksum 이 흔들린다."""

    def __init__(self, by_unit, seed=0, retries_per_call=0):
        self.by_unit, self.rng = by_unit, random.Random(seed)
        self.retry_count, self.events = 0, []
        self.auth = Auth(self.events)
        self.lock = threading.Lock()
        self.in_flight = self.max_in_flight = 0
        self.retries_per_call = retries_per_call

    def candles(self, symbol, *, window_end):
        with self.lock:
            self.events.append("call")
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            delay = self.rng.random() * 0.01
            self.retry_count += self.retries_per_call
        time.sleep(delay)
        with self.lock:
            self.in_flight -= 1
        out = self.by_unit[symbol]
        if isinstance(out, Exception):
            raise out
        return out


def request(units):
    return CollectionRequest(dataset="price_minute", window_start=WINDOW_START, window_end=WINDOW_END,
                             run_id="run-1", session_id="msn_x", execution_mode="resident",
                             universe_version="u1", unit_ids=tuple(units))


def by_unit(n=40):
    out = {}
    for i in range(n):
        u = f"{i:06d}"
        out[u] = (flat(u),) if i % 7 == 0 else KisUnitError("x") if i % 11 == 0 else (candle(u, close=str(100 + i)),)
    return out


@pytest.mark.parametrize("concurrency", [2, 4])
def test_same_records_checksums_and_manifest_as_sequential(concurrency):
    units = by_unit()
    seq = KisPriceCollector(client=SlowClient(units, 1), clock=lambda: WINDOW_END).collect(request(units), WINDOW_END)
    par = KisPriceCollector(client=SlowClient(units, 2), clock=lambda: WINDOW_END,
                            concurrency=concurrency).collect(request(units), WINDOW_END)
    assert par[1] == seq[1] and par[2] == seq[2]
    assert par[0].result_checksum == seq[0].result_checksum
    assert par[0].manifest_checksum == seq[0].manifest_checksum and par[0].status == seq[0].status


def test_in_flight_is_bounded_and_token_is_fetched_before_parallel_calls():
    client = SlowClient(by_unit())
    KisPriceCollector(client=client, clock=lambda: WINDOW_END, concurrency=3).collect(request(by_unit()), WINDOW_END)
    assert client.max_in_flight <= 3
    assert client.events[0] == "token" and client.events.count("token") == 1


def test_source_failure_still_kills_the_window_and_cancels_unstarted_calls():
    units = by_unit(60)
    units["000005"] = KisSourceError("global")
    client = SlowClient(units)
    with pytest.raises(KisSourceError):
        KisPriceCollector(client=client, clock=lambda: WINDOW_END, concurrency=2).collect(request(units), WINDOW_END)
    assert client.events.count("call") < 60          # 아직 시작 안 한 요청은 보내지 않았다


def test_window_retry_count_is_not_lost():
    units = by_unit(20)
    result = KisPriceCollector(client=SlowClient(units, retries_per_call=1), clock=lambda: WINDOW_END,
                               concurrency=3).collect(request(units), WINDOW_END)[0]
    assert result.retry_count == 20
