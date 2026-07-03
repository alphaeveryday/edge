"""저부하 HTTP 클라이언트 (프로토타입 PoliteClient 축소 이식).

- 요청 간 최소 간격(직렬 호출 전제 — 어댑터가 심볼별로 순차 질의)
- 5xx/일시 오류는 지수 백오프(1→2→4초) 재시도
- 4xx/429 는 즉시 중단(StopFetch) — 키 오류·쿼터 초과를 재시도로 두드리지 않는다

stdlib(urllib)만 사용해 의존성 없이 단위테스트에서 import 된다.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request

RETRY_BACKOFF_SEC = [1, 2, 4]


class StopFetch(Exception):
    """4xx/429 — 이 소스에 대한 수집을 즉시 중단해야 한다."""


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

    def get(self, url: str, *, accept: str = "application/json") -> str:
        """GET 후 본문 문자열 반환. 4xx/429 는 StopFetch, 재시도 소진은 RuntimeError."""
        last_exc: Exception | None = None
        for backoff in [0, *RETRY_BACKOFF_SEC]:
            if backoff:
                self._sleep(backoff)
            self._respect_interval()
            req = urllib.request.Request(url, headers={"Accept": accept})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self._last_request_at = time.monotonic()
                    return resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                self._last_request_at = time.monotonic()
                if exc.code == 429 or 400 <= exc.code < 500:
                    raise StopFetch(f"HTTP {exc.code}: 수집 중단") from exc
                last_exc = exc  # 5xx → 재시도
            except (urllib.error.URLError, TimeoutError) as exc:
                self._last_request_at = time.monotonic()
                last_exc = exc
        raise RuntimeError(f"GET 재시도 소진: {last_exc}")
