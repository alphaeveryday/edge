"""공유 호출 예산의 DB 계약을 실 PostgreSQL(Flyway 로 적용된 실제 마이그레이션)에서 고정한다(ALPHA-1087).

단위 테스트(test_call_budget)는 저장소를 가짜로 바꿔 페이서의 제어 흐름만 본다. 여기서 지키는 것은
`call_budget_acquire` 자체의 불변식과, 네트워크 경계에서 저장소가 **기한 안에 발신 없이** 끝나는 것이다:
- 동시 요청이 슬롯을 잃지 않는다 — 잃으면 합산 속도가 예산을 넘는다.
- 상위 등급이 활성이면 하위는 하한 몫만 — 없으면 분봉이 배치에 밀리거나 배치가 굶는다.
- 일시정지는 새 허용을 끊고, 소진 판정은 이미 발급된 예약을 센다 — 전환·롤백의 전제다.
- 재초기화·속도 변경이 예약 상태를 되돌리지 않는다 — 되돌리면 같은 슬롯을 두 번 준다.
- 응답만 잃으면 서버는 예약을 확정했어도 호출자는 기한 안에 실패하고 발신하지 않는다(잔여 finding 해소).
"""

import os
import socket
import threading
import time
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("E2E_PGHOST"), reason="실 PostgreSQL 필요")


def _db(port=None):
    from data_pipeline.config import DbConfig

    return DbConfig(host=os.environ["E2E_PGHOST"], port=port or int(os.environ["E2E_PGPORT"]),
                    name="edge", user="edge", password="edge", sslmode="disable")


def _conn():
    import psycopg

    db = _db()
    return psycopg.connect(host=db.host, port=db.port, dbname=db.name, user=db.user, password=db.password,
                           autocommit=True)


def _cfg(**kw):
    from data_pipeline.config import CallBudgetConfig

    return CallBudgetConfig(enabled=True, **kw)


@pytest.fixture
def budget():
    """테스트마다 새 예산 행(다른 테스트·재실행과 상태를 섞지 않는다)."""
    from data_pipeline.sources.call_budget import init_budget

    bid = f"t-{uuid.uuid4().hex[:10]}"
    with _conn() as c:
        assert init_budget(c, bid, 50.0)
    yield bid
    with _conn() as c:
        c.execute("DELETE FROM call_budget_class WHERE budget_id = %s", (bid,))
        c.execute("DELETE FROM call_budget WHERE budget_id = %s", (bid,))


def _next_slot(bid):
    with _conn() as c:
        return c.execute("SELECT next_slot_at FROM call_budget WHERE budget_id = %s", (bid,)).fetchone()[0]


def _store(**kw):
    from data_pipeline.sources.call_budget import PgBudgetStore

    return PgBudgetStore(_db(kw.pop("port", None)), _cfg(budget_id="kis", **kw))


def test_concurrent_callers_never_lose_a_slot(budget):
    """8개 '프로세스'(각자 커넥션)가 동시에 받아도 예약은 정확히 1/rate 씩 쌓인다 — 갱신 유실이 있으면 합이 모자란다.

    시작 슬롯을 미래에 고정한다: 허용이 속도보다 늦으면 슬롯이 현재 시각으로 건너뛰어(쉰 시간은 적립하지 않는다)
    유실을 가릴 수 있다. 전 구간이 그 시각 전에 끝났음을 확인한 뒤 정확한 증가량을 본다.
    """
    stores = [_store() for _ in range(8)]
    for s in stores:
        s.ensure_connected()
    with _conn() as c:
        start = c.execute("UPDATE call_budget SET rate_per_sec = 100, next_slot_at = extract(epoch FROM clock_timestamp()) + 0.5 "
                          "WHERE budget_id = %s RETURNING next_slot_at", (budget,)).fetchone()[0]
    outcomes, lock = [], threading.Lock()

    def worker(s):
        for _ in range(9):
            o = s.acquire(budget, 0)[0]
            with lock:
                outcomes.append(o)
    ts = [threading.Thread(target=worker, args=(s,)) for s in stores]
    [t.start() for t in ts]; [t.join() for t in ts]
    for s in stores:
        s.close()
    with _conn() as c:
        now, final = c.execute("SELECT extract(epoch FROM clock_timestamp()), next_slot_at FROM call_budget "
                               "WHERE budget_id = %s", (budget,)).fetchone()
    assert float(now) < start, "시작 슬롯 전에 끝나지 않았다 — 시각 건너뜀이 섞여 판정할 수 없다"
    assert outcomes == ["GRANTED"] * 72                     # 0.5 + 72/100 = 1.22s < 분 가격 선예약 폭 2.0s
    # epoch 초(≈1.8e9) 누적 덧셈 오차는 1e-6 대다. 슬롯 하나 유실은 0.01s 라 1e-4 로 확실히 걸린다.
    assert final == pytest.approx(start + 72 / 100.0, abs=1e-4)


