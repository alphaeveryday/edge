"""저부하 HTTP 클라이언트 (프로토타입 PoliteClient 축소 이식).

- 요청 간 최소 간격 = **평균 발신률 상한**. 클라이언트 1개를 워커 여럿이 공유해도
  전체 발신률이 1/min_interval 로 묶인다(thread-safe). 인접 간격은 보장 대상이 아니다
- 5xx/일시 오류는 지수 백오프(1→2→4초) 재시도
- 응답 없이 끊긴 연결은 같은 재시도 루프 안에서 **한도를 두고** 한 번 다시 보낸다. 한도 밖이면
  종전대로 그 예외를 그대로 올린다(`DISCONNECT_RETRY_*`)
- 4xx/429 는 즉시 중단(StopFetch) — 키 오류·쿼터 초과를 재시도로 두드리지 않는다
- `keep_alive=True` 면 본문 없는 요청(GET)은 호스트별 연결을 다시 쓴다(`PoliteClient._open`). 기본은
  종전대로 호출마다 새 연결이다

간격 강제·재시도·StopFetch 백본은 `request()` 한 곳에 있고, `get()` 은 그 위의
하위호환 래퍼다(GET+Accept). KR 벤더는 이 코어를 재사용한다 — KIS·BigKinds 는 커스텀
헤더·POST 본문이, OpenDART 는 바이너리(ZIP) 응답이 필요해 request() 인자로 표현한다.
단, 초당한도가 HTTP 429 가 아니라 응답 본문(예 KIS EGW00201)으로 오는 벤더의 재시도는
운반 계층이 본문 의미를 모르므로 각 어댑터가 처리한다(여긴 운반만).

stdlib(urllib)만 사용해 의존성 없이 단위테스트에서 import 된다.
"""

from __future__ import annotations

import http.client
import io
import select
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from ..failures import SafeFailureError

RETRY_BACKOFF_SEC = [1, 2, 4]

# 응답 없이 끊긴 연결(`RemoteDisconnected`·`ConnectionResetError`·`IncompleteRead`)의 재시도 한도.
# urllib 은 발신 단계 실패만 `URLError` 로 감싸고 응답 수신 단계의 끊김은 원형 그대로 올린다 — 그래서
# 아래 재시도를 통째로 빠져나가 호출자를 죽였다(분 가격은 종목 하나의 끊김이 window 전체 실패였다).
# 목적은 **드문 끊김**(KIS 분봉 실측 하루 9~37건)을 흡수하는 것뿐이다. 계속 끊기는 장애에서 기존 재시도
# (어댑터의 EGW00201 루프·window 재청구)와 곱해져 발신이 늘지 않게 세 겹으로 묶는다:
#   - 호출당 1회(새 루프가 아니라 기존 재시도 루프의 한 칸을 쓴다 — 호출당 발신 상한 4 는 그대로다)
#   - **재발신 시각**이 첫 발신 뒤 `DISCONNECT_RETRY_DEADLINE_SEC` 안일 때만. 끊긴 시점에 백오프를 더해
#     미리 보고(넘길 것이면 기다리지 않는다), 발신 간격·공유 허용 대기가 끝난 발신 직전에 다시 본다
#   - 클라이언트 전체로 **고정 구간**(`DISCONNECT_RETRY_BUDGET_WINDOW_SEC`)마다 `DISCONNECT_RETRY_BUDGET` 회까지.
#     ⚠️ "임의의 60초에 5회"를 보장하지 않는다 — 구간 경계에 걸치면 임의의 60초에 최대 2배(10회)까지 나간다.
#     ponytail: 고정 구간 카운터. 상한이 있다는 것이 목적이라 그대로 둔다. 엄밀한 이동 창이 필요해지면
#     재시도 시각 deque 로 바꾼다.
# 재발신도 다른 발신과 같은 길을 지난다 — 백오프 뒤 발신 간격(또는 공유 호출 허용)을 다시 받는다.
# 한도 밖이면 다시 보내지 않고 **그 예외를 그대로 올린다**(이 재시도가 없던 때와 같은 동작). 안전 실패
# (`NETWORK_RETRY_EXHAUSTED`)로 바꾸지 않는 이유: 분 가격은 그걸 종목 결손으로 접어 window 를 커밋하는데,
# 커밋된 window 는 자동 재청구되지 않는다(DUE·만료 CLAIMED 만 다시 집는다). 예외로 window 를 실패시켜야
# lease 만료 뒤 전 종목 재수집 경로가 남는다 — 끊김으로 생긴 결손을 영구화하지 않는다.
DISCONNECT_RETRY_DEADLINE_SEC = 10.0
DISCONNECT_RETRY_BUDGET = 5
DISCONNECT_RETRY_BUDGET_WINDOW_SEC = 60.0

