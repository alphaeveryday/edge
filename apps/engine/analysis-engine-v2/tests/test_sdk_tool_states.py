"""Actual installed SDK MCP state transitions, with no network or paid LLM:

advertise -> invalid schema/unknown name -> reject before execution -> corrected call
advertise -> expected domain error -> failed audit + actionable error -> committed evidence
committed evidence -> invalid final reference -> same-client repair -> accepted final
missing frozen observation -> non-retryable data_unavailable -> alternative source -> evidence
internal/database/audit/workspace failure -> fatal error -> later calls blocked -> no final

Only the model conversation is simulated; tools/list and tools/call execute the SDK handlers.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import json

import pytest

from edge_analysis_v2.agent.runner import make_server, run_model
from edge_analysis_v2.tools.execution import AuditedExecution, ToolInputError


SCHEMA = {'function': {'name': 'read_observation', 'description': 'Read one available observation by ID.',
    'parameters': {'type': 'object', 'required': ['instrument_id'], 'additionalProperties': False,
                   'properties': {'instrument_id': {'type': 'string', 'enum': ['known']},
                                  'date': {'type': 'string', 'format': 'date'}}}}}


async def sdk_call(server, name, arguments):
    # Use the actual SDK-created MCP endpoint, including its own schema/exception adapter.
    entry = server['instance'].get_request_handler('tools/call')
    return await entry.handler(None, entry.params_type(name=name, arguments=arguments))


async def sdk_list(server):
    entry = server['instance'].get_request_handler('tools/list')
    return (await entry.handler(None, entry.params_type())).tools


def text(result):
    return '\n'.join(block.text for block in result.content if block.type == 'text')


class MemoryStore:
    def __init__(self, fail=False):
        self.runs = []
        self.fail = fail

    def register_definition(self, **definition):
        pass

    def save_run(self, **record):
        if self.fail:
            raise ConnectionError('postgresql://private-password@host unavailable')
        self.runs.append(deepcopy(record))
        return record['output']


def audited(call, store):
    return AuditedExecution(call, store, definitions=[{
        'tool_id': 'read_observation:v1', 'function_name': 'read_observation', 'version': 'v1',
        'description': SCHEMA['function']['description'], 'source_names': ['observations']}],
        analysis_kind='outlook', analysis_id='analysis', context={'analysis_at': '2026-10-17T00:00:00Z'})


def test_sdk_input_rejection_then_audited_repair_returns_only_committed_evidence():
    store = MemoryStore()
    calls = []
    def call(name, args):
        calls.append(args)
        if args.get('date') == '2026-10-16':
            raise ToolInputError('No observation on this date; choose 2026-10-15.')
        return {'tool_run_id': 'committed', 'result': {'value': 17}}
    server, allowed = make_server([SCHEMA], audited(call, store).call)
    async def scenario():
        listed = await sdk_list(server)
        assert [(t.name, t.description) for t in listed] == [('read_observation', SCHEMA['function']['description'])]
        assert allowed == ['mcp__analysis__read_observation']
        for name, args in [('missing_tool', {}), ('read_observation', {}),
                           ('read_observation', {'instrument_id': 1}),
                           ('read_observation', {'instrument_id': 'known', 'extra': True}),
                           ('read_observation', {'instrument_id': 'not-in-scope'})]:
            rejected = await sdk_call(server, name, args)
            assert rejected.is_error and text(rejected)
        assert not calls and not store.runs
        rejected = await sdk_call(server, 'read_observation', {'instrument_id': 'known', 'date': '2026-10-16'})
        assert rejected.is_error
        error = json.loads(text(rejected))['error']
        assert error['code'] == 'invalid_arguments' and error['retryable'] is True
        assert error['tool_run_id'] == store.runs[-1]['tool_run_id']
        assert '2026-10-15' in error['message']
        repaired = await sdk_call(server, 'read_observation', {'instrument_id': 'known', 'date': '2026-10-15'})
        assert not repaired.is_error
        assert json.loads(text(repaired)) == store.runs[-1]['output']
        assert [row['output'] is None for row in store.runs] == [True, False]
    asyncio.run(scenario())


def test_invalid_calendar_date_is_correctable_before_calculation():
    store = MemoryStore()
    server, _ = make_server([SCHEMA], audited(lambda *args: {'tool_run_id': 'bad', 'result': {}}, store).call)
    result = asyncio.run(sdk_call(server, 'read_observation', {'instrument_id': 'known', 'date': '2026-02-30'}))
    assert result.is_error and 'date' in text(result)
    assert not store.runs


@dataclass
class ResultMessage:
    subtype: str = 'success'
    structured_output: object = None
    result: str = ''
    is_error: bool = False


@pytest.mark.parametrize('failure', ['calculation', 'database', 'audit_store'])
def test_sdk_cannot_turn_internal_or_storage_failure_into_a_successful_final(tmp_path, failure):
    store = MemoryStore(fail=failure == 'audit_store')
    calls, results, final_checks = [], [], []
    def call(name, args):
        calls.append(name)
        if failure == 'database':
            raise ConnectionError('postgresql://private-password@host unavailable')
        if failure == 'calculation':
            raise KeyError('private-password')
        return {'tool_run_id': 'uncommitted', 'result': {'value': 17}}
    class Client:
        def __init__(self, *, options):
            self.options = options
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def query(self, text): pass
        async def receive_response(self):
            server = self.options.mcp_servers['analysis']
            # Simulate a model that ignores a fatal result and attempts another call/final.
            for _ in range(2):
                results.append(await sdk_call(server, 'read_observation', {'instrument_id': 'known'}))
            yield ResultMessage(structured_output={'summary': 'looks successful'})
    with pytest.raises(RuntimeError, match='Tool execution failed|infrastructure'):
        asyncio.run(run_model(initial={'question': 'Inspect observation'}, prompt='system', schemas=[SCHEMA],
            call=audited(call, store).call, output_schema={'type': 'object'}, artifacts=tmp_path,
            key='test-key', model='model', client_factory=Client,
            final_check=lambda result: final_checks.append(result)))
    assert len(calls) == 1 and not final_checks
    assert results and all(result.is_error for result in results)
    assert all('private-password' not in text(result) for result in results)
    assert all(json.loads(text(result))['error']['retryable'] is False for result in results)
    assert not (tmp_path/'response.json').exists()
    assert all('private-password' not in p.read_text(encoding='utf-8') for p in tmp_path.iterdir() if p.is_file())



def active_schemas(kind, directory):
    from edge_analysis_v2.agent.output_schema import EDIT_SCHEMAS
    from edge_analysis_v2.sources.database import DatabaseTools
    from edge_analysis_v2.sources.web_research import WebResearch
    from edge_analysis_v2.tools.fixture_data import FixtureTools, make_fixture
    from edge_analysis_v2.tools.graph.catalog import load_catalog
    from edge_analysis_v2.tools.graph.provider import GraphTools
    if kind == 'editing':
        return EDIT_SCHEMAS
    if kind == 'graph':
        return GraphTools(lambda *args: [], load_catalog()[0], directory,
                          '2026-10-17T00:00:00+00:00').schemas
    fixture = make_fixture()
    if kind == 'fixture':
        return FixtureTools(fixture).schemas
    fixture['news_events'] = []
    web = WebResearch('unused-offline-key', fixture['context']['analysis_at'],
                      request=lambda *args: pytest.fail('schema test must not use network'))
    return DatabaseTools(fixture, web=web).schemas


@pytest.mark.parametrize('kind', ['database_and_web', 'fixture', 'editing', 'graph'])
def test_every_advertised_tool_survives_sdk_schema_conversion_and_rejects_extra_arguments(tmp_path, kind):
    from edge_analysis_v2.tools.model_schema import agent_tool_schemas
    schemas = active_schemas(kind, tmp_path/'graph')
    called = []
    server, allowed = make_server(schemas, lambda *args: called.append(args))
    async def scenario():
        listed = await sdk_list(server)
        assert len(listed) == len(schemas) and len({tool.name for tool in listed}) == len(schemas)
        for advertised, original, visible in zip(listed, schemas, agent_tool_schemas(schemas)):
            definition = original['function']
            assert advertised.name == definition['name']
            assert advertised.description == definition['description'] and advertised.description.strip()
            assert advertised.input_schema == visible['function']['parameters']
            assert advertised.input_schema['additionalProperties'] is False
            assert 'mcp__analysis__' + advertised.name in allowed
            rejected = await sdk_call(server, advertised.name, {'__unexpected_argument__': True})
            assert rejected.is_error and text(rejected)
            if definition['parameters'].get('required'):
                # Required fields are never guessed or silently defaulted at the SDK boundary.
                rejected = await sdk_call(server, advertised.name, {})
                assert rejected.is_error and 'required' in text(rejected)
            from pydantic import ValidationError
            with pytest.raises(ValidationError):
                await sdk_call(server, advertised.name, ['not-an-argument-object'])
        if kind != 'graph':
            assert not {'search_news_articles', 'search_news_threads', 'get_issue_evidence'} & {tool.name for tool in listed}
        assert not called
    asyncio.run(scenario())


def test_workspace_validation_can_repair_but_disk_failure_blocks_analysis_and_workspace(tmp_path, monkeypatch):
    from edge_analysis_v2.agent.tool_errors import ToolFailureState
    from edge_analysis_v2.agent.work_context import WorkContext
    from edge_analysis_v2.agent.work_session import WorkSession
    from edge_analysis_v2.agent.work_tools import make_workspace_server
    state = ToolFailureState()
    session = WorkSession(tmp_path/'workspace', tmp_path/'artifacts', (), [])
    workspace = make_workspace_server(WorkContext({'news': []}), session, state)
    calls = []
    analysis, _ = make_server([SCHEMA], lambda *args: calls.append(args), failure_state=state)
    async def scenario():
        listed = await sdk_list(workspace)
        assert {tool.name for tool in listed} == {'read_source', 'read_tool_result', 'create_directory', 'update_tasks', 'retire_research_materials'}
        for tool in listed:
            assert tool.description.strip() and tool.input_schema['additionalProperties'] is False
            invalid = await sdk_call(workspace, tool.name, {'__unexpected_argument__': True})
            assert invalid.is_error
        invalid = await sdk_call(workspace, 'create_directory', {'path': '../outside'})
        assert invalid.is_error and 'notes/' in text(invalid)
        valid = await sdk_call(workspace, 'create_directory', {'path': 'notes/research'})
        assert not valid.is_error
        missing = await sdk_call(workspace, 'read_tool_result', {'result_id': 'missing-result'})
        assert missing.is_error and json.loads(text(missing))['error']['retryable'] is True
        def disk_failure(*args, **kwargs):
            raise OSError('private-password disk path')
        monkeypatch.setattr(session, 'note_path', disk_failure)
        failed = await sdk_call(workspace, 'create_directory', {'path': 'notes/next'})
        assert failed.is_error and json.loads(text(failed))['error']['retryable'] is False
        assert 'private-password' not in text(failed)
        assert (await sdk_call(workspace, 'read_source', {'source': 'news'})).is_error
        assert (await sdk_call(analysis, 'read_observation', {'instrument_id': 'known'})).is_error
        assert not calls
        with pytest.raises(RuntimeError, match='Tool execution failed'):
            state.check()
    asyncio.run(scenario())


def test_news_discovery_error_repair_read_evidence_and_final_reference_repair(tmp_path):
    from edge_analysis_v2.sources.database import DatabaseTools
    from test_news_events import source, add
    data = source()
    add(data, 'correction', thread='t', novelty='CORRECTION')
    provider = DatabaseTools(data)
    store = MemoryStore()
    execution = AuditedExecution(provider.call, store, definitions=provider.definitions,
        analysis_kind='outlook', analysis_id='analysis', context=data['context'])
    states, queries, evidence = [], [], []
    class Client:
        def __init__(self, *, options): self.options = options
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def query(self, text): queries.append(text)
        async def receive_response(self):
            server = self.options.mcp_servers['analysis']
            if len(queries) == 1:
                for name, arguments in [('list_events', {'cursor': 'invalid-cursor'}),
                        ('get_event_timeline', {'event_key': 'event:missing'}),
                        ('get_news_evidence', {'news_ids': ['missing-news'], 'include_body': False})]:
                    failed = await sdk_call(server, name, arguments)
                    error = json.loads(text(failed))['error']
                    assert failed.is_error and error['retryable'] and error['tool_run_id']
                    states.append('input_error')
                listing = json.loads(text(await sdk_call(server, 'list_events', {})))
                key = listing['result']['items'][0]['event_key']
                timeline = json.loads(text(await sdk_call(server, 'get_event_timeline', {'event_key': key})))
                assert timeline['result']['items'][0]['news_ids'] == ['correction']
                for body in (True, False):
                    result = await sdk_call(server, 'get_news_evidence', {'news_ids': ['correction'], 'include_body': body})
                    assert not result.is_error
                    evidence.append(json.loads(text(result)))
                states.extend(['repaired_discovery', 'timeline', 'read', 'committed_evidence'])
                yield ResultMessage(structured_output={'tool_run_id': 'made-up-id'})
            else:
                states.append('final_repair')
                yield ResultMessage(structured_output={'tool_run_id': evidence[-1]['tool_run_id']})
    def check(final):
        return None if evidence and final['tool_run_id'] == evidence[-1]['tool_run_id'] else 'Use the committed get_news_evidence include_body=false tool_run_id.'
    final = asyncio.run(run_model(initial={'question': 'Read the correction'}, prompt='system',
        schemas=provider.schemas, call=execution.call, output_schema={'type': 'object'},
        artifacts=tmp_path, key='offline-key', model='model', client_factory=Client, final_check=check))
    assert final['tool_run_id'] == store.runs[-1]['tool_run_id']
    assert len(queries) == 2 and states == ['input_error'] * 3 + ['repaired_discovery', 'timeline', 'read', 'committed_evidence', 'final_repair']
    assert evidence[0]['tool_run_id'] != evidence[1]['tool_run_id']
    assert json.loads((tmp_path/'response.json').read_text(encoding='utf-8')) == final


def test_result_transport_failure_is_fatal_and_cannot_expose_partial_evidence():
    from edge_analysis_v2.agent.tool_errors import ToolFailureState
    state, calls = ToolFailureState(), []
    def call(*args):
        calls.append(args)
        return {'tool_run_id': 'already-committed', 'result': {}}
    def broken_formatter(result):
        raise ValueError('private-password serialization failure')
    server, _ = make_server([SCHEMA], call, broken_formatter, state)
    async def scenario():
        for _ in range(2):
            failed = await sdk_call(server, 'read_observation', {'instrument_id': 'known'})
            assert failed.is_error and json.loads(text(failed))['error']['retryable'] is False
            assert 'private-password' not in text(failed)
        assert len(calls) == 1
        with pytest.raises(RuntimeError, match='Tool execution failed'):
            state.check()
    asyncio.run(scenario())


def test_workspace_failure_cannot_be_ignored_by_model_final(tmp_path, monkeypatch):
    from edge_analysis_v2.agent.work_context import WorkContext
    def broken_read(*args, **kwargs):
        raise OSError('private-password disk failure')
    monkeypatch.setattr(WorkContext, 'read', broken_read)
    seen = []
    class Client:
        def __init__(self, *, options): self.options = options
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def query(self, text): pass
        async def receive_response(self):
            seen.append(await sdk_call(self.options.mcp_servers['workspace'], 'read_source', {'source': 'news'}))
            yield ResultMessage(structured_output={})
    with pytest.raises(RuntimeError, match='Tool execution failed'):
        asyncio.run(run_model(initial={'news': []}, prompt='system', schemas=[], call=lambda *args: None,
            output_schema={'type': 'object'}, artifacts=tmp_path, key='offline-key', model='model', client_factory=Client))
    assert seen[0].is_error and 'private-password' not in text(seen[0])
    assert not (tmp_path/'response.json').exists()


@pytest.mark.parametrize('error_type,code', [('ToolDataUnavailableError', 'data_unavailable'),
                                            ('ToolSourceDataError', 'invalid_source_data')])
def test_unavailable_data_is_not_retryable_but_alternative_observations_remain_usable(error_type, code):
    # Metadata is typed at the raising site, never inferred by parsing this message.
    from edge_analysis_v2.tools import execution
    expected_refusal = getattr(execution, error_type)
    store = MemoryStore()
    def call(name, args):
        if args.get('date') == '2026-10-16':
            raise expected_refusal('This observation cannot be used. Choose another covered source.')
        return {'tool_run_id': 'alternative-evidence', 'result': {'value': 17}}
    server, _ = make_server([SCHEMA], audited(call, store).call)
    async def scenario():
        missing = await sdk_call(server, 'read_observation', {'instrument_id': 'known', 'date': '2026-10-16'})
        error = json.loads(text(missing))['error']
        assert missing.is_error and error['code'] == code
        assert error['retryable'] is False and error['tool_run_id'] == store.runs[0]['tool_run_id']
        alternative = await sdk_call(server, 'read_observation', {'instrument_id': 'known', 'date': '2026-10-15'})
        assert not alternative.is_error
        assert json.loads(text(alternative)) == store.runs[-1]['output']
        assert [row['output'] is None for row in store.runs] == [True, False]
    asyncio.run(scenario())
