"""외부 API 공유 호출 허용 — PostgreSQL `call_budget` 행에서 발신 슬롯을 예약한다(ALPHA-1087).

같은 KIS 앱키를 쓰는 프로세스들이 각자 `PoliteClient` 간격으로 돌면 합이 한도를 넘을 수 있다
(ALPHA-1087: 장중 합산 제시 유량 16.5~18.5/s). 켜면 `PoliteClient.request()` 의 **매 발신 시도**가 여기서
허용을 받는다 — 운반 계층 재시도(5xx·네트워크)와 어댑터 재시도(EGW00201)도 새 허용을 받는다.

시간 계약(ALPHA-1087). 저장소가 주는 wait 는 **DB 처리 시각부터** 슬롯까지의 초다. 호출자는 처리 시각을
자기 시계로 `[t_req, t_resp]`(허용 요청 발신~응답 수신) 안의 어딘가로만 안다. 그래서
  - 이르지 않게: `t_resp + wait` 이후에 발신하고,
  - 늦지 않게:   `t_req + wait + send_window` 까지만 발신한다.
왕복 `t_resp - t_req` 가 `rtt_max` 를 넘으면 두 조건을 믿을 수 없어 버린다. 버린 허용은 반환·재사용하지
않는다(이미 저장소 슬롯을 차지했으므로 예산 손실로 센다). 서로 다른 시계는 **차이**만 쓴다.

⚠️ 보장하지 않는 것: 이 함수가 돌아온 뒤 실제 소켓 쓰기까지의 지연. urllib 은 요청마다 새 TCP·TLS
연결을 열어 **핸드셰이크 뒤에** 요청 바이트를 보낸다. 벤더 도착 시각의 편차는 그만큼 남는다 —
예산(R)을 공식 한도보다 낮게 두는 이유다(ALPHA-1087 조건부 상한).

장애 정책: 저장소에 닿지 못하면 **발신하지 않는다**(fail-closed). 예산을 나누지 않은 로컬 폴백은 없다
(ALPHA-1087: 연결된 쪽이 전체 예산을, 끊긴 쪽이 로컬 몫을 써 합이 한도를 넘었다). 저장소 장애·기한 초과·
설정 오류는 `RuntimeError` 가 **아닌** 예외로 올린다 — 어댑터가 `RuntimeError` 를 종목 단위 실패로
격리하므로(kis_minute `_call`), 같은 계열로 올리면 저장소 장애가 '결손 종목이 있는 window 확정'으로 바뀐다.

psycopg 는 지연 import 한다(db.py 관례).
"""

from __future__ import annotations

import ipaddress
import logging
import select
import socket
import threading
import time

from ..config import CallBudgetConfig, DbConfig
from .http import StopFetch

logger = logging.getLogger(__name__)

# 호출 등급 — 작을수록 먼저. DB call_budget_class 의 정책(선예약 폭·하한 몫)과 짝이다.
CLASS_MONITOR = 0     # 분 가격 워커(현재 ETF·구성종목이 한 window 라 등급을 나눌 수 없다)
CLASS_TOPUP = 1       # 발화 보충(준비 원장 연결 뒤 사용 — 지금 호출자는 없다)
CLASS_LANE = 2        # 장중 레인: iNAV·업종지수·장중 수급
CLASS_BATCH = 3       # EOD 배치·과거일 백필


class CallBudgetError(StopFetch):
    """공유 허용 실패의 공통 부모 — **`StopFetch`(이 소스 수집 중단) 계열이고 RuntimeError 가 아니다.**

    KIS 어댑터들은 종목 루프에서 `except StopFetch: raise` 뒤에 `except Exception: 종목 격리` 를 둔다
    (kis_price·kis_nav·kis_investor·kis_etf_profile·kis_investor_estimate, iNAV 수집기). 허용 장애가 그냥
    Exception 이면 종목마다 격리돼 저장소 장애 한 번이 종목 수만큼 대기·부분 실패로 번진다(ALPHA-1087
    edge-review). StopFetch 계열이라 기존 "소스 전역 중단" 규약을 그대로 탄다.

    `status` 에 고정 코드 `CALL_BUDGET` 을 싣는다 — HTTP 상태가 없는 StopFetch 로 보이면 수집 로그가
    "공급자 요청 거부"로 오분류한다(failures.http_failure 가 이 값을 CALL_BUDGET_BLOCKED 로 분류).
    """

    code = "CALL_BUDGET_ERROR"

    def __init__(self, detail: str = ""):
        super().__init__(f"{self.code}: {detail}", status="CALL_BUDGET")


