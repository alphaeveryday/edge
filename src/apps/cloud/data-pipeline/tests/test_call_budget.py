"""공유 호출 허용(sources/call_budget.py) 계약 — DB 없이 가짜 저장소·가짜 시계로 고정한다.

DB 함수(call_budget_acquire) 자체와 모의 벤더 도착 시각 판정은 로컬 통합 검증(ALPHA-1087)이
맡는다. 여기는 CI 에서 도는 **시간 계약·장애 분류**의 회귀 방어다.
"""

import threading
import time
import urllib.error

import pytest

from data_pipeline.config import CallBudgetConfig
from data_pipeline.sources import call_budget as cb
from data_pipeline.sources.http import PoliteClient
from data_pipeline.sources.kis_minute import KisMinuteClient, KisUnitError


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, s)


class FakeStore:
    """acquire 호출마다 script 의 다음 항목을 낸다. 항목: ('GRANTED', wait) | ('DENIED', wait) | 'DOWN' | 함수."""

    def __init__(self, clock, script, rtt=0.001):
        self.clock, self.script, self.rtt, self.calls = clock, list(script), rtt, 0

    def acquire(self, budget_id, call_class, cost=1, timeout=None):
        self.calls += 1
        item = self.script.pop(0)
        if callable(item):
            item = item()
        if item == "DOWN":
            raise cb.CallBudgetUnavailable("OperationalError")
        rtt = item[2] if len(item) > 2 else self.rtt
        self.clock.t += rtt          # 허용 요청 왕복 — 처리 시각은 이 구간 어딘가
        return item[0], item[1], 0.0


def pacer(script, **cfg):
    clock = Clock()
    store = FakeStore(clock, script)
    p = cb.SharedBudgetPacer(store, CallBudgetConfig(enabled=True, **cfg), caller="t", call_class=cb.CLASS_LANE,
                             clock=clock, sleep=clock.sleep)
    return p, store, clock


def test_grant_waits_until_latest_possible_slot_then_returns():
    p, store, clock = pacer([("GRANTED", 0.3)])
    t0 = clock.t
    p.pace()
    # 이르지 않게: 응답 수신(t0+rtt) + wait 이후에만 돌아온다
    assert clock.t == pytest.approx(t0 + 0.001 + 0.3)
    assert p.stats.c["granted"] == 1 and store.calls == 1


def test_slow_response_grant_is_discarded_not_reused():
    """응답이 늦게 온 허용(왕복 > rtt_max)은 슬롯이 이미 지났을 수 있어 버리고 새로 받는다(ALPHA-1087 반례)."""
    p, store, _ = pacer([("GRANTED", 0.3, 1.2), ("GRANTED", 0.1)])
    p.pace()
    assert p.stats.c["discard_rtt"] == 1 and p.stats.c["granted"] == 1 and store.calls == 2


def test_grant_that_goes_stale_while_waiting_is_discarded():
    """슬롯까지 기다리는 사이 워커가 멈추면(여기선 sleep 이 늦게 깸) 기한을 넘긴 허용은 버린다."""
    clock = Clock()
    store = FakeStore(clock, [("GRANTED", 0.2), ("GRANTED", 0.0)])
    stalls = iter([1.0, 0.0])

    def late_sleep(s):
        clock.t += s + next(stalls, 0.0)
    p = cb.SharedBudgetPacer(store, CallBudgetConfig(enabled=True), caller="t", call_class=0, clock=clock, sleep=late_sleep)
    p.pace()
    assert p.stats.c["discard_late"] == 1 and p.stats.c["granted"] == 1


def test_denied_polls_again_with_capped_wait():
    p, store, clock = pacer([("DENIED", 5.0), ("GRANTED", 0.0)], deny_poll_cap_sec=0.5)
    t0 = clock.t
    p.pace()
    assert p.stats.c["denied"] == 1 and clock.t < t0 + 0.6


def test_store_outage_sends_nothing_and_raises_after_limit():
    p, store, _ = pacer(["DOWN"] * 200, store_outage_max_sec=1.0)
    with pytest.raises(cb.CallBudgetUnavailable):
        p.pace()
    assert p.stats.c["granted"] == 0 and p.stats.c["store_errors"] >= 5


def test_short_store_outage_recovers_without_sending_meanwhile():
    p, store, _ = pacer(["DOWN", "DOWN", ("GRANTED", 0.0)], store_outage_max_sec=5.0)
    p.pace()
    assert p.stats.c["store_errors"] == 2 and p.stats.c["granted"] == 1 and p.stats.c["outage_ms"] > 0


def test_deadline_bounds_waiting():
    p, _, _ = pacer([("DENIED", 100.0)] * 5, max_wait_sec=1.0)
    with pytest.raises(cb.CallBudgetDeadlineExceeded):
        p.pace()


