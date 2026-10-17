import asyncio
import signal

import pytest

from edge_portfolio import worker


def test_healthcheck_allows_ten_minute_analysis_but_detects_a_stalled_worker(monkeypatch):
    import ast
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    compose = (Path(__file__).parents[1] / 'deploy/compose.yml').read_text()
    command = next(line.split('test: ', 1)[1] for line in compose.splitlines() if 'portfolio-worker-ready' in line)
    script = ast.literal_eval(command)[-1]
    marker = SimpleNamespace(exists=lambda: True, stat=lambda: SimpleNamespace(st_mtime=0))
    monkeypatch.setitem(sys.modules, 'pathlib', SimpleNamespace(Path=lambda _: marker))
    monkeypatch.setitem(sys.modules, 'time', SimpleNamespace(time=lambda: 600))
    exec(script)
    monkeypatch.setitem(sys.modules, 'time', SimpleNamespace(time=lambda: 660))
    with pytest.raises(AssertionError):
        exec(script)


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
    async def work(*args):
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


async def test_shutdown_during_database_claim_returns_unstarted_job(monkeypatch, tmp_path):
    stopping = asyncio.Event()
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    job = {'id': 'new-job', 'started_at': 'claim-version'}
    def claim():
        stopping.set()  # SIGTERM while the database call was completing.
        return job
    released = []
    monkeypatch.setattr(worker.store, 'claim', claim)
    monkeypatch.setattr(worker.store, 'release_claim', lambda *args: released.append(args), raising=False)
    assert await worker.work_once(stopping) is False
    assert released == [('new-job', 'claim-version')]


def test_worker_only_marks_ready_after_db_initialization_and_cleans_up(monkeypatch, tmp_path):
    from edge_portfolio import newsletter_store
    monkeypatch.setattr(newsletter_store, 'claim_content', lambda: None)
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