class CallBudgetUnavailable(CallBudgetError):
    """저장소에 닿지 못했다 — 발신하지 않았다(fail-closed)."""

    code = "CALL_BUDGET_UNAVAILABLE"


class CallBudgetDeadlineExceeded(CallBudgetError):
    """max_wait 안에 유효한 허용을 얻지 못했다 — 발신하지 않았다."""

    code = "CALL_BUDGET_DEADLINE"


class CallBudgetMisconfigured(CallBudgetError):
    """예산 행·등급 행이 없다 — 초기화 전에 켰다. 자동 생성하지 않는다."""

    code = "CALL_BUDGET_MISCONFIGURED"


class PgBudgetStore:
    """허용 판정 전용 커넥션 하나(autocommit). 업무 트랜잭션·커넥션과 공유하지 않는다.

    호출 1건 = `call_budget_acquire` 한 번 = 짧은 트랜잭션 하나(잠금→시각→판정→예약). 대기·HTTP 는
    트랜잭션 밖이다. 커넥션 오류는 버리고 다음 호출에 다시 연다.

    시간 상한(ALPHA-1087). 호출 1회(저장소 잠금 대기+이름 해석+연결·TLS+질의)는 `acquire`/`ensure_connected`
    진입부터 `min(timeout, statement_timeout + 0.5s)` 안에 끝난다. `timeout` 은 pace 의 남은 시간이다. 서버 상한
    (statement_timeout·lock_timeout)은 서버 실행만 막는다. psycopg 3 는 두 경계를 못 막는다 — `execute` 는 응답
    수신에 전체 기한이 없고(`Connection.wait` 가 기한 없이 소켓을 기다린다), `connect_timeout` 은 정수 초·최소
    2초다. 그래서 연결·질의 모두 libpq 비동기 API(`psycopg.pq`)로 하고 소켓을 기한까지만 기다린다. 기한을 넘기면
    **서버가 예약을 확정했을 수 있다** — 그래도 허용을 못 받았으니 발신하지 않고, 상태를 모르는 커넥션은
    버린다(재사용·반환 없음. 그 슬롯은 예산 손실로 센다).
    DNS: libpq 는 호스트 이름을 `connect_start` 안에서 **동기로** 푼다(libpq 18 `connectDBStart`, 실측 무응답
    DNS 20초). 그래서 이름은 여기서 기한까지만 기다려 풀고 주소를 `hostaddr` 로 넘긴다(`host` 는 그대로 —
    TLS SNI·인증서 이름 검사·비밀번호 조회가 쓴다). 조회는 취소할 수 없어 기한을 넘기면 스레드가 끝까지 돌지만,
    저장소당 **하나만** 돌고 다음 연결이 그 결과를 이어받는다(조회가 쌓이지 않는다).
    ⚠️ 기한 밖: 이 함수들이 돌아온 뒤 HTTP 발신까지(`SharedBudgetPacer` 가 슬롯까지 자는 시간은 max_wait 안이다).
    """

    def __init__(self, db: DbConfig, cfg: CallBudgetConfig):
        self.db, self.cfg = db, cfg
        self._pg = None                     # psycopg.pq.PGconn — 이 저장소 전용, autocommit(libpq 기본)
        self._lock = threading.Lock()
        self._lookup: _Lookup | None = None  # 진행 중이거나 결과를 아직 안 쓴 이름 해석(저장소당 최대 1개)

    def _begin(self, timeout: float | None) -> float:
        """이 호출의 단조 기한을 정하고 저장소 잠금을 그 기한까지만 기다린다(다른 스레드의 호출도 기한이 있다)."""
        cap = self.cfg.statement_timeout_ms / 1000 + _QUERY_MARGIN_SEC
        end = time.monotonic() + min(cap, cap if timeout is None else timeout)
        if not self._lock.acquire(timeout=max(0.0, end - time.monotonic())):
            raise CallBudgetUnavailable("StoreBusy")
        return end

    def _connect_if_needed(self, end: float) -> None:
        from psycopg import pq
        from psycopg.conninfo import make_conninfo

        if self._pg is not None and self._pg.status == pq.ConnStatus.OK:
            return
        self.close()
        addrs = self._resolve(end)
        timeouts = f"-c statement_timeout={self.cfg.statement_timeout_ms} -c lock_timeout={self.cfg.statement_timeout_ms}"
        try:
            # 주소마다 같은 host 를 짝지운다 — libpq 가 주소를 차례로 시도하는 종전 동작을 유지한다.
            pg = pq.PGconn.connect_start(make_conninfo(
                host=",".join([self.db.host] * len(addrs)), hostaddr=",".join(addrs), port=self.db.port,
                dbname=self.db.name, user=self.db.user,
                password=self.db.password, sslmode=self.db.sslmode, application_name="call-budget",
                options=timeouts).encode())
        except Exception as exc:  # noqa: BLE001 — 원문은 접속 정보를 담을 수 있다
            raise CallBudgetUnavailable(type(exc).__name__) from None
        try:
            state = pq.PollingStatus.WRITING
            while state != pq.PollingStatus.OK:
                if state == pq.PollingStatus.FAILED or pg.status == pq.ConnStatus.BAD:
                    # 원문에는 접속 정보가 섞일 수 있다 — 종류만 싣는다(ALPHA-1064 관례).
                    raise CallBudgetUnavailable("ConnectFailed")
                _wait_socket(pg.socket, end, select.POLLIN if state == pq.PollingStatus.READING else select.POLLOUT,
                             "ConnectTimeout")
                state = pg.connect_poll()
            pg.nonblocking = 1
        except CallBudgetUnavailable:
            pg.finish()
            raise
        except Exception as exc:  # noqa: BLE001 — 소켓·libpq 오류도 발신 금지로(원문은 접속 정보를 담을 수 있다)
            pg.finish()
            raise CallBudgetUnavailable(type(exc).__name__) from None
        self._pg = pg

    def _resolve(self, end: float) -> list[str]:
        """호스트 이름을 기한까지만 기다려 주소 목록으로 푼다. 숫자 주소는 그대로. 실패·기한 초과는 발신 금지."""
        try:
            return [str(ipaddress.ip_address(self.db.host))]
        except ValueError:
            pass
        if self._lookup is None:                    # 기한을 넘긴 조회가 있으면 새로 시작하지 않고 그걸 기다린다
            try:
                self._lookup = _Lookup(self.db.host, self.db.port)
            except RuntimeError:                    # 스레드를 못 띄웠다 — RuntimeError 로 새면 종목 단위 격리로 접힌다
                raise CallBudgetUnavailable("DnsThread") from None
        if not self._lookup.done.wait(timeout=max(0.0, end - time.monotonic())):
            raise CallBudgetUnavailable("DnsTimeout")
        lookup, self._lookup = self._lookup, None   # 결과는 한 번만 쓴다 — 다음 연결은 새로 푼다(주소 변경 반영)
        if not lookup.addrs:
            raise CallBudgetUnavailable("DnsFailed")
        return lookup.addrs

    def ensure_connected(self, timeout: float | None = None) -> None:
        """커넥션을 미리 연다 — 연결 수립(TCP·인증)이 허용 왕복 측정에 섞여 첫 허용이 폐기되지 않게."""
        end = self._begin(timeout)
        try:
            self._connect_if_needed(end)
        finally:
            self._lock.release()

    def acquire(self, budget_id: str, call_class: int, cost: int = 1,
                timeout: float | None = None) -> tuple[str, float, float]:
        """(outcome, wait_sec, lock_wait_sec). 저장소 오류·기한 초과는 CallBudgetUnavailable(발신 금지).
        `timeout` 은 pace 의 남은 시간이다. 상한을 넘긴 응답은 어차피 rtt_max 로 버려질 허용이다.
        """
        end = self._begin(timeout)
        try:
            self._connect_if_needed(end)
            try:
                return self._query(end, budget_id, call_class, cost)
            except CallBudgetUnavailable:
                self.close()                # 응답 불확실 — 상태를 모르는 커넥션은 재사용하지 않는다
                raise
            except Exception as exc:        # noqa: BLE001 — libpq·소켓 오류. 원문은 접속 정보를 담을 수 있다
                self.close()
                raise CallBudgetUnavailable(type(exc).__name__) from None
        finally:
            self._lock.release()

    def _query(self, end: float, budget_id: str, call_class: int, cost: int) -> tuple[str, float, float]:
        from psycopg import pq

        pg = self._pg
        if time.monotonic() >= end:             # 남은 시간이 없으면 보내지 않는다 — 받지 못할 예약이 슬롯만 차지한다
            raise CallBudgetUnavailable("ResponseTimeout")
        pg.send_query_params(_ACQUIRE_SQL, [budget_id.encode(), str(call_class).encode(), str(cost).encode()])
        while pg.flush():                   # 1 = 아직 다 못 보냄(비차단 커넥션)
            # libpq 계약: 읽기·쓰기 어느 쪽이든 기다리고, 읽을 게 오면 먼저 소비해야 서버 송신이 풀린다.
            if _wait_socket(pg.socket, end, select.POLLIN | select.POLLOUT) & select.POLLIN:
                pg.consume_input()
        results = []
        while True:
            while pg.is_busy():
                _wait_socket(pg.socket, end, select.POLLIN)
                pg.consume_input()
            r = pg.get_result()
            if r is None:
                break
            results.append(r)
        # 결과를 **전부** 본다 — 행을 준 뒤 암묵 커밋이 실패하면 오류 결과가 뒤따른다. 첫 결과만 믿으면
        # 롤백된 예약으로 발신한다(ALPHA-1087 edge-review).
        bad = next((r for r in results if r.status != pq.ExecStatus.TUPLES_OK), None)
        if bad is not None or len(results) != 1:
            state = bad.error_field(pq.DiagnosticField.SQLSTATE) if bad is not None else None
            raise CallBudgetUnavailable(f"ServerError:{(state or b'').decode()}")
        row = results[0]
        return row.get_value(0, 0).decode(), float(row.get_value(0, 1)), float(row.get_value(0, 2))

    def close(self) -> None:
        """전용 커넥션을 닫는다(다음 acquire 가 다시 연다)."""
        if self._pg is not None:
            try:
                self._pg.finish()           # 비차단 — 종료 메시지는 최선 노력, 소켓은 닫힌다
            except Exception:  # noqa: BLE001 — 닫기 실패는 다음 연결로 대체된다
                pass
            self._pg = None