def test_active_higher_class_leaves_lower_only_its_floor(budget):
    """분 가격(0)이 방금 받았으면 장중 레인(2)은 하한 몫 한 번, 발화 보충(1, 하한 0)은 없음. 창이 지나면 다시 받는다."""
    with _conn() as c:
        c.execute("UPDATE call_budget SET active_window_sec = 0.3 WHERE budget_id = %s", (budget,))
    s = _store()
    assert s.acquire(budget, 0)[0] == "GRANTED"
    assert s.acquire(budget, 2)[0] == "GRANTED"             # 하한 몫(4/s)
    assert s.acquire(budget, 2)[0] == "DENIED_HIGHER_ACTIVE"   # 하한 몫 소진 — 상위가 계속 우선
    assert s.acquire(budget, 1)[0] == "DENIED_HIGHER_ACTIVE"   # 하한 몫이 없는 등급
    time.sleep(0.35)
    assert s.acquire(budget, 1)[0] == "GRANTED"             # 상위가 쉬면 선예약 폭 안에서 받는다
    s.close()


def test_pause_stops_new_grants_and_drain_counts_issued_reservations(budget):
    from data_pipeline.sources.call_budget import MAX_SEND_WINDOW_SEC, drain_remaining, set_paused

    s = _store()
    for _ in range(20):                                      # 20/50 = 0.4s 앞까지 예약
        assert s.acquire(budget, 0)[0] == "GRANTED"
    with _conn() as c:
        set_paused(c, budget, True)
        before = _next_slot(budget)
        assert s.acquire(budget, 0)[0] == "PAUSED"
        assert _next_slot(budget) == before                  # 일시정지는 예약을 만들지 않는다
        left = drain_remaining(c, budget)
        # 이미 발급된 마지막 예약(≈0.4s 앞) + 최대 발신 창까지 기다려야 '추가 발신 없음'
        assert MAX_SEND_WINDOW_SEC < left <= MAX_SEND_WINDOW_SEC + 0.4 + 0.1
        set_paused(c, budget, False)
    assert s.acquire(budget, 0)[0] == "GRANTED"
    s.close()


def test_reinit_and_rate_change_preserve_reservation_state(budget):
    from data_pipeline.sources.call_budget import init_budget, set_rate

    s = _store()
    for _ in range(5):
        s.acquire(budget, 2)
    s.close()
    with _conn() as c:
        before = c.execute("SELECT b.next_slot_at, cl.floor_next_at, cl.last_grant_at FROM call_budget b "
                           "JOIN call_budget_class cl USING (budget_id) WHERE budget_id = %s AND call_class = 2",
                           (budget,)).fetchone()
        assert init_budget(c, budget, 1.0) is False           # 재배포·재시작의 init 은 아무것도 안 바꾼다
        set_rate(c, budget, 20.0)
        after = c.execute("SELECT b.next_slot_at, cl.floor_next_at, cl.last_grant_at, b.rate_per_sec FROM call_budget b "
                          "JOIN call_budget_class cl USING (budget_id) WHERE budget_id = %s AND call_class = 2",
                          (budget,)).fetchone()
    assert after[:3] == before and after[3] == 20.0


