import asyncio
import signal

import pytest

from edge_portfolio import worker


@pytest.mark.parametrize('stop_signal', [signal.SIGTERM, signal.SIGINT])
def test_shutdown_finishes_the_claimed_job_without_claiming_another(monkeypatch, tmp_path, stop_signal):
    marker = tmp_path / 'ready'
    monkeypatch.setattr(worker, 'HEARTBEAT', marker)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)
    installed = {}
    previous = object()
    monkeypatch.setattr(worker.signal, 'getsignal', lambda _: previous)
    def install(number, handler):
        old = installed.get(number, previous)
        installed[number] = handler
        return old
    monkeypatch.setattr(worker.signal, 'signal', install)
    events = []
    async def work():
        events.append('claimed')
        assert len(events) == 1, 'A draining worker must not claim another customer job'
        installed[stop_signal](stop_signal, None)
        await asyncio.sleep(0)
        events.append('saved')
        return True
    monkeypatch.setattr(worker, 'work_once', work)
    asyncio.run(worker.main())
    assert events == ['claimed', 'saved']
    assert not marker.exists()
    assert installed == {signal.SIGTERM: previous, signal.SIGINT: previous}


def test_worker_only_marks_ready_after_db_initialization_and_cleans_up(monkeypatch, tmp_path):
    marker = tmp_path / 'ready'
    marker.touch()  # A previous process must not make this startup appear ready.
    monkeypatch.setattr(worker, 'HEARTBEAT', marker)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    def initialize():
        assert not marker.exists()

    monkeypatch.setattr(worker.store, 'initialize', initialize)

    def claim():
        assert not marker.exists()
        return None

    async def sleep(_):
        assert marker.exists()
        raise asyncio.CancelledError

    monkeypatch.setattr(worker.store, 'claim', claim)
    monkeypatch.setattr(worker.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.main())
    assert not marker.exists()


def test_worker_queue_failure_never_signals_ready(monkeypatch, tmp_path):
    marker = tmp_path / 'ready'
    monkeypatch.setattr(worker, 'HEARTBEAT', marker)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)

    def claim():
        assert not marker.exists()
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(worker.store, 'claim', claim)
    with pytest.raises(RuntimeError, match='database unavailable'):
        asyncio.run(worker.main())
    assert not marker.exists()
