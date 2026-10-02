"""Analysis slots cap how many cloud analyses hold result-writer connections at once."""
import pytest

from edge_analysis_v2.cloud.worker import SlotUnavailable, acquire_slot


class Locks:
    """Stand-in for the lock connection: `busy` slot keys are held by other sessions."""

    def __init__(self, busy=()):
        self.busy, self.held, self.asked = set(busy), [], []

    def execute(self, statement, parameters):
        assert 'pg_try_advisory_lock' in statement
        key = parameters[0]
        self.asked.append(key)
        granted = key not in self.busy
        if granted:
            self.held.append(key)
        class Row:
            def fetchone(self):
                return (granted,)
        return Row()


def test_takes_a_free_slot_without_waiting():
    locks = Locks(busy={'cloud:slot:0'})
    slept = []
    assert acquire_slot(locks, 3, sleep=slept.append) == 1
    assert locks.held == ['cloud:slot:1'] and slept == []


def test_full_slots_make_the_task_wait_instead_of_starting():
    """A fourth analysis must not open more writer connections; it starts only after a slot frees."""
    locks = Locks(busy={'cloud:slot:0', 'cloud:slot:1', 'cloud:slot:2'})
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:
            locks.busy.discard('cloud:slot:2')  # another analysis finished
    assert acquire_slot(locks, 3, sleep=sleep, clock=lambda: 0) == 2
    assert len(waits) == 3 and locks.held == ['cloud:slot:2']


def test_never_holds_more_slots_than_configured():
    """The slot count is the cap: a task never probes beyond it, even when all are busy."""
    locks = Locks(busy={'cloud:slot:0', 'cloud:slot:1'})
    ticks = iter(range(100))
    with pytest.raises(SlotUnavailable):
        acquire_slot(locks, 2, wait_seconds=3, sleep=lambda seconds: None, clock=lambda: next(ticks))
    assert set(locks.asked) == {'cloud:slot:0', 'cloud:slot:1'} and locks.held == []


def test_gives_up_after_the_wait_so_the_workflow_timeout_is_not_the_first_signal():
    locks = Locks(busy={'cloud:slot:0'})
    now = [0]
    def sleep(seconds):
        now[0] += seconds
    with pytest.raises(SlotUnavailable):
        acquire_slot(locks, 1, wait_seconds=600, sleep=sleep, clock=lambda: now[0])
    assert 600 <= now[0] < 610
