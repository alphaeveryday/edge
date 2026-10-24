import json
import asyncio
import threading

import pytest

from edge_portfolio import worker
from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput
from report_fixtures import final_payload
from test_agent import Evidence, optimization_call


@pytest.mark.parametrize('reason', ['null', 'customer_failed', 'unknown_reason'])
async def test_nullable_provider_metadata_cannot_discard_an_otherwise_valid_report(monkeypatch, reason):
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CUSTOMER_POLICY_CONFLICT'}
        def validate(self, final): return None
    phases = []
    async def model(payload):
        role = phases[-1]
        content = {**final_payload(), 'recommendation': None, 'withheld_reason': reason} if role == 'opinion' else final_payload()[role]
        if role == 'question': content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    if reason == 'unknown_reason':
        with pytest.raises(ValueError, match='withheld_reason'):
            await run_analysis(request, 'test', phases.append, model_call=model, evidence=Evidence())
        return
    result = await run_analysis(request, 'test', phases.append, model_call=model, evidence=Evidence())
    assert 'opinion' in result['sections']
    repairs = [event for event in result['diagnostics']['events'] if event['event'] == 'structured_output_normalized']
    assert len(repairs) == (1 if reason == 'null' else 0)


async def test_nested_timeout_preserves_progress_and_has_a_timeout_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key-must-not-leak')
    monkeypatch.setattr(worker.store, 'claim', lambda: {'id': 'test', 'kind': 'analysis',
        'input': {'holdings': [], 'planning': True, 'horizon': '10년 이상'}})
    progress, completed = [], []
    monkeypatch.setattr(worker.store, 'checkpoint', lambda job, result: progress.append(result), raising=False)
    monkeypatch.setattr(worker.store, 'finish', lambda job, **kwargs: completed.append(kwargs))
    async def fail(*args, **kwargs):
        kwargs['on_event']({'event': 'tool_started', 'tool': 'fetch_source', 'elapsed_seconds': 1})
        raise ExceptionGroup('private input', [ExceptionGroup('transport', [TimeoutError('private provider body')])])
    monkeypatch.setattr(worker, 'run_analysis', fail)
    assert await worker.work_once()
    assert completed[0]['failure'] == 'TIMED_OUT'
    diagnostics = completed[0]['result']['diagnostics']
    assert diagnostics['failure_codes'] == ['TimeoutError']
    assert diagnostics['events'][0]['tool'] == 'fetch_source'
    assert 'private' not in json.dumps(completed)
    assert 'test-key' not in json.dumps(completed)


async def test_slow_checkpoint_storage_cannot_hold_up_analysis_completion(monkeypatch, tmp_path):
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(worker, 'HEARTBEAT', tmp_path / 'ready')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(worker.store, 'claim', lambda: {'id': 'test', 'kind': 'analysis',
        'input': {'holdings': [], 'planning': True, 'horizon': '10년 이상'}})
    def checkpoint(*args):
        entered.set()
        release.wait(1)
        exited.set()
    monkeypatch.setattr(worker.store, 'checkpoint', checkpoint)
    monkeypatch.setattr(worker.store, 'finish', lambda *args, **kwargs: True)
    async def analyze(*args, **kwargs):
        kwargs['on_event']({'event': 'tool_started', 'tool': 'fetch_source'})
        while not entered.is_set():
            await asyncio.sleep(.001)
        return {'elapsed_seconds': 0}
    monkeypatch.setattr(worker, 'run_analysis', analyze)
    try:
        await asyncio.wait_for(worker.work_once(), 2)
        assert not exited.is_set(), 'Optional checkpoint I/O must not delay the completed analysis'
    finally:
        release.set()


async def test_malformed_tool_json_is_repairable_and_execution_records_are_safe(monkeypatch):
    phases, events, snapshots = [], [], []
    sent_bad = False
    async def model(payload):
        nonlocal sent_bad
        role = phases[-1]
        if role == 'portfolio' and not sent_bad:
            sent_bad = True
            return {'choices': [{'message': {'role': 'assistant', 'content': '', 'reasoning_content': 'private reasoning',
                'tool_calls': [{'id': 'bad-arguments', 'type': 'function', 'function': {'name': 'search_etfs', 'arguments': '{'}}]}}]}
        if role == 'portfolio' and (call := optimization_call(payload)):
            rejected = next(m for m in payload['messages'] if m.get('tool_call_id') == 'bad-arguments')
            assert json.loads(rejected['content'])['reason'] == 'INVALID_TOOL_ARGUMENT'
            return call
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content), 'reasoning_content': 'private reasoning'},
                             'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30, 'private': 'not allowed'}}
    result = await run_analysis(AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상'),
        'test-key', phases.append, model_call=model, evidence=Evidence(), on_event=events.append, on_checkpoint=snapshots.append)
    assert result['diagnostics']['termination_reason'] == 'COMPLETED'
    assert result['diagnostics']['limits']['exa_calls'] == 70
    assert result['diagnostics']['model_calls'] == len([event for event in events if event['event'] == 'model_started'])
    assert any(event['event'] == 'tool_finished' and event.get('code') == 'INVALID_TOOL_ARGUMENT' for event in events)
    assert snapshots[-1]['sections'].keys() == result['sections'].keys()
    assert 'private reasoning' not in json.dumps(events)
    assert 'not allowed' not in json.dumps(events)


async def test_failed_exa_call_is_included_in_attempt_count():
    phases, events = [], []
    class FailingEvidence(Evidence):
        calls = 0
        async def call(self, name, args):
            self.calls += 1
            raise TimeoutError('provider details must remain private')
    async def model(payload):
        if phases[-1] == 'question':
            return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps({**final_payload()['question'], 'decision': {}})}}]}
        return {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'timeout', 'type': 'function',
            'function': {'name': 'search_sources', 'arguments': '{"query":"ETF","objective":"evidence"}'}}]}}]}
    with pytest.raises(TimeoutError):
        await run_analysis(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), 'test-only', phases.append,
            model_call=model, evidence=FailingEvidence(), on_event=events.append)
    assert events[-1]['event'] == 'tool_failed'
    assert events[-1]['counters']['exa_calls'] == 1
    assert 'provider details' not in json.dumps(events)