def test_unknown_budget_is_loud():
    p, _, _ = pacer([("UNKNOWN_BUDGET", 0.0)])
    with pytest.raises(cb.CallBudgetMisconfigured):
        p.pace()


def test_batch_adapter_stops_the_source_instead_of_isolating_each_symbol():
    """배치 어댑터는 종목 루프에서 StopFetch 만 전파하고 나머지는 종목 격리한다. 허용 장애가 종목마다 격리되면
    저장소 장애 한 번이 종목 수 × 대기로 번지고 부분 실패로 끝난다 — 소스 전체 중단이어야 한다(edge-review)."""
    import test_kis_price as tp
    from data_pipeline.sources.kis_price import KisDailyPriceSource

    class DownPacer:
        calls = 0

        def pace(self, cost=1):
            DownPacer.calls += 1
            raise cb.CallBudgetUnavailable("OperationalError")

    class Client(tp.FakeClient):
        def request(self, method, url, **kw):
            DownPacer().pace()
            return super().request(method, url, **kw)
    src = tp._source({}, client=Client({}))
    src.auth._token = "tok"                          # 토큰은 있다 — 종목 요청 경로를 본다
    with pytest.raises(cb.CallBudgetUnavailable):
        list(src.fetch(["005930", "000660", "035420"]))
    assert DownPacer.calls == 1                      # 첫 종목에서 멈췄다(종목마다 다시 기다리지 않는다)


def test_budget_errors_are_not_runtime_errors():
    """어댑터가 RuntimeError 를 종목 단위 실패로 접는다(kis_minute _call). 저장소 장애가 그 계열이면
    '결손 종목이 있는 window 확정'이 된다 — 계열 밖이어야 window 시도 실패로 전파된다."""
    for exc in (cb.CallBudgetUnavailable, cb.CallBudgetDeadlineExceeded, cb.CallBudgetMisconfigured):
        assert not issubclass(exc, (RuntimeError, ValueError))


