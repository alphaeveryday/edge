"""저부하 HTTP 클라이언트 (프로토타입 PoliteClient 축소 이식).

- 요청 간 최소 간격 = **평균 발신률 상한**. 클라이언트 1개를 워커 여럿이 공유해도
  전체 발신률이 1/min_interval 로 묶인다(thread-safe). 인접 간격은 보장 대상이 아니다
- 5xx/일시 오류는 지수 백오프(1→2→4초) 재시도
- 4xx/429 는 즉시 중단(StopFetch) — 키 오류·쿼터 초과를 재시도로 두드리지 않는다

간격 강제·재시도·StopFetch 백본은 `request()` 한 곳에 있고, `get()` 은 그 위의
하위호환 래퍼다(GET+Accept). KR 벤더는 이 코어를 재사용한다 — KIS·BigKinds 는 커스텀
헤더·POST 본문이, OpenDART 는 바이너리(ZIP) 응답이 필요해 request() 인자로 표현한다.
단, 초당한도가 HTTP 429 가 아니라 응답 본문(예 KIS EGW00201)으로 오는 벤더의 재시도는
운반 계층이 본문 의미를 모르므로 각 어댑터가 처리한다(여긴 운반만).

stdlib(urllib)만 사용해 의존성 없이 단위테스트에서 import 된다.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from ..failures import SafeFailureError

RETRY_BACKOFF_SEC = [1, 2, 4]


class CallStats:
    """발신 계측 누적기(ALPHA-1124) — 이름 붙은 합계를 모으고, 읽는 쪽이 `drain()` 으로 가져가며 비운다.

    운반 계층(`PoliteClient`)과 어댑터가 **같은 인스턴스**에 더한다. 한 구간의 소요를 발신 대기·
    응답 소요·재시도 대기로 나눠 보려면 한 줄에 있어야 하기 때문이다(느린 응답과 유량 제한은 합계
    시간만으로는 구분되지 않는다). 받는 값은 건수와 초뿐이다 — URL·헤더·응답 본문은 받지 않는다.
    thread-safe(동시 요청).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sums: dict[str, float] = {}

    def add(self, **amounts: float) -> None:
        """이름별 합계에 더한다. 초 단위 값은 이름을 `_sec` 로 끝낸다(`drain` 이 밀리초로 낸다)."""
        with self._lock:
            for key, amount in amounts.items():
                self._sums[key] = self._sums.get(key, 0) + amount

    @contextmanager
    def attempt(self):
        """발신 1회 — 시도 수·응답 소요(합·최대)와 실패 종류를 센다. 예외는 그대로 지나간다."""
        started = time.monotonic()
        try:
            yield
        except Exception as exc:
            self.add(**{f"err_{_error_kind(exc)}": 1})
            raise
        finally:
            rtt = time.monotonic() - started
            with self._lock:
                self._sums["attempts"] = self._sums.get("attempts", 0) + 1
                self._sums["rtt_sec"] = self._sums.get("rtt_sec", 0) + rtt
                self._sums["rtt_max_sec"] = max(self._sums.get("rtt_max_sec", 0), rtt)

    def drain(self) -> dict[str, int]:
        """누적을 읽고 비운다 — 건수는 그대로, 초(`*_sec`)는 밀리초 정수(`*_ms`)로 낸다."""
        with self._lock:
            sums, self._sums = self._sums, {}
        return {
            (key[:-4] + "_ms" if key.endswith("_sec") else key):
                round(value * 1000) if key.endswith("_sec") else int(value)
            for key, value in sums.items()
        }


