"""삭제는 RDS 디스크를 잠시 먹는다(WAL). 여유가 모자라면 기다리거나 멈춰야 DB 가 가득 차지 않는다."""

from data_pipeline.ops.snapshot_retention import GIB, DiskGuard, run


def test_guard_waits_for_wal_to_drain_then_continues():
    values, slept = iter([2.0 * GIB, 2.0 * GIB, 3.0 * GIB]), []
    guard = DiskGuard(lambda: next(values), pace_bytes=2.5 * GIB, min_bytes=1.5 * GIB, sleep=slept.append)
    assert guard.check() is True and slept == [60, 60]


def test_guard_stops_below_the_floor_and_when_space_never_recovers():
    assert DiskGuard(lambda: 1.0 * GIB, pace_bytes=2.5 * GIB, min_bytes=1.5 * GIB, sleep=lambda s: None).check() is False
    assert DiskGuard(lambda: 2.0 * GIB, pace_bytes=2.5 * GIB, min_bytes=1.5 * GIB, sleep=lambda s: None, waits=3).check() is False


class FakeConn:
    def __init__(self, ids):
        self.ids, self.deleted, self.statements = ids, [], []

    def execute(self, query, params=None):
        self.statements.append(query)
        conn = self

        class Result:
            rowcount = len(params["ids"]) if params and "ids" in params else 0

            def fetchall(self):
                return [(i, 10) for i in conn.ids]
        if params and "ids" in params:
            self.deleted.extend(params["ids"])
        return Result()

    def transaction(self):
        import contextlib
        return contextlib.nullcontext()


def test_a_stopped_guard_leaves_the_rest_and_skips_vacuum():
    conn, answers = FakeConn([f"r{i}" for i in range(45)]), iter([True, False])
    guard = DiskGuard(lambda: None, pace_bytes=0, min_bytes=0)
    guard.check = lambda: next(answers)
    summary = run(conn, mode="run", age="7 days", guard=guard)
    assert summary["deleted"] == 20 and summary["stopped"] is True
    assert not any("VACUUM" in s for s in conn.statements)
