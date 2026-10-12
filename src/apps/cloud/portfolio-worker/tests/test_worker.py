import asyncio

import pytest

from edge_portfolio import worker


def test_worker_only_marks_ready_after_db_initialization_and_cleans_up(monkeypatch, tmp_path):
    marker = tmp_path / 'ready'
    marker.touch()  # A previous process must not make this startup appear ready.
    monkeypatch.setattr(worker, 'HEARTBEAT', marker)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    def initialize():
        assert not marker.exists()

    monkeypatch.setattr(worker.store, 'initialize', initialize)

    async def poll():
        assert marker.exists()
        return False

    async def sleep(_):
        assert marker.exists()
        raise asyncio.CancelledError

    monkeypatch.setattr(worker, 'work_once', poll)
    monkeypatch.setattr(worker.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.main())
    assert not marker.exists()


def test_worker_queue_failure_never_signals_ready(monkeypatch, tmp_path):
    marker = tmp_path / 'ready'
    monkeypatch.setattr(worker, 'HEARTBEAT', marker)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)

    async def poll():
        raise RuntimeError('database unavailable')

    monkeypatch.setattr(worker, 'work_once', poll)
    with pytest.raises(RuntimeError, match='database unavailable'):
        asyncio.run(worker.main())
    assert not marker.exists()
