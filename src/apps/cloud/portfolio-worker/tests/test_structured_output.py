"""The provider must constrain report shape before the application validates its meaning."""
import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from edge_portfolio.contracts import FinalReport, Section
from edge_portfolio.decision import QuestionSection
from edge_portfolio.structured_output import strict_schema, structured_call


async def test_required_research_repair_forces_a_tool_without_enabling_report_submission():
    def handler(request):
        body = json.loads(request.content)
        assert body['tool_choice'] == 'required'
        assert body['thinking'] == {'type': 'disabled'}
        assert 'reasoning_effort' not in body
        assert [tool['function']['name'] for tool in body['tools']] == ['lookup']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'tool_calls': [{'id': '1', 'function': {'name': 'lookup', 'arguments': '{}'}}]}}]})
    payload = {'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {
        'type': 'object', 'properties': {}}}}], 'messages': [], 'allow_report': False,
        'tool_choice': 'required', 'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await structured_call(client, 'test', payload, Section)


async def test_unfinished_research_repair_does_not_order_the_model_to_submit_an_unavailable_report():
    from edge_portfolio.agent import run_analysis
    from edge_portfolio.contracts import AnalysisInput
    from report_fixtures import final_payload
    from test_agent import Evidence
    stages, attempts = [], []
    async def model(payload):
        if stages[-1] == 'question':
            return {'choices': [{'message': {'content': json.dumps({**final_payload()['question'], 'decision': {}})}}]}
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError('STRUCTURED_OUTPUT_REQUIRED')
        assert not payload['allow_report']
        assert payload['tool_choice'] == 'required'
        assert not any('submit_report 도구 하나로' in str(message.get('content', '')) for message in payload['messages'])
        raise RuntimeError('RESEARCH_REPAIR_REACHED')
    with pytest.raises(RuntimeError, match='RESEARCH_REPAIR_REACHED'):
        await run_analysis(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), 'test',
                           stages.append, model_call=model, evidence=Evidence())


@pytest.mark.parametrize('model', [Section, QuestionSection, FinalReport])
def test_provider_schema_retains_required_fields_and_references(model):
    schema = strict_schema(model.model_json_schema())
    Draft202012Validator.check_schema(schema)
    def check(node):
        if isinstance(node, dict):
            assert not {'minItems', 'maxItems', 'minLength', 'maxLength', 'default', 'title'} & node.keys()
            if node.get('type') == 'object':
                assert set(node['required']) == set(node['properties'])
                assert node['additionalProperties'] is False
            for name in ('properties', '$defs'):
                for value in node.get(name, {}).values():
                    check(value)
            for name in ('items', 'anyOf', 'allOf', 'oneOf'):
                if name in node:
                    check(node[name])
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(schema)
    assert schema['properties'].keys() == model.model_json_schema()['properties'].keys()
    assert not Draft202012Validator(schema).is_valid({})


async def test_report_is_forced_to_strict_tool_and_restored_to_existing_contract():
    payload = {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': 'finish'}],
               'tools': [], 'tool_choice': 'none', 'response_format': {'type': 'json_object'}}
    def handler(request):
        assert str(request.url) == 'https://api.deepseek.com/beta/chat/completions'
        body = json.loads(request.content)
        assert 'response_format' not in body
        assert body['tool_choice'] == {'type': 'function', 'function': {'name': 'submit_report'}}
        function = body['tools'][-1]['function']
        assert function['strict'] is True
        assert 'paragraphs' in function['parameters']['required']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': '', 'reasoning_content': 'done',
            'tool_calls': [{'id': 'report', 'type': 'function', 'function': {
                'name': 'submit_report', 'arguments': '{"paragraphs":["a","b","c"],"evidence":[],"limitations":[]}'}}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await structured_call(client, 'test-key', payload, Section)
    message = result['choices'][0]['message']
    assert 'tool_calls' not in message
    assert message['reasoning_content'] == 'done'
    assert Section.model_validate_json(message['content']).paragraphs == ['a', 'b', 'c']
    assert payload['tools'] == []  # Request history must not accumulate stage schemas.


async def test_research_tools_remain_available_with_required_structured_submission():
    def handler(request):
        body = json.loads(request.content)
        assert body['tool_choice'] == 'auto'
        assert body['thinking'] == {'type': 'enabled'}
        assert [tool['function']['name'] for tool in body['tools']] == ['lookup', 'submit_report']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'lookup-1', 'type': 'function',
            'function': {'name': 'lookup', 'arguments': '{"query":"VOO"}'}}]}}]})
    payload = {'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {
        'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']}}}],
        'tool_choice': 'auto', 'messages': []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await structured_call(client, 'test-key', payload, QuestionSection)
    assert result['choices'][0]['message']['tool_calls'][0]['id'] == 'lookup-1'