class _Lookup:
    """`getaddrinfo` 한 번을 데몬 스레드에서 돌린다 — 취소할 수 없는 동기 호출을 기한 밖으로 빼는 그릇.
    데몬이라 프로세스 종료를 붙잡지 않는다."""

    def __init__(self, host: str, port: int):
        self.done, self.addrs = threading.Event(), []
        threading.Thread(target=self._run, args=(host, port), name="call-budget-dns", daemon=True).start()

    def _run(self, host: str, port: int) -> None:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            self.addrs = list(dict.fromkeys(info[4][0] for info in infos))   # 순서 유지·중복 제거
        except OSError:
            pass                                # 빈 목록 = 실패(원문은 호스트 이름을 담는다)
        finally:
            self.done.set()


_ACQUIRE_SQL = b"SELECT outcome, wait_sec, lock_wait_sec FROM call_budget_acquire($1, $2::smallint, $3::integer)"
_QUERY_MARGIN_SEC = 0.5     # 서버 실행 상한(statement_timeout) 위의 네트워크 여유


def _wait_socket(fd: int, end: float, event: int, what: str = "ResponseTimeout") -> int:
    """소켓이 준비될 때까지 기한까지만 기다리고 준비된 이벤트를 돌려준다. 질의 중 기한이면 응답 불확실이다
    (서버는 처리했을 수 있다)."""
    left = end - time.monotonic()
    if left <= 0:
        raise CallBudgetUnavailable(what)
    poller = select.poll()                  # select() 는 fd 1024 이상을 못 다룬다
    poller.register(fd, event | select.POLLERR | select.POLLHUP)
    ready = poller.poll(left * 1000)
    if not ready:
        raise CallBudgetUnavailable(what)
    return ready[0][1]


