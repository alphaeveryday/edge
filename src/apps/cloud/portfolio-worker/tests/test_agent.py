import json
import httpx
import pytest
from itertools import combinations
from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput, ROLES, LIMITS
from report_fixtures import final_payload


class Evidence:
    sources = [{"id": "s1", "passages": [{"id": "p1", "text": "official source quotation"}]}]


@pytest.fixture(autouse=True)
def offline_prices(monkeypatch):
    async def unavailable(self, rows):
        return {'status': 'unavailable', 'reason': 'TEST_PRICE_PROVIDER_OFFLINE'}
    monkeypatch.setattr('edge_portfolio.market_data.MarketData.get', unavailable)


def optimization_call(payload):
    if any(call['function']['name'] == 'optimize_portfolios' for message in payload['messages'] for call in message.get('tool_calls', [])):
        return None
    ids = ['US:VOO', 'US:QQQ', 'US:VTI', 'US:SPY']
    groups = [[key] for key in ids] + [list(pair) for pair in combinations(ids, 2)]
    return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': 'optimize', 'type': 'function', 'function': {'name': 'optimize_portfolios', 'arguments': json.dumps({'combinations': groups})}}]}}]}


@pytest.mark.parametrize('disconnect_once', [False, True])
async def test_one_conversation_visits_four_roles_and_keeps_context(disconnect_once):
    phases, conversations = [], []
    disconnected = False
    async def model(payload):
        nonlocal disconnected
        if disconnect_once and not disconnected:
            disconnected = True
            raise httpx.ReadError('response connection closed')
        conversations.append(len(payload["messages"]))
        role = phases[-1]
        if role == 'portfolio' and (call := optimization_call(payload)):
            return call
        count = LIMITS[role][0] // 3
        section = {"paragraphs": ["가" * count, "나" * count, "다" * count], "evidence": [{"source_id": "s1", "passage_id": "p1"}], "limitations": []}
        if role == 'opinion':
            section = final_payload({name: {**section, 'paragraphs': ['가' * (LIMITS[name][0] // 3)] * 3} for name in ROLES})
            section['recommendation'] = None  # Missing input weights must never produce a recommendation.
        return {"choices": [{"message": {"role": "assistant", "content": json.dumps(section), "reasoning_content": "private"}}]}
    result = await run_analysis(AnalysisInput(holdings=[{"name": "VOO", "amount_krw": 100}], horizon="10년 이상"), "test", phases.append, model_call=model, evidence=Evidence())
    assert phases == list(ROLES)
    assert conversations == sorted(set(conversations))
    assert list(result["sections"]) == list(ROLES)
    assert "private" not in json.dumps(result)
    assert result['recommendation'] is None
    assert result['optimization']['reason'] == 'CURRENT_WEIGHTS_REQUIRED'


async def test_repeated_model_read_failure_stops_after_one_retry():
    calls = []
    async def model(payload):
        calls.append(1)
        raise httpx.ReadError('response connection closed')
    with pytest.raises(httpx.ReadError):
        await run_analysis(AnalysisInput(holdings=[{"name": "VOO", "amount_krw": 100}], horizon="10년 이상"),
                           'test', lambda _: None, model_call=model, evidence=Evidence())
    assert len(calls) == 2


async def test_invalid_output_stops_after_one_repair():
    calls = []
    async def model(payload):
        calls.append(1)
        return {"choices": [{"message": {"role": "assistant", "content": "{}"}}]}
    with pytest.raises(ValueError, match="OUTPUT_INVALID"):
        await run_analysis(AnalysisInput(holdings=[{"name": "VOO", "amount_krw": 100}], horizon="10년 이상"), "test", lambda _: None, model_call=model, evidence=Evidence())
    assert len(calls) == 2


async def test_unregistered_url_is_rejected_without_losing_the_whole_analysis():
    class RestrictedEvidence(Evidence):
        discovered = {'https://official.example/fund'}
        async def call(self, name, args):
            raise ValueError('TOOL_NOT_ALLOWED')
    phases, rejected = [], []
    async def model(payload):
        role = phases[-1]
        if role == 'portfolio' and (call := optimization_call(payload)):
            return call
        if role == 'portfolio' and not rejected:
            rejected.append(True)
            return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'call1', 'type': 'function', 'function': {'name': 'fetch_source', 'arguments': '{"url":"https://invented.example"}'}}]}}]}
        if role == 'portfolio' and payload['messages'][-1]['role'] == 'tool' and payload['messages'][-1]['tool_call_id'] != 'optimize':
            reply = json.loads(payload['messages'][-1]['content'])
            assert reply['status'] == 'rejected'
            assert reply['fetchable_urls'] == ['https://official.example/fund']
        def section(name):
            return {'paragraphs': ['가' * (LIMITS[name][0] // 3)] * 3, 'evidence': [{'source_id': 's1', 'passage_id': 'p1'}], 'limitations': []}
        content = final_payload({name: section(name) for name in ROLES}) if role == 'opinion' else section(role)
        if role == 'opinion':
            content['recommendation'] = None
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    result = await run_analysis(AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상'), 'test', phases.append, model_call=model, evidence=RestrictedEvidence())
    assert phases == list(ROLES)
    assert list(result['sections']) == list(ROLES)
