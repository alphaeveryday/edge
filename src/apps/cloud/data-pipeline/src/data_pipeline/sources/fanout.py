"""수집 어댑터 per-item 루프의 병렬 실행 (ALPHA-569).

`kis_price`·`kis_investor`·`kis_nav`·`krx_etf` 의 `fetch()` 가 같은 형상이다 — `plan()` 으로
대상을 뽑고, 인증을 run 당 1회 하고, 대상마다 요청해 행을 내되 `StopFetch` 는 소스 전체를
중단하고 그 밖의 예외는 대상 단위로 격리한다. 그 루프만 여기로 뽑는다.

⚠️ **지금 이 헬퍼를 쓰는 곳은 `krx_etf` 하나뿐이다.** 나머지 셋은 ALPHA-570 에서 옮겨온다
(같은 스프린트의 바로 다음 티켓). 단일 호출부 추상화는 Rule 2 위반이라 리뷰에서 지적받았고,
지금 인라인했다가 다음 티켓에서 다시 뽑는 churn 대신 이 상태를 명시해 두는 쪽을 택했다.
근거는 **실패 semantics 가 어댑터마다 갈리면 안 된다**는 것이다 — 격리·중단 규약이 갈리면
collection_log 의 partial/error 판정이 어댑터마다 달라진다. 다만 아래 정책(all-or-nothing·
파도·StopFetch 절충)은 **현재 KRX 로만 검증됐다**. 570 에서 어댑터별로 재확인한다.

유량 제어는 여기 없다 — `PoliteClient` 가 발신률을 묶는다(ALPHA-568). 워커는 클라이언트
**하나를 공유**해야 한다. 워커마다 클라이언트를 두면 rate 가 워커 수만큼 샌다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from .http import StopFetch

T = TypeVar("T")

# 한 번에 큐에 올릴 대상 수 = 동시성 × 이 배수. `ThreadPoolExecutor.map` 은 넘긴 항목을
# **즉시 전부** 큐에 넣고 완료분을 메모리에 쌓으므로, 전량을 한 번에 주면 다년 백필에서
# 터진다(가격은 심볼당 최대 MAX_PAGES×100 = 2만 행). 파도로 끊어 상한을 둔다.
WAVE_FACTOR = 4


def _collect(item: T, worker: Callable[[T], Iterable[dict]]) -> tuple[list[dict], Exception | None]:
    """대상 하나를 처리해 (행 목록, 격리된 예외)를 만든다.

    **행을 먼저 리스트로 만든다** — 스레드에서 제너레이터를 그대로 흘릴 수 없기 때문이다.
    그래서 대상 처리가 중간에 실패하면 그 대상의 행은 **전부 버린다**(all-or-nothing).
    옛 직렬 코드는 `yield from` 이라 실패 전까지의 행이 raw 에 남았는데, 그건 구성종목
    일부만 든 스냅샷을 남겨 하류 비중 합이 100%에 못 미치게 한다. 전부-또는-없음이
    격리 의미에 더 맞고, 직렬·병렬 산출물이 같아야 한다는 요구와도 맞는다.

    `on_failure` 를 여기서 부르지 않는 것은 의도다 — 워커 완료 순으로 부르면
    `fetch_failures`(= collection_log 의 실패 목록) 순서가 실행마다 달라진다. 호출부가
    **입력 순서로** 소비하며 부른다.
    """
    try:
        return list(worker(item)), None
    except StopFetch:
        raise  # 4xx/429·미로그인 — 소스 전체 문제라 격리 대상이 아니다
    except Exception as exc:  # noqa: BLE001 — 대상 단위 격리가 이 계층의 계약이다
        return [], exc


def fanout(
    items: Sequence[T],
    worker: Callable[[T], Iterable[dict]],
    *,
    concurrency: int,
    on_failure: Callable[[T, Exception], None],
) -> Iterator[dict]:
    """대상들을 `worker` 로 처리해 행을 **입력 순서대로** 낸다.

    - **순서 보존**: 완료 순(as-completed)이 아니라 `map` 이다. raw ndjson 행 순서가 수집
      타이밍에 따라 흔들리면 재현·회귀 비교가 어려워진다. 헤드오브라인 손해는 감수한다.
    - **`StopFetch` 전파**: 소비 시점에 그대로 올라가 남은 파도는 제출되지 않는다. 다만 **같은
      파도 안의 다른 대상은 이미 요청이 나갔을 수 있다** — 직렬이던 옛 동작보다 최대 한 파도분
      더 요청한다. 중단 사유가 키·쿼터·세션이라 그만큼 더 두드리는 대가는 감수한다.
    - **격리 기록**: `on_failure` 가 어댑터의 `_note_failure` 다. 리스트 append 는 GIL 하에서
      원자적이라 별도 락이 필요 없다(락을 더하면 없는 문제를 방어하는 코드가 된다).

    `concurrency <= 1` 이면 스레드를 아예 쓰지 않는다 — 기본값이 직렬이라 이 변경이 배선되기
    전까지 동작이 정확히 이전과 같아야 한다.
    """
    if concurrency <= 1:
        for item in items:
            rows, exc = _collect(item, worker)
            if exc is not None:
                on_failure(item, exc)
            yield from rows
        return

    wave = concurrency * WAVE_FACTOR
    pool = ThreadPoolExecutor(max_workers=concurrency)
    try:
        for start in range(0, len(items), wave):
            batch = items[start:start + wave]
            for item, (rows, exc) in zip(batch, pool.map(lambda it: _collect(it, worker), batch)):
                if exc is not None:
                    on_failure(item, exc)  # 입력 순서로 기록 — 완료 순이면 로그가 비결정적
                yield from rows
    finally:
        # `with` 의 기본 종료는 `wait=True, cancel_futures=False` 라 StopFetch·소비자 중단·
        # gen.close() 에서도 **이미 제출된 파도 전체**를 끝까지 돌린다(최대 concurrency×4 건을
        # 벤더에 더 던지고 그만큼 매달린다). 아직 시작 안 한 작업은 취소한다 — 실행 중인
        # 것은 스레드라 못 끊지만 in-flight 가 concurrency 로 묶인다.
        pool.shutdown(wait=True, cancel_futures=True)