class _Stats:
    """호출자·등급별 카운터. 로그 필드는 고정 이름만(카디널리티·민감정보 없음)."""

    FIELDS = ("granted", "denied", "denied_higher_active", "denied_horizon", "paused", "discard_rtt", "discard_late",
              "store_errors", "deadline_exceeded", "outage_ms")

    def __init__(self):
        self.c = dict.fromkeys(self.FIELDS, 0)
        self.acq_rtt_ms: list[float] = []
        self.send_delay_ms: list[float] = []
        self.lock_wait_ms: list[float] = []

    def snapshot_and_reset(self):
        """현재 카운터·분위수를 돌려주고 비운다(주기 요약 로그용)."""
        def _pct(xs, q):
            xs = sorted(xs)
            return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2) if xs else None

        out = dict(self.c, acq_rtt_ms_p50=_pct(self.acq_rtt_ms, .5), acq_rtt_ms_p99=_pct(self.acq_rtt_ms, .99),
                   send_delay_ms_p99=_pct(self.send_delay_ms, .99), lock_wait_ms_p99=_pct(self.lock_wait_ms, .99))
        self.__init__()
        return out


class SharedBudgetPacer:
    """`PoliteClient` 가 발신 직전에 부르는 허용 대기. 돌아오면 지금 발신해도 된다."""

    def __init__(self, store, cfg: CallBudgetConfig, *, caller: str, call_class: int,
                 clock=time.monotonic, sleep=time.sleep, report_every_sec: float = 60.0):
        if call_class not in (CLASS_MONITOR, CLASS_TOPUP, CLASS_LANE, CLASS_BATCH):
            raise ValueError(f"알 수 없는 호출 등급: {call_class}")
        self.store, self.cfg = store, cfg
        self.caller, self.call_class = caller, call_class
        self._clock, self._sleep = clock, sleep
        self.stats = _Stats()
        self._report_every = report_every_sec
        self._last_report = clock()
        self._lock = threading.Lock()

    def pace(self, cost: int = 1) -> None:
        """유효한 허용을 얻고 발신 가능 구간에 들어올 때까지 기다린다. 돌아오면 지금 발신한다.

        거절은 재질의, 왕복 초과·늦은 허용은 폐기 후 재요청, 저장소 장애는 발신 없이 store_outage_max_sec
        까지 재연결 후 CallBudgetUnavailable, max_wait 초과는 CallBudgetDeadlineExceeded.
        """
        now = self._clock()
        deadline = now + self.cfg.max_wait_sec
        outage_since = None
        while True:
            self._maybe_report()                    # 폐기·거절만 이어지는 장애 중에도 요약을 낸다
            if self._clock() > deadline:
                self._count("deadline_exceeded")
                raise CallBudgetDeadlineExceeded(self.caller)
            try:
                # 저장소 호출도 남은 기한 안에서만 기다린다 — 응답 유실·잠금 대기가 max_wait 를 넘기지 않게
                if hasattr(self.store, "ensure_connected"):
                    self.store.ensure_connected(timeout=deadline - self._clock())   # 연결 수립은 왕복 측정 밖에서
                t_req = self._clock()
                outcome, wait, lock_wait = self.store.acquire(self.cfg.budget_id, self.call_class, cost,
                                                              timeout=deadline - t_req)
            except CallBudgetUnavailable:
                self._count("store_errors")
                outage_since = outage_since if outage_since is not None else self._clock()
                if self._clock() - outage_since >= self.cfg.store_outage_max_sec:
                    self._count("outage_ms", int((self._clock() - outage_since) * 1000))
                    raise
                self._sleep(min(0.2, max(0.0, deadline - self._clock())))   # 발신 없이 재연결만 시도
                continue
            t_resp = self._clock()
            if outage_since is not None:
                self._count("outage_ms", int((t_resp - outage_since) * 1000))
                logger.warning("call_budget 저장소 복구 caller=%s class=%d down_ms=%d",
                               self.caller, self.call_class, int((t_resp - outage_since) * 1000))
                outage_since = None
            if outcome in ("UNKNOWN_BUDGET", "UNKNOWN_CLASS"):
                raise CallBudgetMisconfigured(f"{outcome} budget={self.cfg.budget_id} class={self.call_class}")
            if outcome == "PAUSED":
                self._count("paused")               # 전환·롤백 중 — 발신하지 않고 기한까지만 기다린다
                if t_resp + wait > deadline:
                    self._count("deadline_exceeded")
                    raise CallBudgetDeadlineExceeded(self.caller)
                self._sleep(min(max(wait, 0.005), self.cfg.deny_poll_cap_sec, max(0.0, deadline - self._clock())))
                continue
            if outcome.startswith("DENIED"):
                self._count("denied")
                if outcome == "DENIED_HIGHER_ACTIVE":
                    self._count("denied_higher_active")
                elif outcome == "DENIED_HORIZON":
                    self._count("denied_horizon")
                if t_resp + wait > deadline:
                    self._count("deadline_exceeded")
                    raise CallBudgetDeadlineExceeded(self.caller)
                self._sleep(min(max(wait, 0.005), self.cfg.deny_poll_cap_sec, max(0.0, deadline - self._clock())))
                continue
            if outcome != "GRANTED":                # 모르는 판정은 허용이 아니다 — 코드·DB 함수 계약 불일치
                raise CallBudgetMisconfigured(f"알 수 없는 판정 {outcome!r} budget={self.cfg.budget_id}")
            rtt = t_resp - t_req
            self._observe(rtt, lock_wait)
            if rtt > self.cfg.rtt_max_sec:
                self._count("discard_rtt")          # 처리 시각을 좁힐 수 없다 — 반환·재사용 없이 버린다
                continue
            earliest = t_resp + wait
            latest = t_req + wait + self.cfg.send_window_sec
            if earliest > deadline:
                self._count("deadline_exceeded")    # 기한 뒤 슬롯 — 잠들지 않는다(허용은 반환·재사용하지 않음)
                raise CallBudgetDeadlineExceeded(self.caller)
            delay = earliest - self._clock()
            if delay > 0:
                self._sleep(delay)
            # 통계 잠금을 **시각 검사 앞에서** 잡는다 — 검사 뒤에 잠금을 기다리면 그 대기만큼 만료된 허용으로
            # 발신한다(edge-review 3라운드). 잠금은 재진입 불가라 안에서는 _count 대신 직접 센다.
            with self._lock:
                sent_at = self._clock()
                if sent_at > deadline:
                    self.stats.c["deadline_exceeded"] += 1   # 슬롯까지 자는 사이 기한을 넘겼다
                    raise CallBudgetDeadlineExceeded(self.caller)
                if sent_at > latest:
                    self.stats.c["discard_late"] += 1        # 대기 중 정지·지연 — 늦은 발신은 다음 슬롯과 뭉친다
                    continue
                self.stats.c["granted"] += 1
                self.stats.send_delay_ms.append((sent_at - earliest) * 1000)
            return                                  # 시각 검사 뒤에는 아무 작업도 하지 않는다(요약은 루프 시작에서)

    def _count(self, field, n=1):
        with self._lock:
            self.stats.c[field] += n

    def _observe(self, rtt, lock_wait):
        with self._lock:
            self.stats.acq_rtt_ms.append(rtt * 1000)
            self.stats.lock_wait_ms.append(lock_wait * 1000)

    def _maybe_report(self, force=False):
        now = self._clock()
        if not force and now - self._last_report < self._report_every:
            return
        with self._lock:
            snap = self.stats.snapshot_and_reset()
            self._last_report = now
        logger.info("call_budget.summary caller=%s class=%d %s", self.caller, self.call_class,
                    " ".join(f"{k}={v}" for k, v in snap.items()))

    def close(self):
        """남은 카운터를 요약 로그로 내보내고 저장소 커넥션을 닫는다."""
        self._maybe_report(force=True)
        close = getattr(self.store, "close", None)
        if close:
            close()