def test_kis_minute_propagates_budget_outage_instead_of_unit_failure(monkeypatch):
    class DownPacer:
        def pace(self, cost=1):
            raise cb.CallBudgetUnavailable("OperationalError")
    client = KisMinuteClient("k", "s", PoliteClient(min_interval=0, pacer=DownPacer()))
    client.auth._token = "tok"                       # 토큰은 이미 있다 — 시세 요청 경로의 분류를 본다
    monkeypatch.setattr(client, "_headers", lambda token=None: {})
    with pytest.raises(cb.CallBudgetUnavailable):
        client._call("https://example.invalid/x", "005930")
    # 대조: 운반 계층 재시도 소진(RuntimeError)은 여전히 종목 단위 실패다
    monkeypatch.setattr(client.client, "request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(KisUnitError):
        client._call("https://example.invalid/x", "005930")


def test_transport_retry_takes_a_new_grant(monkeypatch):
    """5xx 재시도도 같은 예산에서 새 허용을 받는다."""
    calls = []

    class CountingPacer:
        def pace(self, cost=1):
            calls.append(1)

    attempts = iter([urllib.error.HTTPError("u", 503, "x", {}, None), None])

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def fake_urlopen(req, timeout):
        e = next(attempts)
        if e:
            raise e
        return Resp()
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    c = PoliteClient(min_interval=0, pacer=CountingPacer())
    c._sleep = lambda s: None
    assert c.get("https://example.invalid") == "ok"
    assert len(calls) == 2


def test_disabled_by_default_keeps_local_interval_path():
    class S:
        call_budget = CallBudgetConfig()
        db = None
    c = cb.kis_http_client(S(), min_interval=0.5, caller="x", call_class=cb.CLASS_BATCH)
    assert c.pacer is None and c.min_interval == 0.5


def test_enabled_without_db_is_loud():
    class S:
        call_budget = CallBudgetConfig(enabled=True)
        db = None
    with pytest.raises(cb.CallBudgetMisconfigured):
        cb.make_pacer(S(), caller="x", call_class=cb.CLASS_BATCH)


def test_config_rejects_empty_send_window():
    with pytest.raises(ValueError):
        CallBudgetConfig(rtt_max_sec=0.05, send_window_sec=0.05)
    with pytest.raises(ValueError):
        CallBudgetConfig(budget_id="KIS app key")


def test_grant_beyond_deadline_does_not_sleep():
    """속도를 크게 낮춘 직후 먼 슬롯을 받으면 기한까지 잠들지 않고 바로 실패한다(롤백 절차의 전제)."""
    p, _, clock = pacer([("GRANTED", 500.0)], max_wait_sec=30.0)
    t0 = clock.t
    with pytest.raises(cb.CallBudgetDeadlineExceeded):
        p.pace()
    assert clock.t - t0 < 1.0


def test_paused_budget_sends_nothing_until_deadline():
    """일시정지 중에는 발신하지 않고, 기한을 넘기면 실패한다(전환·롤백 중 공유 쪽 발신 차단)."""
    p, store, _ = pacer([("PAUSED", 1.0)] * 100, max_wait_sec=3.0)
    with pytest.raises(cb.CallBudgetDeadlineExceeded):
        p.pace()
    assert p.stats.c["granted"] == 0 and p.stats.c["paused"] >= 3


def test_pause_then_resume_grants_again():
    p, _, _ = pacer([("PAUSED", 0.2), ("PAUSED", 0.2), ("GRANTED", 0.0)])
    p.pace()
    assert p.stats.c["paused"] == 2 and p.stats.c["granted"] == 1


def test_budget_error_is_classified_as_its_own_failure_not_provider_rejection():
    """StopFetch 계열이지만 공급자에 요청을 보내지 않았다 — 수집 로그가 '공급자 요청 거부'로 적으면 원인을 잘못 가리킨다."""
    from data_pipeline.failures import http_failure
    exc = cb.CallBudgetUnavailable("OperationalError")
    assert http_failure(exc.status)["code"] == "CALL_BUDGET_BLOCKED"
    assert "CALL_BUDGET_UNAVAILABLE" in str(exc)


def test_drain_uses_the_max_send_window_not_the_cli_setting():
    """일시정지 소진은 워커가 쓸 수 있는 가장 큰 발신 창을 기준으로 판정해야 한다."""
    assert cb.drain_remaining.__defaults__ == (cb.MAX_SEND_WINDOW_SEC,)
    assert CallBudgetConfig.model_fields["send_window_sec"].metadata[-1].le == cb.MAX_SEND_WINDOW_SEC


def test_contended_stats_lock_cannot_turn_a_late_grant_into_a_send():
    """시각 검사 뒤에 통계 잠금을 기다리면 그 대기만큼 만료된 허용으로 발신한다 — 잠금은 검사 앞에서 잡는다."""
    import threading as _t
    clock = Clock()
    store = FakeStore(clock, [("GRANTED", 0.0), ("GRANTED", 0.0)])
    p = cb.SharedBudgetPacer(store, CallBudgetConfig(enabled=True), caller="t", call_class=0, clock=clock, sleep=clock.sleep)
    real_lock = p._lock

    class SlowLock:
        """다른 스레드가 잠금을 오래 쥔 상황 — 획득 순간 시계가 0.2초 흐른다."""
        def __enter__(self):
            real_lock.acquire()
            clock.t += 0.2
        def __exit__(self, *a):
            real_lock.release()
    p._lock = SlowLock()
    p._observe = lambda *a: None                     # 왕복 기록의 잠금은 빼고 **최종 발신 판정의 잠금**만 겨냥한다
    # 첫 허용은 잠금 대기로 늦어져 버려지고(발신 없음), 두 번째도 같은 이유로 버려진다 → 스텁 소진
    with pytest.raises(IndexError):
        p.pace()
    assert p.stats.c["granted"] == 0 and p.stats.c["discard_late"] == 2


class _FakeResult:
    def __init__(self, status, row=None, sqlstate=None):
        self.status, self.row, self.sqlstate = status, row, sqlstate

    def get_value(self, r, c):
        return self.row[c]

    def error_field(self, _field):
        return self.sqlstate


class _FakePg:
    """libpq 비차단 커넥션 대역 — 응답이 이미 도착해 있고 get_result 가 script 를 낸다."""

    socket = 0

    def __init__(self, results):
        from psycopg import pq

        self.results, self.status = list(results), pq.ConnStatus.OK

    def finish(self):
        pass

    def send_query_params(self, *a):
        pass

    def flush(self):
        return 0

    def is_busy(self):
        return False

    def get_result(self):
        return self.results.pop(0) if self.results else None


def test_error_after_the_row_is_not_a_grant():
    """행(GRANTED)을 받은 뒤 커밋 단계 오류가 오면 예약은 롤백됐다 — 허용으로 믿으면 예산 밖 발신이다."""
    from psycopg import pq

    store = cb.PgBudgetStore(None, CallBudgetConfig(enabled=True))
    store._pg = _FakePg([_FakeResult(pq.ExecStatus.TUPLES_OK, (b"GRANTED", b"0", b"0")),
                         _FakeResult(pq.ExecStatus.FATAL_ERROR, sqlstate=b"40001")])
    with pytest.raises(cb.CallBudgetUnavailable, match="40001"):
        store.acquire("kis", 0, timeout=1.0)
    assert store._pg is None                                # 상태를 모르는 커넥션은 버린다
    store._pg = _FakePg([_FakeResult(pq.ExecStatus.TUPLES_OK, (b"GRANTED", b"0.01", b"0"))])
    assert store.acquire("kis", 0, timeout=1.0) == ("GRANTED", 0.01, 0.0)


def test_socket_error_while_connecting_is_a_budget_outage(monkeypatch):
    """연결 대기 중 소켓 오류(OSError)도 CallBudgetUnavailable 여야 한다 — 원형으로 새면 pace 의 재연결·
    CALL_BUDGET_BLOCKED 분류를 건너뛰고, 반쯤 연 libpq 커넥션이 남는다."""
    from psycopg import pq

    finished = []

    class Half:
        socket, status = 0, pq.ConnStatus.STARTED

        def finish(self):
            finished.append(True)

    def boom(*a, **k):
        raise OSError("poll failed")
    class FakePGconn:
        @staticmethod
        def connect_start(conninfo):
            return Half()
    monkeypatch.setattr(pq, "PGconn", FakePGconn)
    monkeypatch.setattr(cb, "_wait_socket", boom)
    from data_pipeline.config import DbConfig
    store = cb.PgBudgetStore(DbConfig(host="127.0.0.1", port=1, name="d", user="u", password="p"),
                             CallBudgetConfig(enabled=True))
    with pytest.raises(cb.CallBudgetUnavailable, match="OSError"):
        store.acquire("kis", 0, timeout=1.0)
    assert finished == [True] and store._pg is None
    assert store._lock.acquire(blocking=False)                 # 잠금도 풀렸다


def test_slow_dns_stays_inside_the_call_deadline_and_does_not_pile_up(monkeypatch):
    """libpq 는 호스트 이름을 connect_start 안에서 동기로 푼다(실측: 무응답 DNS 20초) — 그대로 두면 저장소 호출이
    기한을 넘겨 pace 의 max_wait·장애 판정이 무력해진다. 이름 해석도 기한 안에서 끝나야 하고, 기한을 넘긴 조회가
    호출마다 새 스레드로 쌓이면 안 된다(취소할 수 없는 조회는 하나만 돌고 다음 호출이 이어받는다)."""
    import socket as _socket

    from psycopg import pq

    from data_pipeline.config import DbConfig

    release, calls, seen = threading.Event(), [], []

    def slow_getaddrinfo(host, port, *a, **k):
        calls.append(host)
        release.wait(5)
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("10.0.0.7", port)),
                (_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("10.0.0.7", port))]

    class Stop(Exception):
        pass

    class FakePGconn:
        @staticmethod
        def connect_start(conninfo):
            seen.append(conninfo.decode())
            raise Stop                           # 주소가 libpq 로 넘어가는지만 본다

    monkeypatch.setattr(cb.socket, "getaddrinfo", slow_getaddrinfo)
    monkeypatch.setattr(pq, "PGconn", FakePGconn)
    store = cb.PgBudgetStore(DbConfig(host="db.example", port=5432, name="d", user="u", password="p"),
                             CallBudgetConfig(enabled=True))
    for _ in range(3):
        t = time.monotonic()
        with pytest.raises(cb.CallBudgetUnavailable, match="DnsTimeout"):
            store.acquire("kis", 0, timeout=0.1)
        assert time.monotonic() - t < 0.5
    assert calls == ["db.example"]               # 기한을 넘긴 조회를 기다릴 뿐 새로 쌓지 않는다
    release.set()
    with pytest.raises(cb.CallBudgetUnavailable, match="Stop"):
        store.acquire("kis", 0, timeout=1.0)     # 끝난 조회를 이어받아 연결을 시작한다
    assert calls == ["db.example"]
    assert "hostaddr=10.0.0.7" in seen[0] and "host=db.example" in seen[0]   # 이름은 TLS·인증 검사용으로 남는다


def test_failed_dns_is_a_budget_outage_and_the_next_connect_resolves_again(monkeypatch):
    """이름 해석 실패는 발신 금지(CallBudgetUnavailable)다. 실패 결과를 붙잡아 두면 복구 뒤에도 못 붙는다."""
    from data_pipeline.config import DbConfig

    calls = []

    def fail(host, *a, **k):
        calls.append(host)
        raise OSError("Name or service not known")

    monkeypatch.setattr(cb.socket, "getaddrinfo", fail)
    store = cb.PgBudgetStore(DbConfig(host="db.example", port=5432, name="d", user="u", password="p"),
                             CallBudgetConfig(enabled=True))
    for _ in range(2):
        with pytest.raises(cb.CallBudgetUnavailable, match="DnsFailed"):
            store.acquire("kis", 0, timeout=1.0)
    assert calls == ["db.example", "db.example"]
