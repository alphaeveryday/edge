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


def _worker_with_fakes(monkeypatch, events):
    from unittest.mock import MagicMock, Mock
    from edge_analysis_v2.cloud import worker
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = None
    def execute(statement, parameters=None):
        events.append(parameters[0])
        result = MagicMock()
        result.fetchone.return_value = (True,)
        return result
    connection.execute.side_effect = execute
    monkeypatch.setattr(worker, 'connect_results', lambda *a, **k: connection)
    monkeypatch.setattr(worker, 'connect_sources', lambda *a, **k: (events.append('sources'), connection)[1])
    for name in ['load_source', 'load_flow', 'load_prices', 'load_research_observations', 'DatabaseTools']:
        monkeypatch.setattr(worker, name, Mock())
    monkeypatch.setattr(worker, 'execute_request', lambda **k: events.append('analysis'))
    monkeypatch.setattr(worker, 'Publisher', lambda *a: Mock())
    monkeypatch.setattr(worker, 'export_records', lambda *a: None)
    return worker


REQUEST = {'kind': 'outlook', 'analysis_id': 'one', 'etf_code': '091160', 'analysis_at': '2026-10-02T10:00:00+09:00'}


def test_worker_takes_a_slot_before_it_reads_sources_or_runs_the_model(monkeypatch, tmp_path):
    """The slot must come before any work that opens more connections or spends model tokens."""
    from unittest.mock import Mock
    events = []
    worker = _worker_with_fakes(monkeypatch, events)
    worker.run(REQUEST, bucket='test', ca_path=tmp_path/'ca', folder=tmp_path/'output', key='test', model='test',
               session=Mock(), slots=2)
    assert events == ['cloud:outlook:091160', 'cloud:slot:0', 'sources', 'analysis']


def test_worker_without_a_slot_starts_nothing_and_reports_why(monkeypatch, tmp_path):
    from unittest.mock import Mock
    events = []
    worker = _worker_with_fakes(monkeypatch, events)
    monkeypatch.setattr(worker, 'acquire_slot', Mock(side_effect=SlotUnavailable('No analysis slot became free')))
    with pytest.raises(RuntimeError, match='SlotUnavailable'):
        worker.run(REQUEST, bucket='test', ca_path=tmp_path/'ca', folder=tmp_path/'output', key='test', model='test',
                   session=Mock(), slots=1)
    assert events == ['cloud:outlook:091160']