def make_pacer(settings, *, caller: str, call_class: int) -> SharedBudgetPacer | None:
    """설정이 켜져 있으면 pacer, 아니면 None(기존 PoliteClient 간격 경로)."""
    cfg: CallBudgetConfig | None = getattr(settings, "call_budget", None)  # 섹션이 없으면 비활성(부분 설정 대역 포함)
    if cfg is None or not cfg.enabled:
        return None
    if settings.db is None:
        raise CallBudgetMisconfigured("call_budget.enabled 인데 db 설정이 없다")
    return SharedBudgetPacer(PgBudgetStore(settings.db, cfg), cfg, caller=caller, call_class=call_class)


def kis_http_client(settings, *, min_interval: float, caller: str, call_class: int, timeout: float = 10.0):
    """KIS 앱키 예산을 쓰는 PoliteClient. 비활성이면 종전과 같은 로컬 간격 클라이언트다.

    활성이면 `min_interval` 을 쓰지 않는다 — 그 간격은 **계정 예산을 나눠 쓰려던** 값이라 공유 허용이
    대체한다. KIS REST 에 계정 한도와 별개인 엔드포인트별 간격은 공식 문서에서 찾지 못했다(ALPHA-1087).
    토큰 발급(분당 1회, EGW00133)은 별도 제한이라 kis_auth 의 대기·재시도가 계속 맡는다.
    """
    from .http import PoliteClient

    return PoliteClient(min_interval=min_interval, timeout=timeout,
                        pacer=make_pacer(settings, caller=caller, call_class=call_class))


