import asyncio
import json
import httpx
import pytest
from itertools import combinations
from types import SimpleNamespace
from edge_portfolio.agent import run_analysis
from edge_portfolio.contracts import AnalysisInput, ROLES, LIMITS
from report_fixtures import final_payload


async def test_research_context_keeps_citable_passages_once_and_all_discovered_urls():
    from edge_portfolio.agent import ExaEvidence
    class Session:
        async def call_tool(self, name, args):
            body = ('URL: https://official.example/first\n' + 'research ' * 1000 + '\nURL: https://official.example/last\n') if name == 'web_search_exa' else 'Official ETF research paragraph.\n' * 200
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text=body)])
    evidence = ExaEvidence(Session())
    found = await evidence.call('search_sources', {'query': 'ETF', 'objective': 'verify alternative'})
    assert len(found['search_results']) <= 2500
    assert 'https://official.example/last' in found['fetchable_urls']
    result = await evidence.call('fetch_source', {'url': 'https://official.example/last'})
    passages = list(result['passages'])
    while result['next_offset'] is not None:
        result = await evidence.call('fetch_source', {'url': 'https://official.example/last', 'offset': result['next_offset']})
        passages.extend(result['passages'])
    assert passages == evidence.sources[0]['passages']
    assert evidence.calls == 2  # Reading saved passages does not spend another provider request.
    assert result['passages']
    assert 'excerpt' not in result  # The same text must not consume context twice.
    assert evidence.sources[0]['excerpt']  # Original evidence stays in the saved report.


async def test_search_and_fetch_share_70_calls_then_stop_before_remote_request():
    from edge_portfolio.agent import ExaEvidence
    calls = []
    class Session:
        async def call_tool(self, name, args):
            calls.append(name)
            body = 'URL: https://official.example/etf\n' + 'Official ETF research. ' * 5
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text=body)])
    evidence = ExaEvidence(Session())
    for index in range(35):
        await evidence.call('search_sources', {'query': f'ETF {index}', 'objective': 'verify facts'})
        await evidence.call('fetch_source', {'url': 'https://official.example/etf', 'expand': True})
    assert calls.count('web_search_exa') == 35
    assert calls.count('web_fetch_exa') == 35
    with pytest.raises(ValueError, match='TOOL_LIMIT'):
        await evidence.call('search_sources', {'query': 'ETF', 'objective': 'verify facts'})
    assert len(calls) == 70  # Do not spend another provider request after the shared budget.


class Evidence:
    sources = [{"id": "s1", "passages": [{"id": "p1", "text": "official source quotation"}]}]


@pytest.mark.parametrize('complete', [False, True])
async def test_analysis_shares_ten_minutes_across_roles(monkeypatch, complete):
    loop = asyncio.get_running_loop()
    now = loop.time()
    monkeypatch.setattr(loop, 'time', lambda: now)
    phases, events = [], []
    release_question, release_opinion = asyncio.Event(), asyncio.Event()

    async def model(payload):
        role = phases[-1]
        if role == 'question':
            await release_question.wait()
        if role == 'portfolio' and (call := optimization_call(payload)):
            return call
        if role == 'opinion':
            await release_opinion.wait()
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}

    task = asyncio.create_task(run_analysis(
        AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상'),
        'test', phases.append, model_call=model, evidence=Evidence(), on_event=events.append))
    try:
        await asyncio.sleep(0)
        now += 301
        for _ in range(3):
            await asyncio.sleep(0)
        assert not task.done(), 'Research must continue beyond the former five-minute deadline'
        release_question.set()
        for _ in range(3):
            await asyncio.sleep(0)
        assert phases == list(ROLES)
        now += 298
        for _ in range(3):
            await asyncio.sleep(0)
        assert not task.done(), 'The final role still has time at 599 seconds'
        assert events[0]['limits']['elapsed_seconds'] == 600
        if complete:
            release_opinion.set()
            result = await task
            assert result['diagnostics']['termination_reason'] == 'COMPLETED'
        else:
            now += 1
            for _ in range(3):
                await asyncio.sleep(0)
            assert task.done(), 'Changing roles must not reset the shared 600-second deadline'
            with pytest.raises(TimeoutError):
                await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_followup_keeps_its_five_minute_deadline(monkeypatch):
    loop = asyncio.get_running_loop()
    now = loop.time()
    monkeypatch.setattr(loop, 'time', lambda: now)
    events = []
    async def model(payload):
        await asyncio.Event().wait()
    task = asyncio.create_task(run_analysis(
        AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), 'test', lambda _: None,
        model_call=model, evidence=Evidence(), followup_question='Explain the saved report',
        prior_report={'sources': []}, on_event=events.append))
    try:
        await asyncio.sleep(0)
        assert events[0]['limits']['elapsed_seconds'] == 300
        now += 300
        for _ in range(3):
            await asyncio.sleep(0)
        assert task.done(), 'Extending initial analysis must not extend followup limits'
        with pytest.raises(TimeoutError):
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_evidence_connection_is_lazy_and_reused_after_the_first_remote_lookup():
    from edge_portfolio.agent import ExaEvidence
    connected = []
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='URL: https://issuer.example/fund')])
    async def connect():
        connected.append(1)
        return Session()
    evidence = ExaEvidence(None, connect)
    evidence.sources = [{'id': 'saved', 'url': 'https://issuer.example/fund', 'passages': [{'id': 'p1', 'text': 'Saved evidence'}]}]
    await evidence.call('fetch_source', {'url': 'https://issuer.example/fund'})
    assert connected == []
    for _ in range(2):
        await evidence.call('search_sources', {'query': 'ETF factsheet', 'objective': 'verify holdings'})
    assert connected == [1]


