import asyncio
import signal
from threading import Event

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
    from edge_portfolio import dashboard_work, dashboard_quotes
    async def no_collection(stopping):
        while not stopping.is_set(): await asyncio.sleep(.01)
    monkeypatch.setattr(dashboard_quotes, 'collection_loop', no_collection)
    monkeypatch.setattr(newsletter_store, 'claim_content', lambda: None)
    async def dashboard_idle(*args):
        return False
    monkeypatch.setattr(dashboard_work, 'work_once', dashboard_idle)
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
    with pytest.raises(ExceptionGroup, match='TaskGroup') as caught:
        asyncio.run(worker.main())
    assert isinstance(caught.value.exceptions[0], RuntimeError)
    assert str(caught.value.exceptions[0]) == 'database unavailable'
    assert not marker.exists()


async def test_dashboard_collection_cannot_delay_already_queued_customer_analyses(monkeypatch, tmp_path):
    from edge_portfolio import dashboard_work, dashboard_quotes
    async def no_collection(stopping):
        while not stopping.is_set(): await asyncio.sleep(.01)
    monkeypatch.setattr(dashboard_quotes, 'collection_loop', no_collection)
    from edge_portfolio import newsletter_work
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *args: None)
    events = []
    analysis_finished = Event()
    async def analysis(stopping):
        events.append('analysis')
        if events.count('analysis') == 4:
            analysis_finished.set()
        await asyncio.sleep(0)
        return True
    async def newsletter(stopping):
        events.append('newsletter')
        return False
    async def dashboard(stopping, *, kind=None):
        if kind == 'backtest': return False
        events.append('dashboard')
        assert await asyncio.to_thread(analysis_finished.wait, 2)
        stopping.set()
        return True
    monkeypatch.setattr(worker, 'work_once', analysis)
    monkeypatch.setattr(newsletter_work, 'work_once', newsletter)
    monkeypatch.setattr(dashboard_work, 'work_once', dashboard)
    await worker.main()
    assert events.count('analysis') >= 4
    assert events.count('dashboard') == 1
    assert 'newsletter' not in events


async def test_saved_holdings_refresh_while_an_analysis_is_still_running(monkeypatch, tmp_path):
    from edge_portfolio import dashboard_work, dashboard_quotes
    async def no_collection(stopping):
        while not stopping.is_set(): await asyncio.sleep(.01)
    monkeypatch.setattr(dashboard_quotes, 'collection_loop', no_collection)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *args: None)
    refreshed = Event()
    async def analysis(stopping):
        try:
            assert await asyncio.to_thread(refreshed.wait, 2)
        finally:
            stopping.set()
        return True
    async def dashboard(stopping, *, kind=None):
        if kind == 'backtest': return False
        refreshed.set()
        return True
    monkeypatch.setattr(worker, 'work_once', analysis)
    monkeypatch.setattr(dashboard_work, 'work_once', dashboard)
    await worker.main()
    assert refreshed.is_set(), 'Saving a portfolio must not wait for the analysis queue to empty'


async def test_shutdown_drains_both_analysis_and_valuation_without_claiming_more(monkeypatch, tmp_path):
    from edge_portfolio import dashboard_work, dashboard_quotes
    async def no_collection(stopping):
        while not stopping.is_set(): await asyncio.sleep(.01)
    monkeypatch.setattr(dashboard_quotes, 'collection_loop', no_collection)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)
    handlers = {}
    monkeypatch.setattr(worker.signal, 'signal', lambda number, handler: handlers.update({number: handler}))
    started = Event()
    completed = []
    async def analysis(stopping):
        assert await asyncio.to_thread(started.wait, 2)
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        completed.append('analysis')
        return True
    async def dashboard(stopping, *, kind=None):
        if kind == 'backtest': return False
        assert not stopping.is_set()
        started.set()
        await asyncio.sleep(.01)
        completed.append('valuation')
        return True
    monkeypatch.setattr(worker, 'work_once', analysis)
    monkeypatch.setattr(dashboard_work, 'work_once', dashboard)
    await worker.main()
    assert completed == ['analysis', 'valuation']
    assert not worker.HEARTBEAT.exists()


async def test_dashboard_claim_failure_never_cancels_analysis_and_retries(monkeypatch, tmp_path, caplog):
    from edge_portfolio import dashboard_work, dashboard_quotes
    async def no_collection(stopping):
        while not stopping.is_set(): await asyncio.sleep(.01)
    monkeypatch.setattr(dashboard_quotes, 'collection_loop', no_collection)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *args: None)
    refreshed = Event()
    calls = 0
    async def analysis(stopping):
        assert await asyncio.to_thread(refreshed.wait, 10)
        stopping.set()
        return True
    async def dashboard(stopping, *, kind=None):
        if kind == 'backtest': return False
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('transient claim error')
        refreshed.set()
        return True
    monkeypatch.setattr(worker, 'work_once', analysis)
    monkeypatch.setattr(dashboard_work, 'work_once', dashboard)
    await worker.main()
    assert calls == 2
    assert 'dashboard loop failed' in caplog.text


async def test_synchronous_dashboard_database_wait_does_not_block_analysis(monkeypatch, tmp_path):
    import time
    from edge_portfolio import dashboard_work, dashboard_quotes
    async def no_collection(stopping):
        while not stopping.is_set(): await asyncio.sleep(.01)
    monkeypatch.setattr(dashboard_quotes, 'collection_loop', no_collection)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    monkeypatch.setattr(worker.store, 'initialize', lambda: None)
    monkeypatch.setattr(worker.signal, 'signal', lambda *args: None)
    started, finished = Event(), Event()
    async def dashboard(stopping, *, kind=None):
        if kind == 'backtest': return False
        started.set()
        time.sleep(.3)  # A synchronous psycopg row-lock wait.
        finished.set()
        return True
    async def analysis(stopping):
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(.01)
        assert not finished.is_set(), 'Analysis must resume during a blocking dashboard DB call'
        stopping.set()
        return True
    monkeypatch.setattr(worker, 'work_once', analysis)
    monkeypatch.setattr(dashboard_work, 'work_once', dashboard)
    await worker.main()
    assert finished.is_set(), 'Shutdown must still drain the background task'
