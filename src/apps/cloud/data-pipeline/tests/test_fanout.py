"""팬아웃 헬퍼 테스트 (ALPHA-569) — 순서·격리·중단·파도 상한 (네트워크 없음).

각 테스트는 '왜'를 주석으로 남긴다(AGENTS Rule 9). 이 헬퍼는 네 어댑터의 per-item 루프를
대신하므로, 실패 semantics 가 어긋나면 collection_log 의 partial/error 판정이 어댑터마다
갈린다 — 그 계약을 여기서 잠근다.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from data_pipeline.sources.fanout import WAVE_FACTOR, fanout
from data_pipeline.sources.http import StopFetch


def _rows(item):
    return [{"item": item, "n": i} for i in range(2)]


def test_preserves_input_order_under_concurrency():
    # WHY: 완료 순으로 내면 raw ndjson 행 순서가 수집 타이밍에 따라 흔들려 재현·회귀 비교가
    #      어려워진다. 느린 대상이 섞여도 입력 순서 그대로 나와야 한다.
    #      **sleep 이 아니라 세마포어로** 첫 대상이 반드시 마지막에 끝나게 만든다 — 고정 sleep
    #      은 느린 CI 에서 첫 대상이 먼저 끝나 완료순 회귀를 놓칠 수 있다(거짓 통과).
    n = 8
    others_done = threading.Semaphore(0)

    def first_finishes_last(item):
        if item == 0:
            for _ in range(n - 1):
                assert others_done.acquire(timeout=5), "나머지 대상이 끝나지 않았다"
        else:
            others_done.release()
        return _rows(item)

    out = list(fanout(list(range(n)), first_finishes_last, concurrency=4,
                      on_failure=lambda i, e: None))
    assert [r["item"] for r in out] == [i for i in range(n) for _ in range(2)]


def test_serial_default_matches_concurrent_output():
    # WHY: `--concurrency` 미지정(=1)은 배선 전까지의 기본값이다. 직렬 경로가 병렬 결과와
    #      다르면 플래그를 켜는 순간 산출물이 조용히 바뀐다.
    items = list(range(10))
    serial = list(fanout(items, _rows, concurrency=1, on_failure=lambda i, e: None))
    parallel = list(fanout(items, _rows, concurrency=4, on_failure=lambda i, e: None))
    assert serial == parallel


def test_serial_path_uses_no_threads():
    # WHY: 기본값이 직렬이라는 건 "스레드를 안 쓴다"까지여야 한다 — 워커 1개짜리 풀을 돌리면
    #      기존 어댑터가 스레드 컨텍스트에 노출돼 이 변경의 blast radius 가 넓어진다.
    main = threading.current_thread()
    seen = []

    def record(item):
        seen.append(threading.current_thread())
        return _rows(item)

    list(fanout([1, 2, 3], record, concurrency=1, on_failure=lambda i, e: None))
    assert all(t is main for t in seen)


def test_per_item_failure_is_isolated_and_recorded():
    # WHY: 한 대상의 실패가 나머지를 죽이면 안 되고(격리), 조용히 사라져도 안 된다(기록) —
    #      격리≠은폐. 이게 collection_log 의 partial 판정 근거다(Rule 12).
    failures = []

    def boom_on_two(item):
        if item == 2:
            raise ValueError("깨진 응답")
        return _rows(item)

    out = list(fanout([1, 2, 3], boom_on_two, concurrency=2,
                      on_failure=lambda i, e: failures.append((i, str(e)))))
    assert [r["item"] for r in out] == [1, 1, 3, 3]  # 2번만 빠지고 순서 유지
    assert failures == [(2, "깨진 응답")]


def test_stop_fetch_propagates_and_halts_remaining_waves():
    # WHY: StopFetch 는 키·쿼터·세션 문제라 대상 단위 격리 대상이 아니다 — 소스 전체를 즉시
    #      중단해야 한다. 격리해 버리면 전 대상이 같은 이유로 실패하며 벤더를 계속 두드린다.
    started = []

    def stop_on_first(item):
        started.append(item)
        if item == 0:
            raise StopFetch("HTTP 429")
        return _rows(item)

    items = list(range(100))
    with pytest.raises(StopFetch):
        list(fanout(items, stop_on_first, concurrency=2, on_failure=lambda i, e: None))

    # 같은 파도 안의 대상은 이미 나갔을 수 있지만(문서화된 절충), 다음 파도는 제출되지 않는다.
    assert len(started) <= 2 * WAVE_FACTOR


def test_wave_bounds_submitted_batch(monkeypatch):
    # WHY: ThreadPoolExecutor.map 은 넘긴 항목을 **즉시 전부** 큐에 올리고 완료분을 메모리에
    #      쌓는다. 전량을 한 번에 주면 다년 백필(심볼당 최대 2만 행)에서 터진다.
    #      **제출량**을 봐야 한다 — 실행 시작 수를 세면 스케줄링 타이밍에 의존해, 200건을 한
    #      번에 큐에 올리는 회귀도 "아직 3개만 시작됨"으로 통과한다.
    concurrency = 3
    batch_sizes = []
    real_map = ThreadPoolExecutor.map

    def spy_map(self, fn, iterable, *a, **kw):
        batch = list(iterable)
        batch_sizes.append(len(batch))
        return real_map(self, fn, batch, *a, **kw)

    monkeypatch.setattr(ThreadPoolExecutor, "map", spy_map)

    list(fanout(list(range(200)), _rows, concurrency=concurrency,
                on_failure=lambda i, e: None))

    assert batch_sizes  # map 을 실제로 썼다
    assert max(batch_sizes) <= concurrency * WAVE_FACTOR
    assert sum(batch_sizes) == 200  # 파도로 끊되 하나도 빠뜨리지 않는다


def test_failures_recorded_in_input_order():
    # WHY: 실패 기록은 collection_log 의 실패 목록이 된다. 워커 완료 순으로 append 하면 같은
    #      입력에도 실행마다 순서가 달라져 감사 로그·회귀 비교가 불안정해진다.
    def fail_odd(item):
        if item % 2:
            time.sleep(0.01 * (5 - item % 5))  # 완료 순서를 입력 순서와 어긋나게
            raise ValueError(f"boom{item}")
        return _rows(item)

    failures = []
    list(fanout(list(range(10)), fail_odd, concurrency=5,
                on_failure=lambda i, e: failures.append(i)))
    assert failures == [1, 3, 5, 7, 9]


def test_partial_rows_discarded_when_item_fails():
    # WHY: 대상 처리가 중간에 실패하면 그 대상의 행을 **전부 버린다**(all-or-nothing).
    #      옛 직렬 코드는 `yield from` 이라 실패 전 행이 raw 에 남았는데, 그건 구성종목 일부만
    #      든 스냅샷을 남겨 하류 비중 합이 100%에 못 미치게 한다. 격리 의미에도 전부-또는-없음이
    #      맞고, 직렬·병렬 산출물이 같아야 한다는 요구와도 맞는다. 동작이 바뀐 지점이라 잠근다.
    def half_then_boom(item):
        yield {"item": item, "n": 0}
        raise ValueError("중간에 깨짐")

    for concurrency in (1, 4):
        failures = []
        out = list(fanout([7], half_then_boom, concurrency=concurrency,
                          on_failure=lambda i, e: failures.append(i)))
        assert out == [], f"concurrency={concurrency} 에서 부분 행이 남았다"
        assert failures == [7]