# ---------- 운영 초기화(명시적) ----------
# (horizon_sec, floor_rate). 하한 몫은 상위 등급이 활성일 때 하위 등급이 받는 최소 몫이다.
# 등급 2 = 업종(45/분≈0.75/s) + iNAV(38/분≈0.63/s) + 장중 수급(2/s, 하루 5슬롯) + 여유 → 4.0/s.
# 등급 3 = EOD 배치·백필의 기아 방지 0.5/s. 로컬 실험 기반 초기값이다 — 운영 수요를 재고 정한다.
DEFAULT_CLASSES = {
    CLASS_MONITOR: (2.0, 0.0),
    CLASS_TOPUP: (0.5, 0.0),
    CLASS_LANE: (0.2, 4.0),
    CLASS_BATCH: (0.05, 0.5),
}


def init_budget(conn, budget_id: str, rate_per_sec: float, classes=None) -> bool:
    """예산 행을 **없을 때만** 만든다. 있으면 아무것도 바꾸지 않고 False(상태 보존 — 재배포·재시작에 초기화하지 않는다).
    속도 변경은 `set_rate` 로만 한다."""
    classes = classes or DEFAULT_CLASSES
    with conn.transaction():
        made = conn.execute(
            "INSERT INTO call_budget (budget_id, rate_per_sec) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING 1",
            (budget_id, rate_per_sec)).fetchone()
        for cls, (horizon, floor) in classes.items():
            conn.execute(
                "INSERT INTO call_budget_class (budget_id, call_class, horizon_sec, floor_rate) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING", (budget_id, cls, horizon, floor))
    return bool(made)


