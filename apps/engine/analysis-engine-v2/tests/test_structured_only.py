"""Completed research must not start another workspace/research loop to fill screen fields."""
import asyncio
import json

import pytest
from jsonschema import ValidationError

from edge_analysis_v2.agent.runner import run_model
from test_model_runner import ResultMessage, SCHEMA


def invoke(tmp_path, responses, *, check=None, **overrides):
    queries = []
    class Client:
        def __init__(self, *, options):
            assert options.effort == overrides.get('effort', 'high')
            assert options.tools == []
            assert options.allowed_tools == []
            assert options.mcp_servers == {}
            assert not options.plugins and not options.skills and not options.hooks
            assert options.output_format['schema'] == SCHEMA
            assert 'notes/' not in options.system_prompt
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, text):
            queries.append(text)
        async def receive_response(self):
            yield ResultMessage(structured_output=responses[min(len(queries)-1, len(responses)-1)])
    args = dict(initial={'body': {'items': ['verified fact']}}, prompt='Use the completed body.',
                schemas=[], call=lambda *a: pytest.fail('No data calls while filling fields'),
                output_schema=SCHEMA, artifacts=tmp_path, key='test-secret', model='deepseek-flash',
                client_factory=Client, final_check=check, structured_only=True)
    result = asyncio.run(run_model(**(args | overrides)))
    return result, queries


def test_completed_input_is_inline_without_notebook_or_todo_rounds(tmp_path):
    result, queries = invoke(tmp_path, [{'summary': 'ok'}])
    assert result == {'summary': 'ok'} and len(queries) == 1
    assert json.loads(queries[0])['input'] == {'body': {'items': ['verified fact']}}
    assert (tmp_path/'response.json').is_file() and (tmp_path/'measurement.json').is_file()


def test_evidence_corrections_still_use_same_output_contract(tmp_path):
    result, queries = invoke(tmp_path, [{'summary': 'bad'}, {'summary': 'fixed'}],
        check=lambda answer: 'Unknown evidence ID; correct it.' if answer['summary'] == 'bad' else None)
    assert result == {'summary': 'fixed'} and len(queries) == 2
    assert queries[1] == 'Unknown evidence ID; correct it.'


def test_invalid_fields_cannot_be_recorded_as_success(tmp_path):
    with pytest.raises(ValidationError):
        invoke(tmp_path, [{'wrong': 'value'}])
    assert not (tmp_path/'response.json').exists()
    assert (tmp_path/'measurement.json').is_file()


def test_bounded_writer_effort_is_explicit_and_recorded(tmp_path):
    invoke(tmp_path, [{'summary': 'ok'}], effort='low')
    assert json.loads((tmp_path/'environment.json').read_text())['reasoning_effort'] == 'low'


def test_unknown_effort_fails_before_model_calls(tmp_path):
    with pytest.raises(ValueError, match='effort'):
        invoke(tmp_path, [{'summary': 'ok'}], effort='typo')


@pytest.mark.parametrize('settings', [{'resume': True}, {'skills': ('cause-investigation',)},
    {'web_search': True}, {'compare_explanations': True}, {'optional_skills': ('cause-investigation',)},
    {'schemas': [{'function': {'name': 'research'}}]}])
def test_conflicting_research_settings_fail_before_spending_tokens(tmp_path, settings):
    with pytest.raises(ValueError, match='structured_only'):
        invoke(tmp_path, [{'summary': 'ok'}], **settings)
