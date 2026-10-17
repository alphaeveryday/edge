"""Large audited results remain readable without granting filesystem access."""
import asyncio
import json

import pytest

from edge_analysis_v2.agent.work_context import WorkContext, SourceInputError
from edge_analysis_v2.agent.work_tools import make_workspace_server
from edge_analysis_v2.agent.runner import make_server


def test_large_single_line_result_can_be_reassembled_without_losing_originals():
    context = WorkContext({'context': {'body': 'keep this original'}})
    original = {'tool_run_id': 'f7', 'result': {'body': '긴 공시 원문\\n🙂' * 10000}}
    first = context.tool_response(original)
    assert first['tool_run_id'] == 'f7' and first['total_characters'] > 95000
    chunks, page = [], first
    while True:
        assert len(page['text']) <= 6000
        chunks.append(page['text'])
        if page['next_offset'] is None:
            break
        page = context.read_tool_result(first['result_id'], offset=page['next_offset'])
    assert json.loads(''.join(chunks)) == original
    assert context.initial['context'] == {'body': 'keep this original'}
    assert context.sources == {'context': {'body': 'keep this original'}}


def test_small_results_keep_the_existing_contract_and_results_do_not_replace_each_other():
    context = WorkContext({})
    small = {'tool_run_id': 'f1', 'result': {'price': 100}}
    assert context.tool_response(small) == small
    first = context.tool_response({'tool_run_id': 'f2', 'result': 'a' * 10000})
    second = context.tool_response({'tool_run_id': 'f3', 'result': 'b' * 10000})
    assert first['result_id'] != second['result_id']
    assert context.read_tool_result(first['result_id']) == first


@pytest.mark.parametrize('arguments', [
    {'result_id': '/etc/passwd'}, {'result_id': []},
    {'result_id': 'r1', 'offset': True}, {'result_id': 'r1', 'offset': -1},
    {'result_id': 'r1', 'limit': 0}, {'result_id': 'r1', 'limit': 6001},
])
def test_invalid_pages_cannot_escape_the_current_run_or_expand_response_size(arguments):
    context = WorkContext({})
    context.tool_response({'tool_run_id': 'f1', 'result': 'a' * 10000})
    with pytest.raises(SourceInputError):
        context.read_tool_result(**arguments)


def test_analysis_result_paging_is_wired_to_the_same_workspace_store(monkeypatch):
    from edge_analysis_v2.agent import runner, work_tools
    monkeypatch.setattr(runner, 'create_sdk_mcp_server', lambda **kwargs: kwargs)
    monkeypatch.setattr(work_tools, 'create_sdk_mcp_server', lambda **kwargs: kwargs)
    context = WorkContext({})
    original = {'tool_run_id': 'f1', 'result': 'original text ' * 10000}
    schemas = [{'type': 'function', 'function': {'name': 'observe', 'description': 'Read data',
                'parameters': {'type': 'object', 'properties': {}}}}]
    server, _ = make_server(schemas, lambda *args: original, context.tool_response)
    first = json.loads(asyncio.run(server['tools'][0].handler({}))['content'][0]['text'])
    workspace = make_workspace_server(context, None)
    reader = next(tool for tool in workspace['tools'] if tool.name == 'read_tool_result')
    second = json.loads(asyncio.run(reader.handler({'result_id': first['result_id'],
        'offset': first['next_offset']}))['content'][0]['text'])
    assert second['offset'] == first['next_offset']
    failed = asyncio.run(reader.handler({'result_id': 'another-session'}))
    assert failed['is_error'] and 'unknown' in failed['content'][0]['text'].lower()