def _error_kind(exc: Exception) -> str:
    """실패 종류의 이름 — HTTP 상태코드 또는 예외 클래스명. 예외 **문자열**은 쓰지 않는다
    (URL·프록시 자격증명이 들어갈 수 있다 — 아래 `SafeFailureError` 주석과 같은 이유).
    `URLError` 는 감싼 원인(시간 초과·연결 거부·DNS)이 구분의 실체라 그쪽 이름을 쓴다."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    reason = getattr(exc, "reason", None)
    return type(reason if isinstance(reason, BaseException) else exc).__name__


class StopFetch(Exception):
    """4xx/429 — 이 소스에 대한 수집을 즉시 중단해야 한다.

    `status` 로 HTTP 상태코드를 함께 싣는다 — 대부분의 4xx 는 중단이 맞지만, 벤더가 4xx 로
    **일시적 유량 제한**을 표현하는 경우가 있어(KIS 토큰 발급 403 EGW00133 "1분당 1회")
    어댑터가 그 하나만 골라 처리하려면 코드가 필요하다. 본문 의미 판정은 여전히 어댑터 몫이다
    (운반 계층은 벤더 오류 어휘를 모른다).
    """

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        # 4xx 응답 본문(잘린 원문). 운반 계층은 이걸 **해석하지 않는다** — 벤더 오류 어휘를
        # 아는 건 어댑터뿐이라, 판정에 필요한 원문만 실어 보낸다.
        self.body = body


class PoliteClient:
    """발신 **슬롯 획득**을 1/min_interval 로 pace 하는 공유 HTTP 클라이언트(thread-safe).

    실제 발신 시각·인접 간격은 보장 대상이 아니다 — 보장 범위와 그 절충은
    `_respect_interval` 도크스트링이 정본이다. 재시도·StopFetch 는 `request()`.
    """

    def __init__(self, *, min_interval: float = 1.0, timeout: float = 10.0, pacer=None):
        self.min_interval = min_interval
        self.timeout = timeout
        # 공유 호출 허용(sources/call_budget.py). 있으면 매 발신 시도가 pacer.pace() 를 거치고
        # 로컬 간격(min_interval)은 쓰지 않는다 — 그 간격은 계정 예산을 나눠 쓰려던 값이다.
        self.pacer = pacer
        # 다음 요청을 보낼 수 있는 가장 이른 시각(monotonic). 워커 여럿이 공유해도 전체
        # 발신 속도가 1/min_interval 로 묶인다(_respect_interval 주석).
        self._next_slot_at = 0.0
        self._lock = threading.Lock()
        # 발신 계측(ALPHA-1124). 읽는 호출자가 없으면 키 몇 개의 합계로 남을 뿐이다.
        self.stats = CallStats()

    # 테스트에서 대기 없이 돌리도록 교체 가능한 지점.
    _sleep = staticmethod(time.sleep)

    def _respect_interval(self) -> None:
        """다음 발신 슬롯까지 대기한다 — **평균 발신률을 1/min_interval 로 묶는다.**

        보장하는 것과 못 하는 것을 구분해 둔다:

        - **하는 일**: 발신 **슬롯 획득**을 1/min_interval 로 pace 한다. 병리적 스케줄링
          지연이 없으면 실제 발신률이 그대로 따라가고, 그게 벤더 한도가 걸리는 축이다
          (KIS `EGW00201` 은 초당 카운터, 앱키당 20/s 를 네 스텝이 나눠 쓴다).
        - **보장하지 않음**: 실제 발신 시각. 인접 간격은 물론이고 **평균률도 엄밀히는 아니다** —
          락이 `urlopen` **전에** 풀리므로, 슬롯을 딴 스레드가 요청 직전에 선점되면 뒤 슬롯
          요청들이 먼저 나가고 지연분이 나중에 합류해 순간적으로 몰릴 수 있다(리뷰에서 배리어로
          강제해 재현). 막으려면 `urlopen` 까지 락을 잡아야 하는데 그러면 I/O 가 직렬화돼
          팬아웃이 무의미해진다. 그래서 예산은 문서 한도(20/s)에 **마진을 두고**(15/s) 잡는다 —
          이 절충의 대가를 마진으로 치른다.

        대기는 락 안에서 한다. 슬롯만 예약하고 락 밖에서 자도 평균률은 같지만, 락 안에서
        기다리면 대기 자체가 직렬화돼 실측 간격이 더 고르게 나온다(같은 조건에서 인접 간격
        0.021s vs 0.016s). I/O 는 여전히 락 밖이라 요청은 그대로 동시에 나간다.

        다음 슬롯은 예약 시각이 아니라 **실제로 깬 시각** 기준이다. 이상 격자를 쓰면 늦게 깬
        지연만큼 다음 간격이 깎인다. 실제 시각 기준이면 격자가 함께 밀려 느려지는 쪽으로만 틀린다.

        간격 기준은 **발신 시각**(start-to-start)이다. 이전 구현은 직전 응답 **완료** 시각
        기준이라 워커가 N개면 각자 마지막 완료만 보고 동시에 나가 유량이 N배로 샜다.

        ⚠️ **직렬 경로도 빨라진다.** 옛 간격은 `RTT + min_interval`, 새 간격은
        `max(RTT, min_interval)` 이다. 실측 기준:
          - KIS  (RTT 0.78s, interval 0.5s): 0.78 → 1.28 req/s  (+64%)
          - KRX  (RTT 12.4s, interval 1.0s): 0.0746 → 0.0806 req/s  (+8%)
        KIS 4브랜치 합이 최대 5.1 req/s 라 문서값 20/s 대비 여유가 크다.
        """
        with self._lock:
            wait = self._next_slot_at - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._next_slot_at = time.monotonic() + self.min_interval

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
        decode: bool = True,
    ) -> str | bytes:
        """운반 코어. 간격 강제 + 5xx/네트워크 재시도 + 4xx/429 StopFetch.

        - method/headers/data 로 GET·POST·커스텀 헤더를 표현한다(data 있으면 POST 본문).
        - decode=True 면 UTF-8 문자열, False 면 원본 bytes 를 돌려준다(바이너리 ZIP 등).
        재시도 소진은 SafeFailureError. 4xx/429 는 재시도·격리 대상이 아니라 즉시 StopFetch.
        """
        for backoff in [0, *RETRY_BACKOFF_SEC]:
            if backoff:
                self._sleep(backoff)
                self.stats.add(transport_retry=1, transport_backoff_sec=backoff)
            # 재시도도 새 허용을 받는다(같은 전체 예산). pacer 가 돌아온 뒤 소켓 쓰기까지의 지연(연결·
            # TLS 핸드셰이크)은 통제하지 못한다 — call_budget 도크스트링.
            paced_from = time.monotonic()
            if self.pacer is not None:
                self.pacer.pace()
            else:
                self._respect_interval()
            self.stats.add(pace_wait_sec=time.monotonic() - paced_from)
            req = urllib.request.Request(
                url, data=data, headers=headers or {}, method=method
            )
            try:
                with self.stats.attempt(), urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    return body.decode("utf-8", errors="replace") if decode else body
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or 400 <= exc.code < 500:
                    try:
                        detail = exc.read().decode("utf-8", errors="replace")[:500]
                    except Exception:
                        detail = ""  # 본문을 못 읽어도 중단 자체는 그대로 진행한다
                    raise StopFetch(
                        f"HTTP {exc.code}: 수집 중단",
                        status=exc.code, body=detail,
                    ) from exc
                pass  # 5xx → 재시도
            except (urllib.error.URLError, TimeoutError):
                pass  # 네트워크 실패 → 재시도
        # 원본 예외 문자열에는 URL·프록시 자격증명 등이 들어갈 수 있다. 호출자는 고정 코드로
        # 일시 장애를 분류하고 원문은 현재 traceback 밖으로 전달하지 않는다(ALPHA-1064).
        raise SafeFailureError("NETWORK_RETRY_EXHAUSTED")

    def get(self, url: str, *, accept: str = "application/json") -> str:
        """GET 후 본문 문자열 반환. 4xx/429 는 StopFetch, 재시도 소진은 SafeFailureError."""
        body = self.request("GET", url, headers={"Accept": accept}, decode=True)
        assert isinstance(body, str)  # decode=True 라 항상 str
        return body