async def test_short_readable_table_has_citable_passages():
    from edge_portfolio.agent import ExaEvidence
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='Fees 0.03%\nYield 2.5%')])
    evidence = ExaEvidence(Session())
    evidence.discovered.add('https://issuer.example/fund')
    result = await evidence.call('fetch_source', {'url': 'https://issuer.example/fund'})
    assert [row['text'] for row in result['passages']] == ['Fees 0.03%', 'Yield 2.5%']


async def test_date_at_a_passage_boundary_remains_citable():
    from edge_portfolio.agent import ExaEvidence
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='x' * 276 + '2026-09-30 holdings')])
    evidence = ExaEvidence(Session())
    evidence.discovered.add('https://issuer.example/fund')
    result = await evidence.call('fetch_source', {'url': 'https://issuer.example/fund'})
    assert any('2026-09-30' in passage['text'] for passage in result['passages'])


@pytest.mark.parametrize('date', ['2026-09-30', '30 Sep 2026', '09/30/2026', '30/09/2026', '2026년9월30일', '2026. 09. 30.'])
async def test_standalone_source_date_remains_citable_for_freshness_checks(date):
    from edge_portfolio.agent import ExaEvidence
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='Published: 2026-10-14\nAs of\n' + date + '\nThe fund invests in a diversified portfolio and reports its holdings and expense ratio.')])
    evidence = ExaEvidence(Session())
    evidence.discovered.add('https://issuer.example/fund')
    result = await evidence.call('fetch_source', {'url': 'https://issuer.example/fund'})
    assert any(passage['text'] == date for passage in result['passages'])
    assert not any(passage['text'].startswith('Published:') for passage in result['passages'])


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
        if role == 'question':
            section = {**section, 'decision': {}}
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
        if role == 'question':
            content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    result = await run_analysis(AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상'), 'test', phases.append, model_call=model, evidence=RestrictedEvidence())
    assert phases == list(ROLES)
    assert list(result['sections']) == list(ROLES)


@pytest.mark.parametrize('research_calls,passage,invalid_final,expected_error', [
    (17, '', 2, None), (1, '가' * 70000, 0, None),
    (1, '가' * 120000, 0, 'MODEL_CONTEXT_BUDGET'), (1, '', 3, 'OUTPUT_INVALID')],
    ids=['research-budget', 'utf8-size', 'context-ceiling', 'repair-ceiling'])
async def test_candidate_research_budget_and_korean_utf8_size_allow_final_publication(research_calls, passage, invalid_final, expected_error):
    class ResearchEvidence(Evidence):
        async def call(self, name, args):
            return {'passage': passage}
    phases, used, repairs_used = [], 0, 0
    async def model(payload):
        nonlocal used, repairs_used
        role = phases[-1]
        if role == 'portfolio' and (call := optimization_call(payload)):
            return call
        if role == 'outlook' and used < research_calls:
            used += 1
            return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': str(used), 'type': 'function', 'function': {'name': 'search_sources', 'arguments': '{}'}}]}}]}
        section = {'paragraphs': ['가' * 40] * 3, 'evidence': [{'source_id': 's1', 'passage_id': 'p1'}], 'limitations': []}
        if role == 'opinion' and repairs_used < invalid_final:
            repairs_used += 1
            return {'choices': [{'message': {'role': 'assistant', 'content': '{}'}}]}
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else section
        if role == 'question':
            content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상')
    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            await run_analysis(request, 'test', phases.append, model_call=model, evidence=ResearchEvidence())
        assert repairs_used == invalid_final
        return
    result = await run_analysis(request, 'test', phases.append, model_call=model, evidence=ResearchEvidence())
    assert result['recommendation'] is None
    assert used == research_calls
    assert result['trace'][-1]['call'] == research_calls + 5 + repairs_used


async def test_runaway_tool_loop_still_stops_after_twenty_four_model_calls():
    class ResearchEvidence(Evidence):
        async def call(self, name, args):
            return {}
    calls = 0
    async def model(payload):
        nonlocal calls
        calls += 1
        return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': str(calls), 'type': 'function', 'function': {'name': 'search_sources', 'arguments': '{}'}}]}}]}
    with pytest.raises(ValueError, match='MODEL_CALL_BUDGET'):
        await run_analysis(AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상'),
            'test', lambda _: None, model_call=model, evidence=ResearchEvidence())
    assert calls == 24


async def test_unverified_customer_failure_cannot_hide_available_candidates(monkeypatch):
    candidates = [{'candidate_id': 'candidate-1', 'allocations': [{'instrument_id': 'US:SPY', 'weight_pct': 100}]}]
    async def optimize(self, args):
        self.result = {'status': 'available', 'candidates': candidates}
        self.batches = {'first', 'alternative'}
        return self.result
    monkeypatch.setattr('edge_portfolio.optimizer.Optimizer.tool', optimize)
    phases = []
    async def model(payload):
        role = phases[-1]
        if role == 'portfolio' and (call := optimization_call(payload)):
            return call
        content = {**final_payload(), 'recommendation': None, 'withheld_reason': 'customer_failed'} if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    checkpoints = []
    with pytest.raises(ValueError, match='Available candidates require a recommendation'):
        await run_analysis(AnalysisInput(holdings=[{'name': 'VOO', 'amount_krw': 100}], horizon='10년 이상'),
                           'test', phases.append, model_call=model, evidence=Evidence(), on_checkpoint=checkpoints.append)
    assert checkpoints[-1]['optimization']['candidates'] == candidates


def test_diagnostic_codes_distinguish_budget_and_validation_failures_without_raw_content():
    from edge_portfolio.worker import failure_codes
    assert failure_codes(ValueError('MODEL_CALL_BUDGET')) == ['MODEL_CALL_BUDGET']
    assert failure_codes(ValueError('MODEL_CONTEXT_BUDGET')) == ['MODEL_CONTEXT_BUDGET']
    assert failure_codes(ValueError('OUTPUT_INVALID: Final allocation must exactly match private-data')) == ['OUTPUT_INVALID', 'ALLOCATION_MISMATCH']
    assert failure_codes(ValueError('OUTPUT_INVALID: unexpected private-data')) == ['OUTPUT_INVALID', 'OUTPUT_SCHEMA']


async def test_exa_rate_limit_survives_exception_groups_as_a_specific_job_failure(monkeypatch):
    from edge_portfolio import worker
    request = httpx.Request('POST', 'https://mcp.exa.ai/mcp')
    error = httpx.HTTPStatusError('private provider response', request=request, response=httpx.Response(429, request=request))
    async def fail(*args, **kwargs):
        raise ExceptionGroup('MCP', [error])
    monkeypatch.setattr(worker, 'run_analysis', fail)
    monkeypatch.setattr(worker.store, 'claim', lambda: {'id': 'test', 'kind': 'analysis', 'input': {'holdings': [{'name': 'cash', 'kind': 'cash', 'amount_krw': 1000000}], 'horizon': '10년 이상'}})
    monkeypatch.setattr(worker.HEARTBEAT.__class__, 'touch', lambda self: None)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test')
    completed = []
    monkeypatch.setattr(worker.store, 'finish', lambda job, **kwargs: completed.append(kwargs))
    assert await worker.work_once()
    assert completed[0]['failure'] == 'SEARCH_RATE_LIMIT'
    assert completed[0]['result']['diagnostics']['failure_codes'] == ['EXA_RATE_LIMIT']


def test_output_diagnostics_identify_schema_field_without_logging_values_or_unknown_keys():
    from edge_portfolio.contracts import FinalReport
    from edge_portfolio.worker import failure_codes
    payload = final_payload()
    payload['recommendation']['evaluation']['scores']['stability'] = 4.2
    payload['private_user_text'] = 'secret@example.com'
    try:
        FinalReport.model_validate(payload)
    except ValueError as cause:
        error = ValueError('OUTPUT_INVALID: redacted')
        error.__cause__ = cause
    codes = failure_codes(error)
    assert 'SCHEMA_RECOMMENDATION_EVALUATION_SCORES_STABILITY_MULTIPLE_OF' in codes
    assert 'SCHEMA_FIELD_EXTRA_FORBIDDEN' in codes
    assert 'private_user_text' not in str(codes).lower()
    assert 'secret' not in str(codes).lower()
    assert '4.2' not in str(codes)


def test_output_diagnostics_distinguish_missing_source_evidence_and_invalid_json():
    from edge_portfolio.worker import failure_codes
    assert failure_codes(ValueError('OUTPUT_INVALID: Portfolio and outlook require source evidence')) == ['OUTPUT_INVALID', 'EVIDENCE_REQUIRED']
    try:
        json.loads('private_user_text')
    except ValueError as cause:
        error = ValueError('OUTPUT_INVALID: redacted')
        error.__cause__ = cause
    assert failure_codes(error) == ['OUTPUT_INVALID', 'JSON_INVALID']
