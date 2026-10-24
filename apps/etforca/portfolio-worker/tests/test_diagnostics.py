import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def diagnostics(monkeypatch):
    monkeypatch.setitem(sys.modules, 'boto3', SimpleNamespace())
    path = Path(__file__).resolve().parents[4] / '.github/scripts/portfolio_diagnostics.py'
    spec = importlib.util.spec_from_file_location('portfolio_diagnostics', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replay_accepts_only_job_ids_and_preserves_read_only_default(diagnostics):
    assert diagnostics.host_script('') == diagnostics.HOST_SCRIPT
    with pytest.raises(ValueError, match='Invalid replay job ID'):
        diagnostics.host_script("'; private-input")
    compile(diagnostics.host_script('a' * 32), 'host_script', 'exec')


@pytest.mark.parametrize('exists,fail', [(False, False), (True, False), (True, True)])
def test_replay_reads_only_failed_analysis_without_mutation_or_private_output(diagnostics, monkeypatch, capsys, exists, fail):
    from edge_portfolio import agent, store
    queries = []
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, args):
            queries.append((query, args))
            return self
        def fetchone(self):
            return {'input': {'holdings': [{'name': 'cash', 'kind': 'cash', 'amount_krw': 1000000}], 'horizon': '10년 이상', 'question': 'private-user-question'}} if exists else None
    monkeypatch.setattr(store, 'connect', DB)
    call = AsyncMock(side_effect=ValueError('private-error-text') if fail else None,
                     return_value={'elapsed_seconds': 12, 'optimization': {'status': 'available', 'candidates': [1]}, 'private': 'private-result'})
    monkeypatch.setattr(agent, 'run_analysis', call)
    deadlines = []
    original_timeout = asyncio.timeout
    def timeout(seconds):
        deadlines.append(seconds)
        return original_timeout(seconds)
    monkeypatch.setattr(asyncio, 'timeout', timeout)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'private-key')
    monkeypatch.setattr(sys, 'argv', ['-c', 'a' * 32])
    exec(compile(diagnostics.REPLAY_SCRIPT, 'replay', 'exec'), {})
    output = capsys.readouterr().out
    assert 'private' not in output
    assert len(queries) == 1
    assert queries[0][0].startswith('SELECT input')
    assert "state='failed' AND kind='analysis'" in queries[0][0]
    assert queries[0][1] == ('a' * 32,)
    assert call.await_count == int(exists)
    assert deadlines == ([300] if exists else []), 'Manual replay must stay inside its existing SSM watchdog'
    report = json.loads(output.removeprefix('REPLAY '))
    assert report['status'] == ('not_found' if not exists else 'failed' if fail else 'succeeded')


@pytest.mark.parametrize('memory,running,stdout,expected', [
    (4096, '', 'PRIVATE log line 1234567\nREVIEW {"rows": 3}\n', {'rows': 3}),
    (4096, '', 'PRIVATE traceback only\n', {'review': 'unavailable', 'returncode': 1}),
    (4096, 'abc123\n', '', {'review': 'skipped', 'reason': 'ALREADY_RUNNING'}),
    (300, '', '', {'review': 'skipped', 'reason': 'LOW_HOST_MEMORY', 'available_mb': 300}),
])
def test_past_review_runs_in_a_disposable_container_and_prints_only_its_summary(diagnostics, monkeypatch, capsys, memory, running, stdout, expected):
    import io
    import subprocess
    calls = []
    def run(command, **options):
        calls.append(command)
        if command[:2] == ['docker', 'ps']:
            return SimpleNamespace(stdout=running, stderr='', returncode=0)
        return SimpleNamespace(stdout=stdout, stderr='PRIVATE stderr', returncode=1 if 'REVIEW' not in stdout else 0)
    monkeypatch.setattr(subprocess, 'run', run)
    script = diagnostics.review_script(90, 2)
    exec(compile(script, 'review', 'exec'), {'open': lambda path: io.StringIO(f'MemTotal: 1 kB\nMemAvailable: {memory * 1024} kB\n')})
    output = capsys.readouterr().out
    assert 'PRIVATE' not in output
    assert json.loads(output.removeprefix('REVIEW ')) == expected
    review = [command for command in calls if 'edge_portfolio.past_review' in command]
    if memory < 600 or running:
        assert review == [], 'A busy host or a review already in progress is never doubled up'
        assert not [command for command in calls if 'rm' in command], "Another run's container is never removed"
        return
    # 분석 중인 worker 컨테이너(exec)가 아니라 제 메모리 상한을 가진 1회용 컨테이너(run --rm)에서 돈다
    command = review[0]
    name = command[command.index('--name') + 1]
    assert command[command.index('run'):command.index('worker')] == ['run', '--rm', '--no-deps', '-T', '--name', name]
    assert name.startswith('portfolio-past-review-') and command[-2:] == ['90', '2']
    assert calls[-1] == ['docker', 'rm', '-f', name], "Only this run's container is removed, even after a timeout"


def test_review_options_are_bounded_and_existing_modes_are_unchanged(diagnostics):
    for days, samples in ((-1, 0), (90, 6), (4000, 0)):
        with pytest.raises(ValueError, match='Invalid review options'):
            diagnostics.review_script(days, samples)
    assert diagnostics.host_script('') == diagnostics.HOST_SCRIPT
    assert 'past_review' not in diagnostics.host_script('a' * 32)
