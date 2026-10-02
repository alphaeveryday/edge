"""Model runner boundaries independent of live model quality."""

import asyncio
from dataclasses import dataclass
import json
import time

import pytest
from jsonschema import ValidationError

from pathlib import Path

from edge_analysis_v2.agent.runner import run_model, make_server, load_prompt


SCHEMA = {'type': 'object', 'required': ['summary'], 'additionalProperties': False,
          'properties': {'summary': {'type': 'string'}}}


@pytest.mark.parametrize('kind', ['movement', 'outlook'])
def test_canonical_prompt_is_sent_and_recorded_without_external_documents(tmp_path, kind):
    """Keep the reviewed instructions identical to the model and audit inputs."""
    import edge_analysis_v2.agent.runner as runner

    prompt_path = Path(runner.__file__).parents[1] / 'prompts' / f'{kind}.yaml'
    prompt = load_prompt(prompt_path)
    captured = []
    base = client_for(ResultMessage(structured_output={'summary': 'ok'}))

    class Client(base):
        def __init__(self, *, options):
            super().__init__(options=options)
            captured.append(options.system_prompt)

    result = asyncio.run(run_model(initial={'news': []}, prompt=prompt, schemas=[],
        call=lambda name, args: None, output_schema=SCHEMA, artifacts=tmp_path,
        key='test-secret', model='deepseek-flash', client_factory=Client, kind=kind))

    assert result == {'summary': 'ok'}
    assert len(captured) == 1 and captured[0].startswith(prompt)
    assert 'analysis:hypothesis-analysis-workflow' in captured[0]
    assert (tmp_path / 'system_prompt.txt').read_text(encoding='utf-8') == captured[0]
    assert (tmp_path/'AGENTS.md').read_text(encoding='utf-8') in captured[0]


def test_valid_json_without_skill_calls_is_accepted_and_workspace_removed(tmp_path):
    workspaces = []
    base = client_for(ResultMessage(structured_output={'summary':'ok'}))
    class Client(base):
        async def __aenter__(self):
            workspaces.append(Path(self.options.cwd))
            return self
    result = asyncio.run(run_model(initial={'news':[]}, prompt='system', schemas=[],
        call=lambda name,args:None, output_schema=SCHEMA, artifacts=tmp_path,
        key='test-secret', model='deepseek-flash', client_factory=Client))
    assert result == {'summary':'ok'}
    assert (tmp_path/'response.json').exists()
    assert workspaces and not workspaces[0].exists()


def test_research_is_bounded_by_elapsed_time_not_a_fixed_turn_count(tmp_path):
    base = client_for(None)
    class Client(base):
        async def receive_response(self):
            assert self.options.max_turns is None
            await asyncio.sleep(10)
            yield ResultMessage(structured_output={'summary':'too late'})
    with pytest.raises(TimeoutError):
        asyncio.run(run_model(initial={'news':[]}, prompt='system', schemas=[],
            call=lambda name,args:None, output_schema=SCHEMA, artifacts=tmp_path,
            key='test-secret', model='deepseek-flash', client_factory=Client, timeout_seconds=1))
    assert not (tmp_path/'response.json').exists()


@dataclass
class ResultMessage:
    subtype: str = 'success'
    structured_output: object = None
    result: str = ''
    is_error: bool = False


def client_for(message):
    class Client:
        def __init__(self, *, options):
            self.options = options
            assert options.tools == ['Skill', 'Read']
            assert options.setting_sources == []
            assert options.permission_mode == 'dontAsk'
            assert options.env['DISABLE_AUTO_COMPACT'] == '0'
            assert options.strict_mcp_config
            # The same agent must have thinking enabled for evidence review.
            assert options.thinking == {'type': 'enabled', 'budget_tokens': 8192}
            assert options.effort == 'high'
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
    from edge_analysis_v2.agent import runner as model_runner
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
    from edge_analysis_v2.agent import runner as model_runner
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
