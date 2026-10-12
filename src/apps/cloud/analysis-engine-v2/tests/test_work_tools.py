"""Agents can select real sources and recover without guessing internal data layouts."""
import asyncio
import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from edge_analysis_v2.agent.work_context import WorkContext
from edge_analysis_v2.agent.work_tools import make_workspace_server


def read_tool(monkeypatch, sources):
    from edge_analysis_v2.agent import work_tools
    monkeypatch.setattr(work_tools, 'create_sdk_mcp_server', lambda **kwargs: kwargs)
    server = make_workspace_server(WorkContext(sources), None)
    return next(tool for tool in server['tools'] if tool.name == 'read_source')


def test_source_schema_only_offers_observations_available_in_this_run(monkeypatch):
    tool = read_tool(monkeypatch, {'news':[], 'prices':{'NOVA':[]}})
    validator = Draft202012Validator(tool.input_schema)
    validator.validate({'source':'news'})
    with pytest.raises(ValidationError):
        validator.validate({'source':'guessed_database'})


@pytest.mark.parametrize('source,subject', [('news','NOVA'), ('prices','guessed_ticker')])
def test_invalid_subject_returns_an_executable_recovery_without_fabricated_data(monkeypatch, source, subject):
    tool = read_tool(monkeypatch, {'news':[{'title':'Original headline'}], 'prices':{'NOVA':[10,11]}})
    failed = asyncio.run(tool.handler({'source':source, 'subject':subject}))
    assert failed['is_error']
    error = json.loads(failed['content'][0]['text'])['error']
    assert error['code'] == 'invalid_argument' and error['field'] == 'subject'
    assert error['retryable'] is True
    retry = error['retry_arguments']
    assert retry['source'] == source and 'subject' not in retry
    repaired = asyncio.run(tool.handler(retry))
    assert not repaired.get('is_error', False)
    assert json.loads(repaired['content'][0]['text'])['rows']


def test_unknown_source_lists_real_choices_instead_of_recommending_guesses(monkeypatch):
    tool = read_tool(monkeypatch, {'news':[]})
    failed = asyncio.run(tool.handler({'source':'headlines'}))
    error = json.loads(failed['content'][0]['text'])['error']
    assert error['allowed_values'] == ['news']
    assert 'retry_arguments' not in error  # No automatic substitution of the user's target.


def test_empty_source_is_a_successful_observation_not_a_retryable_error(monkeypatch):
    tool = read_tool(monkeypatch, {'news':[]})
    result = asyncio.run(tool.handler({'source':'news'}))
    assert not result.get('is_error', False)
    data = json.loads(result['content'][0]['text'])
    assert data['rows'] == [] and data['total_rows'] == 0 and data['next_offset'] is None


@pytest.mark.parametrize('arguments,field', [
    ({'source':[]}, 'source'), ({'source':'news','subject':[]}, 'subject'),
    ({'source':'news','offset':True}, 'offset'), ({'source':'news','limit':0}, 'limit')])
def test_invalid_input_types_return_repair_guidance_instead_of_internal_exceptions(monkeypatch, arguments, field):
    tool = read_tool(monkeypatch, {'news':[]})
    result = asyncio.run(tool.handler(arguments))
    assert result['is_error']
    error = json.loads(result['content'][0]['text'])['error']
    assert error['field'] == field and error['code'] == 'invalid_argument'


def test_internal_failure_is_not_misrepresented_as_bad_model_input(monkeypatch):
    tool = read_tool(monkeypatch, {'news':[]})
    def broken_read(*args, **kwargs):
        raise RuntimeError('Internal fixture failure')
    monkeypatch.setattr(WorkContext, 'read', broken_read)
    with pytest.raises(RuntimeError, match='Internal fixture failure'):
        asyncio.run(tool.handler({'source':'news'}))