def set_paused(conn, budget_id: str, paused: bool) -> None:
    """일시정지를 켜거나 끈다. 켜면 다음 허용 판정부터 공유 쪽은 새 허용을 받지 못한다."""
    with conn.transaction():
        n = conn.execute("UPDATE call_budget SET paused = %s, updated_at = now() WHERE budget_id = %s",
                         (paused, budget_id)).rowcount
    if n != 1:
        raise CallBudgetMisconfigured(f"budget {budget_id} 없음")


# 소진 판정에 쓰는 발신 창 상한 — 워커마다 send_window_sec 설정이 달라도 이 값 이하다(CallBudgetConfig 검증 상한).
MAX_SEND_WINDOW_SEC = 1.0


def drain_remaining(conn, budget_id: str, send_window_sec: float = MAX_SEND_WINDOW_SEC) -> float:
    """일시정지 뒤 이미 발급된 예약이 모두 소진(발신 또는 폐기)되기까지 남은 초. 0 이하면 소진됐다.

    예약 슬롯은 모두 `next_slot_at` 이전이고, 호출자는 슬롯 + 발신 기한 안에만 발신한다 — 그 시각이
    DB 시계로 지나면 공유 쪽 **추가 발신**은 없다. 진행 중 HTTP 는 이미 발신된 요청이라 도착 수를 늘리지
    않지만, 그 요청의 재시도는 새 허용이 필요하므로 일시정지 동안 막힌다.
    """
    row = conn.execute("SELECT paused, next_slot_at - extract(epoch FROM clock_timestamp()) FROM call_budget "
                       "WHERE budget_id = %s", (budget_id,)).fetchone()
    if row is None:
        raise CallBudgetMisconfigured(f"budget {budget_id} 없음")
    if not row[0]:
        raise CallBudgetMisconfigured(f"budget {budget_id} 가 일시정지 상태가 아니다")
    return float(row[1]) + send_window_sec


