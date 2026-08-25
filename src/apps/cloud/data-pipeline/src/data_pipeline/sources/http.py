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

RETRY_BACKOFF_SEC = [1, 2, 4]


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

    def __init__(self, *, min_interval: float = 1.0, timeout: float = 10.0):
        self.min_interval = min_interval
        self.timeout = timeout
        # 다음 요청을 보낼 수 있는 가장 이른 시각(monotonic). 워커 여럿이 공유해도 전체
        # 발신 속도가 1/min_interval 로 묶인다(_respect_interval 주석).
        self._next_slot_at = 0.0
        self._lock = threading.Lock()

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
        재시도 소진은 RuntimeError. 4xx/429 는 재시도·격리 대상이 아니라 즉시 StopFetch.
        """
        last_exc: Exception | None = None
        for backoff in [0, *RETRY_BACKOFF_SEC]:
            if backoff:
                self._sleep(backoff)
            self._respect_interval()
            req = urllib.request.Request(
                url, data=data, headers=headers or {}, method=method
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    return body.decode("utf-8", errors="replace") if decode else body
            except urllib.error.HTTPError as exc:
                if exc.code == 429 or 400 <= exc.code < 500:
                    try:
                        detail = exc.read().decode("utf-8", errors="replace")[:500]
                    except Exception:
                        detail = ""  # 본문을 못 읽어도 중단 자체는 그대로 진행한다
                    raise StopFetch(
                        f"HTTP {exc.code}: 수집 중단 {detail}".rstrip(),
                        status=exc.code, body=detail,
                    ) from exc
                last_exc = exc  # 5xx → 재시도
            except (urllib.error.URLError, TimeoutError) as exc:
                last_exc = exc
        raise RuntimeError(f"{method} 재시도 소진: {last_exc}")

    def get(self, url: str, *, accept: str = "application/json") -> str:
        """GET 후 본문 문자열 반환. 4xx/429 는 StopFetch, 재시도 소진은 RuntimeError."""
        body = self.request("GET", url, headers={"Accept": accept}, decode=True)
        assert isinstance(body, str)  # decode=True 라 항상 str
        return body