# 연결 재사용 경로가 싣는 User-Agent — `urlopen` 이 붙이던 값 그대로다(재사용을 켜도 벤더가 보는 헤더는 같다).
_USER_AGENT = f"Python-urllib/{urllib.request.__version__}"


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


def _peer_closed(sock) -> bool:
    """보낸 요청이 없는데 읽을 것이 생겼는가 — 쉬는 동안 서버가 연결을 닫았다는 뜻이다."""
    poller = select.poll()  # select.select 는 fd 번호 1024 부터 ValueError 다
    poller.register(sock, select.POLLIN)
    return bool(poller.poll(0))


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

    def __init__(self, *, min_interval: float = 1.0, timeout: float = 10.0, pacer=None,
                 keep_alive: bool = False):
        self.min_interval = min_interval
        self.timeout = timeout
        # 연결 재사용(ALPHA-1153) — `_open` 도크스트링. 연결은 스레드마다 따로 둔다(http.client 연결은
        # 동시 요청을 받지 못한다).
        self.keep_alive = keep_alive
        self._conns = threading.local()
        # 끊긴 연결 재시도 예산(DISCONNECT_RETRY_BUDGET) — 현재 **고정 구간**의 시작 시각과 쓴 횟수.
        # 간격용 `_lock` 은 대기 동안 잡혀 있어 따로 둔다.
        self._disconnect_lock = threading.Lock()
        self._disconnect_window_from = 0.0
        self._disconnect_retries = 0
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

    def _may_retry_disconnect(self, resend_by: float, wait: float) -> bool:
        """끊긴 연결을 `wait` 초 뒤에 다시 보내도 되는가 — 그 시각이 기한(`resend_by`) 안이고
        클라이언트 예산이 남았을 때만.

        된다고 답하면 예산 한 칸을 쓴다. 한도의 근거는 `DISCONNECT_RETRY_*` 주석.
        """
        now = time.monotonic()
        if now + wait > resend_by:
            return False
        with self._disconnect_lock:
            if now - self._disconnect_window_from > DISCONNECT_RETRY_BUDGET_WINDOW_SEC:
                self._disconnect_window_from, self._disconnect_retries = now, 0
            if self._disconnect_retries >= DISCONNECT_RETRY_BUDGET:
                return False
            self._disconnect_retries += 1
            return True

    def _open(self, req: urllib.request.Request) -> bytes:
        """한 번 보내고 본문을 받는다. `keep_alive` 면 본문 없는 요청(GET)은 호스트별 연결을 다시 쓴다.

        `urlopen` 은 호출마다 연결을 새로 맺는다(`Connection: close`). 1분에 수백 종목을 한 호스트에 묻는
        분 가격 수집에서는 TCP·TLS 수립이 호출 시간의 절반을 넘었다(ALPHA-1153 — 장 마감 뒤 20건씩:
        새 연결 p50 92ms, 재사용 26ms).

        예외 모양은 `urlopen` 과 같다 — `request()` 의 재시도 분기가 그 모양에 걸려 있다. 발신 단계 실패는
        `URLError`, 2xx 밖 응답은 `HTTPError`, 응답 수신 중 끊김은 원형 그대로다.
        ⚠️ `urlopen` 과 다른 점: 리다이렉트를 따라가지 않고(3xx 는 `HTTPError` — 재시도 뒤 소진) 프록시
        환경변수를 읽지 않는다. 본문 있는 요청(POST)은 재사용하지 않는다 — 종전 경로 그대로다.
        """
        if not self.keep_alive or req.data is not None:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        conns = vars(self._conns).setdefault("conns", {})
        key = (req.type, req.host)  # 스킴까지 — 호스트만 보면 https 요청이 같은 호스트의 평문 연결에 실린다
        conn = conns.get(key)
        if conn is None:
            connect = http.client.HTTPSConnection if req.type == "https" else http.client.HTTPConnection
            conn = conns[key] = connect(req.host, timeout=self.timeout)
        elif conn.sock is not None and _peer_closed(conn.sock):
            # 쉬는 동안 서버가 닫았다 — 요청을 싣기 전에 새로 맺는다. 닫힌 연결에 실으면 응답 없이 끊긴
            # 것으로 보여 끊김 재시도 예산(DISCONNECT_RETRY_*)을 쓴다.
            conn.close()
        if conn.sock is None:
            self.stats.add(connects=1)  # 새로 맺는 횟수 — 재사용이 실제로 되는지 창 요약 로그에서 본다
        try:
            try:
                conn.request(req.get_method(), req.selector,
                             headers={"User-agent": _USER_AGENT, **req.headers})
            except OSError as exc:
                raise urllib.error.URLError(exc) from exc
            resp = conn.getresponse()
            failed = not 200 <= resp.status < 300
            try:
                body = resp.read()
            except Exception:
                if not failed:
                    raise
                # 오류 응답은 상태가 판정이다 — 본문을 못 읽어도 그 상태로 올린다(`urlopen` 은 헤더만 보고 올린다)
                body = b""
                conn.close()
        except BaseException:
            conn.close()  # 응답을 끝까지 받지 못한 연결은 다시 쓰지 않는다 — 다음 발신이 새로 맺는다
            raise
        if failed:
            raise urllib.error.HTTPError(req.full_url, resp.status, resp.reason, resp.headers, io.BytesIO(body))
        return body

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
        응답 없이 끊긴 연결은 한도 안에서 1회만 다시 보내고, 한도 밖이면 그 예외를 그대로 올린다.
        """
        first_sent_at = None
        disconnect_retried = False
        dropped = None  # 끊김 재시도를 앞둔 동안만 — (그 예외, 다시 보낼 수 있는 마지막 시각)
        backoffs = [0, *RETRY_BACKOFF_SEC]
        for slot, backoff in enumerate(backoffs):
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
            if dropped is not None and time.monotonic() > dropped[1]:
                raise dropped[0]  # 발신 간격·공유 허용 대기가 끊김 재시도 기한을 넘겼다
            dropped = None
            if first_sent_at is None:
                first_sent_at = time.monotonic()
            try:
                with self.stats.attempt():
                    body = self._open(req)
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
            except (ConnectionError, http.client.IncompleteRead) as exc:
                # 응답 수신 중 끊김 — 한도 안에서 1회만 재시도하고, 한도 밖이면 그대로 올린다
                # (DISCONNECT_RETRY_* 주석). 남은 칸이 없으면 다시 보낼 수 없으니 예산도 쓰지 않는다.
                upcoming = backoffs[slot + 1:slot + 2]
                deadline = first_sent_at + DISCONNECT_RETRY_DEADLINE_SEC
                if disconnect_retried or not upcoming or not self._may_retry_disconnect(deadline, upcoming[0]):
                    raise
                disconnect_retried = True
                dropped = (exc, deadline)
        # 원본 예외 문자열에는 URL·프록시 자격증명 등이 들어갈 수 있다. 호출자는 고정 코드로
        # 일시 장애를 분류하고 원문은 현재 traceback 밖으로 전달하지 않는다(ALPHA-1064).
        raise SafeFailureError("NETWORK_RETRY_EXHAUSTED")

    def get(self, url: str, *, accept: str = "application/json") -> str:
        """GET 후 본문 문자열 반환. 4xx/429 는 StopFetch, 재시도 소진은 SafeFailureError."""
        body = self.request("GET", url, headers={"Accept": accept}, decode=True)
        assert isinstance(body, str)  # decode=True 라 항상 str
        return body
