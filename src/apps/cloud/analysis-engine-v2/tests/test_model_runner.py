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
    assert len(captured) == 1
    research = (prompt_path.parent/'research.md').read_text(encoding='utf-8')
    contract = (prompt_path.parent/'output-contract.md').read_text(encoding='utf-8')
    writing = (tmp_path/'AGENTS.md').read_text(encoding='utf-8')
    assert research in writing and prompt in writing and contract in writing
    assert research not in captured[0]
    assert (tmp_path / 'system_prompt.txt').read_text(encoding='utf-8') == captured[0]
    query = json.loads((tmp_path/'model_input.json').read_text(encoding='utf-8'))
    assert query['workspace_instructions']['content'] == writing
    # Project instructions reach both tasks without forcing a Skill or Read call.
    for name in ('research.md', 'output-contract.md'):
        content = (prompt_path.parent/name).read_text(encoding='utf-8')
        assert writing.count(content) == 1


def test_valid_json_after_work_registration_needs_no_skill_call_and_workspace_is_removed(tmp_path):
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


def test_tinyfish_credential_is_not_inherited_by_model_process(monkeypatch, tmp_path):
    monkeypatch.setenv('TINYFISH_API_KEY', 'private-provider-key')
    base = client_for(ResultMessage(structured_output={'summary':'ok'}))
    class Client(base):
        async def __aenter__(self):
            assert self.options.env['TINYFISH_API_KEY'] == ''
            assert 'private-provider-key' not in self.options.system_prompt
            return self
    asyncio.run(run_model(initial={'news':[]}, prompt='system', schemas=[], call=lambda *args:None,
        output_schema=SCHEMA, artifacts=tmp_path, key='test-secret', model='deepseek-flash', client_factory=Client))
    assert all('private-provider-key' not in p.read_text(encoding='utf-8')
               for p in tmp_path.iterdir() if p.is_file())


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


@pytest.mark.parametrize('kind,seconds',[('outlook',600),('movement',300)])
def test_deep_outlook_has_more_time_without_expanding_movement(monkeypatch,tmp_path,kind,seconds):
    original = asyncio.timeout
    deadlines = []
    def timeout(value):
        deadlines.append(value)
        return original(value)
    monkeypatch.setattr(asyncio,'timeout',timeout)
    asyncio.run(run_model(initial={'news':[]}, prompt='system', schemas=[],
        call=lambda name,args:None, output_schema=SCHEMA, artifacts=tmp_path,
        key='test-secret', model='deepseek-flash', kind=kind,
        client_factory=client_for(ResultMessage(structured_output={'summary':'ok'}))))
    assert deadlines == [seconds]


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
            assert options.tools == ['Skill', 'Read', 'Write', 'Edit']
            assert options.setting_sources == []
            assert options.permission_mode == 'dontAsk'
            assert options.env['DISABLE_AUTO_COMPACT'] == '0'
            assert options.env['CLAUDE_AUTOCOMPACT_PCT_OVERRIDE'] == '80'
            assert options.model == 'deepseek-flash[1m]'
            assert options.env['CLAUDE_CODE_DISABLE_1M_CONTEXT'] == '0'
            assert options.env['CLAUDE_CODE_AUTO_COMPACT_WINDOW'] == '1000000'
            assert 'PreCompact' in options.hooks
            assert options.strict_mcp_config
            # The same agent must have thinking enabled for evidence review.
            assert options.thinking == {'type': 'enabled', 'budget_tokens': 8192}
            assert options.effort == 'high'
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, text):
            assert json.loads(text)['input']['available_sources']['news'] == {'rows': 0}
        async def receive_response(self):
            if message:
                state = self.options.hooks['PostToolUse'][0].hooks[0].__self__
                state.update_tasks([{'content':'Answer the fixture question', 'status':'completed'}])
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


def test_unfinished_tasks_resume_same_client_and_only_last_result_is_accepted(tmp_path):
    queries = []
    class Client:
        def __init__(self, *, options):
            self.options = options
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, text):
            queries.append(text)
        async def receive_response(self):
            done = len(queries) == 2
            hook = self.options.hooks['PostToolUse'][0].hooks[0]
            hook.__self__.update_tasks([{'content':'Resolve contradictory filing',
                'status':'completed' if done else 'pending'}])
            yield ResultMessage(structured_output={'summary':'resolved' if done else 'premature'})
    result = asyncio.run(run_model(initial={'news':[]}, prompt='system', schemas=[], call=lambda *a:None,
        output_schema=SCHEMA, artifacts=tmp_path, key='test-secret', model='deepseek-flash', client_factory=Client))
    assert result == {'summary':'resolved'}
    assert len(queries) == 2 and 'Resolve contradictory filing' in queries[1]
    assert json.loads((tmp_path/'response.json').read_text()) == result


