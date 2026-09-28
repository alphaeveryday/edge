"""Model runner boundaries independent of live model quality."""

import asyncio
from dataclasses import dataclass
import json
import time

import pytest
from jsonschema import ValidationError

from edge_analysis_v2.model_runner import run_model, make_server


SCHEMA = {'type': 'object', 'required': ['summary'], 'additionalProperties': False,
          'properties': {'summary': {'type': 'string'}}}


@dataclass
class ResultMessage:
    subtype: str = 'success'
    structured_output: object = None
    result: str = ''
    is_error: bool = False


def client_for(message):
    class Client:
        def __init__(self, *, options):
            assert options.tools == []
            assert options.permission_mode == 'dontAsk'
            assert options.env['DISABLE_AUTO_COMPACT'] == '0'
            assert options.strict_mcp_config
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, text):
            assert json.loads(text) == {'news': []}
        async def receive_response(self):
            if message:
                yield message
    return Client


def invoke(tmp_path, message):
    return asyncio.run(run_model(initial={'news': []}, prompt='system', schemas=[],
        call=lambda name, args: None, output_schema=SCHEMA, artifacts=tmp_path,
        key='test-secret', model='deepseek-flash', client_factory=client_for(message)))


def test_success_is_schema_checked_and_recorded_without_secret(tmp_path):
    result = invoke(tmp_path, ResultMessage(structured_output={'summary': 'test-secret'}))
    assert result == {'summary': '[redacted]'}
    assert 'test-secret' not in ''.join(p.read_text(encoding='utf-8') for p in tmp_path.iterdir())


@pytest.mark.parametrize('message', [None, ResultMessage(is_error=True),
    ResultMessage(structured_output={'wrong': 1}), ResultMessage(result='not json')])
def test_missing_failed_or_invalid_final_result_never_succeeds(tmp_path, message):
    with pytest.raises((ValueError, ValidationError)):
        invoke(tmp_path, message)


def test_mcp_returns_callback_output_without_recomputing(monkeypatch):
    from edge_analysis_v2 import model_runner
    monkeypatch.setattr(model_runner, 'create_sdk_mcp_server', lambda **kwargs: kwargs)
    expected = {'tool_run_id': 'stored', 'result': {'amount': 18}}
    called=[]
    def call(name, args):
        called.append((name,args))
        return expected
    schema={'name':'sum','description':'Sum finalized data','parameters':{'type':'object'}}
    server, allowed=make_server([{'function':schema}],call)
    result=asyncio.run(server['tools'][0].handler({'days':5}))
    assert json.loads(result['content'][0]['text']) == expected
    assert called == [('sum',{'days':5})]
    assert allowed == ['mcp__analysis__sum']


def test_cancelled_tool_finishes_audit_before_caller_observes_failure(monkeypatch):
    from edge_analysis_v2 import model_runner
    monkeypatch.setattr(model_runner, 'create_sdk_mcp_server', lambda **kwargs: kwargs)
    finished=[]
    def call(name,args):
        time.sleep(0.05)
        finished.append(True)
        return {'tool_run_id':'stored','result':{}}
    schema={'function':{'name':'slow','description':'test','parameters':{'type':'object'}}}
    async def check():
        server,_=make_server([schema],call)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await server['tools'][0].handler({})
        assert finished == [True]
    asyncio.run(check())
