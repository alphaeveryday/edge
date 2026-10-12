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
    path = Path(__file__).resolve().parents[5] / '.github/scripts/portfolio_diagnostics.py'
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
    report = json.loads(output.removeprefix('REPLAY '))
    assert report['status'] == ('not_found' if not exists else 'failed' if fail else 'succeeded')
