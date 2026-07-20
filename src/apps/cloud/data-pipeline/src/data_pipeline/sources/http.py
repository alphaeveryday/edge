"""저부하 HTTP 클라이언트 (프로토타입 PoliteClient 축소 이식).

- 요청 간 최소 간격(직렬 호출 전제 — 어댑터가 심볼별로 순차 질의)
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
    def __init__(self, *, min_interval: float = 1.0, timeout: float = 10.0):
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request_at = 0.0

    # 테스트에서 대기 없이 돌리도록 교체 가능한 지점.
    _sleep = staticmethod(time.sleep)

    def _respect_interval(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if self._last_request_at and elapsed < self.min_interval:
            self._sleep(self.min_interval - elapsed)

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
                    self._last_request_at = time.monotonic()
                    body = resp.read()
                    return body.decode("utf-8", errors="replace") if decode else body
            except urllib.error.HTTPError as exc:
                self._last_request_at = time.monotonic()
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
                self._last_request_at = time.monotonic()
                last_exc = exc
        raise RuntimeError(f"{method} 재시도 소진: {last_exc}")

    def get(self, url: str, *, accept: str = "application/json") -> str:
        """GET 후 본문 문자열 반환. 4xx/429 는 StopFetch, 재시도 소진은 RuntimeError."""
        body = self.request("GET", url, headers={"Accept": accept}, decode=True)
        assert isinstance(body, str)  # decode=True 라 항상 str
        return body