async def test_unstructured_provider_response_is_not_silently_accepted():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'not json'}}]}))) as client:
        with pytest.raises(ValueError, match='STRUCTURED_OUTPUT_REQUIRED'):
            await structured_call(client, 'test-key', {'tools': [], 'tool_choice': 'none', 'messages': []}, Section)


async def test_all_tools_use_strict_schemas_without_making_optional_arguments_mandatory():
    from edge_portfolio.agent import TOOLS
    def handler(request):
        body = json.loads(request.content)
        assert all(tool['function']['strict'] is True for tool in body['tools'])
        parameters = body['tools'][0]['function']['parameters']
        assert parameters['properties']['offset']['anyOf'][-1] == {'type': 'null'}
        assert set(parameters['required']) == {'query', 'offset'}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'search-1', 'type': 'function',
            'function': {'name': 'search_etfs', 'arguments': '{"query":"VOO","offset":null}'}}]}}]})
    payload = {'tools': TOOLS, 'tool_choice': 'auto', 'messages': []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await structured_call(client, 'test-key', payload, QuestionSection)
    args = json.loads(result['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
    assert args == {'query': 'VOO'}
    assert 'strict' not in TOOLS[0]['function']


async def test_optional_tool_choice_without_submission_retries_with_forced_report():
    from edge_portfolio.agent import run_analysis
    from edge_portfolio.contracts import AnalysisInput
    from report_fixtures import final_payload
    from test_agent import Evidence
    stages, calls = [], []
    async def model(payload):
        if stages[-1] != 'question':
            raise RuntimeError('REPORT_RECOVERED')
        calls.append(payload['tool_choice'])
        if len(calls) == 1:
            raise ValueError('STRUCTURED_OUTPUT_REQUIRED')
        assert payload['tool_choice'] == 'none'
        content = {**final_payload()['question'], 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(RuntimeError, match='REPORT_RECOVERED'):
        await run_analysis(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'),
                           'test', stages.append, model_call=model, evidence=Evidence())
    assert calls == ['auto', 'none']


async def test_valid_plain_report_is_validated_instead_of_discarded():
    body = '{"paragraphs":["a","b","c"],"evidence":[],"limitations":[]}'
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={'choices': [{'finish_reason': 'stop', 'message': {'content': body}}]}))) as client:
        result = await structured_call(client, 'test-key', {'tools': [], 'messages': []}, Section)
    assert Section.model_validate_json(result['choices'][0]['message']['content']).paragraphs == ['a', 'b', 'c']
    assert result['structured_output_fallback'] is True


async def test_report_tool_is_unavailable_until_required_research_is_done():
    def handler(request):
        body = json.loads(request.content)
        assert 'allow_report' not in body
        assert [tool['function']['name'] for tool in body['tools']] == ['lookup']
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'tool_calls': [{'id': 'lookup-1', 'type': 'function',
            'function': {'name': 'lookup', 'arguments': '{}'}}]}}]})
    payload = {'tools': [{'type': 'function', 'function': {'name': 'lookup', 'parameters': {
        'type': 'object', 'properties': {}}}}], 'messages': [], 'allow_report': False}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await structured_call(client, 'test-key', payload, Section)


@pytest.mark.parametrize('format_failure', ['syntax', 'truncated'])
async def test_json_syntax_retry_does_not_consume_business_validation_retry(format_failure):
    from edge_portfolio.agent import run_analysis
    from edge_portfolio.contracts import AnalysisInput
    from report_fixtures import final_payload
    from test_agent import Evidence
    stages, calls = [], []
    async def model(payload):
        if stages[-1] != 'question':
            raise RuntimeError('REPORT_RECOVERED')
        calls.append(payload)
        content = '{' if len(calls) == 1 else '{}' if len(calls) == 2 else json.dumps({**final_payload()['question'], 'decision': {}})
        return {'choices': [{'finish_reason': 'length' if len(calls) == 1 and format_failure == 'truncated' else 'stop',
                             'message': {'role': 'assistant', 'content': content}}]}
    with pytest.raises(RuntimeError, match='REPORT_RECOVERED'):
        await run_analysis(AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 100}],
            horizon='10년 이상'), 'test', stages.append, model_call=model, evidence=Evidence())
    assert len(calls) == 3


async def test_plain_json_business_errors_reach_existing_report_validation():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}))) as client:
        result = await structured_call(client, 'test-key', {'tools': [], 'messages': []}, Section)
    with pytest.raises(ValueError):
        Section.model_validate_json(result['choices'][0]['message']['content'])