def test_compaction_recovery_precedes_publication_even_when_model_skips_tools(tmp_path):
    queries = []
    class Client:
        def __init__(self, *, options):
            self.options = options
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, text):
            queries.append(text)
        async def receive_response(self):
            if len(queries) == 1:
                hook = self.options.hooks['PreCompact'][0].hooks[0]
                await hook({'trigger':'auto'}, None, {})
                yield ResultMessage(structured_output={'summary':'before recovery'})
            else:
                assert 'Read AGENTS.md again' in queries[-1]
                state = self.options.hooks['PostToolUse'][0].hooks[0].__self__
                state.update_tasks([{'content':'Recover the research question', 'status':'completed'}])
                yield ResultMessage(structured_output={'summary':'after recovery'})
    result = asyncio.run(run_model(initial={'news':[]}, prompt='system', schemas=[], call=lambda *a:None,
        output_schema=SCHEMA, artifacts=tmp_path, key='test-secret', model='deepseek-flash', client_factory=Client))
    assert result == {'summary':'after recovery'}
    assert len(queries) == 2


def test_text_only_final_without_registered_questions_is_never_published(tmp_path):
    queries = []
    class Client:
        def __init__(self, *, options):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, text):
            queries.append(text)
        async def receive_response(self):
            yield ResultMessage(structured_output={'summary':'Looks finished but has no questions'})
    with pytest.raises(ValueError, match='Unfinished'):
        asyncio.run(run_model(initial={'news':[]}, prompt='system', schemas=[], call=lambda *a:None,
            output_schema=SCHEMA, artifacts=tmp_path, key='test-secret', model='deepseek-flash', client_factory=Client))
    assert len(queries) == 4
    assert all('workspace.update_tasks' in query for query in queries[1:])
    assert not (tmp_path/'response.json').exists()
    assert json.loads((tmp_path/'workspace.json').read_text())['status'] == 'incomplete'


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


def test_resume_restores_work_and_rejects_changed_observations(tmp_path):
    initial = {'news':[]}
    (tmp_path/'input.json').write_text(json.dumps(initial))
    (tmp_path/'workspace.json').write_text(json.dumps({'status':'interrupted', 'reminders':1,
        'todos':[{'content':'Use recovered IR', 'status':'pending'}], 'notes':{'evidence.md':'Saved IR'}}))
    class Client(client_for(ResultMessage(structured_output={'summary':'resumed'}))):
        async def query(self, text):
            value = json.loads(text)
            assert value['resumed_work']['todos'][0]['content'] == 'Use recovered IR'
            state = self.options.hooks['PostToolUse'][0].hooks[0].__self__
            assert state.note_path('notes/evidence.md').read_text() == 'Saved IR'
            state.update_tasks([{'content':'Use recovered IR', 'status':'completed'}])
    args = dict(prompt='system', schemas=[], call=lambda *a:None, output_schema=SCHEMA,
        artifacts=tmp_path, key='test-secret', model='deepseek-flash', client_factory=Client, resume=True)
    with pytest.raises(ValueError, match='same original'):
        asyncio.run(run_model(initial={'news':[{'title':'new fact'}]}, **args))
    result = asyncio.run(run_model(initial=initial, **args))
    assert result == {'summary':'resumed'}
    assert json.loads((tmp_path/'workspace.json').read_text())['status'] == 'completed'


def test_an_overlong_final_answer_is_never_accepted(tmp_path):
    # WHY: the CLI sends an over-limit answer back to the model itself; if one still arrives, it must not be published.
    schema = {'type': 'object', 'required': ['summary'], 'additionalProperties': False,
              'properties': {'summary': {'type': 'string', 'maxLength': 5}}}
    class Client:
        def __init__(self, *, options):
            self.options = options
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, text):
            pass
        async def receive_response(self):
            self.options.hooks['PostToolUse'][0].hooks[0].__self__.update_tasks([{'content': 'Answer', 'status': 'completed'}])
            yield ResultMessage(structured_output={'summary': 'far too long'})
    with pytest.raises(ValidationError):
        asyncio.run(run_model(initial={'news': []}, prompt='system', schemas=[], call=lambda *a: None,
            output_schema=schema, artifacts=tmp_path, key='test-secret', model='deepseek-flash', client_factory=Client))
    assert not (tmp_path/'response.json').exists()