class _DropReplies:
    """클라이언트↔PG 중계. `drop` 이 켜지면 서버→클라이언트 바이트를 버린다(서버는 처리했는데 응답만 잃음)."""

    def __init__(self):
        self.drop = False
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            up = socket.create_connection((os.environ["E2E_PGHOST"], int(os.environ["E2E_PGPORT"])))
            threading.Thread(target=self._pipe, args=(c, up, False), daemon=True).start()
            threading.Thread(target=self._pipe, args=(up, c, True), daemon=True).start()

    def _pipe(self, src, dst, reply):
        try:
            while data := src.recv(65536):
                if not (reply and self.drop):
                    dst.sendall(data)
        except OSError:
            pass
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self):
        self.srv.close()


def test_lost_reply_fails_closed_within_the_deadline_and_recovers(budget):
    """응답 유실: 서버는 예약을 확정(next_slot_at 전진)했지만 호출자는 상한 안에 CallBudgetUnavailable.
    상태를 모르는 커넥션은 버리고, 장애가 걷히면 같은 저장소가 다시 연결해 받는다."""
    from data_pipeline.sources.call_budget import CallBudgetUnavailable

    proxy = _DropReplies()
    s = _store(port=proxy.port, statement_timeout_ms=200)   # 호출 상한 = 0.2 + 0.5 = 0.7s
    s.ensure_connected()
    before = _next_slot(budget)
    proxy.drop = True
    t = time.monotonic()
    with pytest.raises(CallBudgetUnavailable, match="ResponseTimeout"):
        s.acquire(budget, 0)
    assert time.monotonic() - t < 0.7 + 0.2
    assert _next_slot(budget) > before                        # 서버는 처리·확정했다 — 응답만 잃었다
    t = time.monotonic()
    with pytest.raises(CallBudgetUnavailable):                # 재연결도 같은 상한(인증 응답도 버려진다)
        s.acquire(budget, 0)
    assert time.monotonic() - t < 0.7 + 0.2
    proxy.drop = False
    assert s.acquire(budget, 0)[0] == "GRANTED"
    s.close(); proxy.close()


def test_pacer_sends_nothing_and_returns_by_max_wait_when_replies_are_lost(budget):
    from data_pipeline.sources.call_budget import CLASS_MONITOR, CallBudgetError, SharedBudgetPacer

    proxy = _DropReplies()
    cfg = _cfg(budget_id=budget, max_wait_sec=1.5, store_outage_max_sec=5, statement_timeout_ms=200)
    from data_pipeline.sources.call_budget import PgBudgetStore
    s = PgBudgetStore(_db(proxy.port), cfg)
    s.ensure_connected()
    proxy.drop = True
    p = SharedBudgetPacer(s, cfg, caller="e2e", call_class=CLASS_MONITOR)
    t = time.monotonic()
    with pytest.raises(CallBudgetError):                      # 돌아오면 곧 발신이다 — 예외만이 '발신 없음'
        p.pace()
    assert time.monotonic() - t < 1.5 + 0.2
    s.close(); proxy.close()


def test_long_row_lock_is_cut_by_the_server_timeout(budget):
    """예산 행을 오래 쥔 트랜잭션이 있어도 서버 상한(lock/statement timeout)으로 끊기고 예약은 안 생긴다."""
    import psycopg
    from data_pipeline.sources.call_budget import CallBudgetUnavailable

    db = _db()
    holder = psycopg.connect(host=db.host, port=db.port, dbname=db.name, user=db.user, password=db.password)
    holder.execute("SELECT 1 FROM call_budget WHERE budget_id = %s FOR UPDATE", (budget,))
    s = _store(statement_timeout_ms=200)
    before = _next_slot(budget)
    t = time.monotonic()
    with pytest.raises(CallBudgetUnavailable, match="ServerError"):
        s.acquire(budget, 0)
    assert time.monotonic() - t < 0.7 + 0.2
    holder.rollback(); holder.close()
    assert _next_slot(budget) == before
    s.close()