def set_rate(conn, budget_id: str, rate_per_sec: float) -> None:
    """속도만 바꾼다. 예약 상태(next_slot_at)는 보존한다."""
    with conn.transaction():
        n = conn.execute("UPDATE call_budget SET rate_per_sec = %s, updated_at = now() WHERE budget_id = %s",
                         (rate_per_sec, budget_id)).rowcount
    if n != 1:
        raise CallBudgetMisconfigured(f"budget {budget_id} 없음")


if __name__ == "__main__":
    # python -m data_pipeline.sources.call_budget init <budget> <rate> | set-rate <budget> <rate>
    #                                            | pause <budget> | resume <budget> | status <budget>
    # pause 는 예약 소진(drained)을 DB 시계로 확인한 뒤에 끝난다 — 고정 대기로 대신하지 않는다(ALPHA-1087).
    import sys
    import time as _time

    from ..config import load_settings
    from ..db import connect

    if len(sys.argv) < 3 or (sys.argv[1] in ("init", "set-rate") and len(sys.argv) < 4):
        raise SystemExit("usage: init <budget> <rate> | set-rate <budget> <rate> | pause|resume|status <budget>")
    settings = load_settings()
    cmd, budget = sys.argv[1], sys.argv[2]
    with connect(settings.db) as c:
        c.autocommit = True
        if cmd == "init":
            print("created" if init_budget(c, budget, float(sys.argv[3])) else "exists (unchanged)")
        elif cmd == "set-rate":
            set_rate(c, budget, float(sys.argv[3]))
            print("rate updated")
        elif cmd == "pause":
            set_paused(c, budget, True)
            # 이 CLI 의 설정이 아니라 **허용 가능한 최대 발신 창**으로 판정한다 — 워커가 더 큰 창을 쓰면
            # CLI 기준으로는 소진인데 워커는 아직 유효한 예약으로 발신할 수 있다(edge-review).
            while (left := drain_remaining(c, budget)) > 0:
                _time.sleep(min(left, 1.0))
            print("paused; drained — 공유 쪽 추가 발신 없음(진행 중 요청의 재시도는 새 허용이 필요해 막힌다)")
        elif cmd == "resume":
            set_paused(c, budget, False)
            print("resumed")
        elif cmd == "status":
            row = c.execute("SELECT budget_id, rate_per_sec, paused, next_slot_at - extract(epoch FROM clock_timestamp()) "
                            "FROM call_budget WHERE budget_id = %s", (budget,)).fetchone()
            if row is None:
                raise SystemExit(f"budget {budget} 없음")
            print(row)
        else:
            raise SystemExit("init|set-rate|pause|resume|status")
