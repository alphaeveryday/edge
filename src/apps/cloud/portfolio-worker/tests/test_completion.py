from types import SimpleNamespace
import pytest

from edge_portfolio.completion import assess_completion, data_status
from edge_portfolio.contracts import AnalysisInput, FinalReport
from edge_portfolio.decision import Decision
from report_fixtures import final_payload


def test_valid_json_without_an_answer_is_not_customer_completion():
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상', question='리밸런싱 어떻게 하나요?')
    result = assess_completion(FinalReport.model_validate(final_payload()), request, Decision(kind='rebalance'),
        {'recommendation': {'allocations': []}}, [], '2026-10-14')
    assert result['status'] == 'partial'
    assert result['reasons'] == ['UNANSWERED_QUESTIONS', 'EXECUTION_PLAN_MISSING']


def test_recovery_keeps_omitted_answers_and_evidence_for_the_same_target():
    from edge_portfolio.completion import retain_research
    first = FinalReport.model_validate({**final_payload(), 'answers': [
        {'question_index': 1, 'status': 'answered', 'answer': 'The first question has been answered.'}],
        'evidence_coverage': [{'instrument_id': 'US:VOO', 'field': 'fees', 'status': 'missing'}]})
    second = FinalReport.model_validate({**final_payload(), 'answers': [
        {'question_index': 2, 'status': 'answered', 'answer': 'The second question has been answered.'}]})
    merged = retain_research(second, first)
    assert {answer.question_index for answer in merged.answers} == {1, 2}
    assert merged.evidence_coverage == first.evidence_coverage
    second.answers *= 2
    with pytest.raises(ValueError, match='ANSWER_QUESTION_INDEX'):
        assess_completion(retain_research(second, first),
            AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), Decision(questions=['First?', 'Second?']),
            {'recommendation': None}, [], '2026-10-14')
    second.answers = second.answers[:1]
    second.recommendation = None
    assert [answer.question_index for answer in retain_research(second, first).answers] == [2]
    assert retain_research(second, first).evidence_coverage == []


@pytest.mark.parametrize('change_search', [False, True])
async def test_recovery_returns_both_answers_when_the_second_round_omits_the_first(monkeypatch, change_search):
    import json
    from edge_portfolio.agent import run_analysis
    from test_agent import Evidence
    class Optimizer:
        instances = []
        def __init__(self, *args):
            self.instances.append(self)
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET'}
            self.batches = {'first', 'alternative'}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, opinions = [], []
    async def model(payload):
        role = stages[-1]
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question': content = {**content, 'decision': {'questions': ['First?', 'Second?']}}
        if role == 'opinion':
            opinions.append(1)
            if change_search and len(opinions) > 1:
                Optimizer.instances[0].result.update(revision=2)
            content['answers'] = [{'question_index': min(2, len(opinions)), 'status': 'answered', 'answer': 'This question has a complete answer.'}]
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상', question='First? Second?')
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence())
    assert len(opinions) == (3 if change_search else 2)
    assert result['completion']['status'] == ('partial' if change_search else 'no_match')
    assert (result['completion']['tasks'][0]['status'] == 'answered') is (not change_search)
    assert result['completion']['tasks'][1]['status'] == 'answered'


def test_explicit_research_correction_can_withdraw_a_previously_complete_answer():
    from edge_portfolio.completion import retain_research
    payload = final_payload()
    payload['answers'] = [{'question_index': 1, 'status': 'answered', 'answer': 'Previously considered answered.'}]
    payload['evidence_coverage'] = [{'instrument_id': 'US:VOO', 'field': 'fees', 'status': 'supported'}]
    previous = FinalReport.model_validate(payload)
    payload['answers'] = [{'question_index': 1, 'status': 'partial', 'answer': 'New research requires a correction.', 'missing': ['Updated fee schedule']}]
    payload['evidence_coverage'][0]['status'] = 'missing'
    corrected = retain_research(FinalReport.model_validate(payload), previous)
    assert corrected.answers[0].status == 'partial'
    assert corrected.answers[0].missing == ['Updated fee schedule']
    assert corrected.evidence_coverage[0].status == 'missing'


def test_single_labelled_fee_value_is_readable_product_evidence():
    from edge_portfolio.evidence_quality import inspect_body
    assert inspect_body('# Fund fees\nURL: https://issuer.example/fees\nAs of 2026-09-30\nTotal expense ratio: 0.03%', 24000)['status'] == 'available'
    assert inspect_body('# Navigation\nURL: https://issuer.example\nHome\nAbout\nContact', 24000)['status'] == 'unavailable'


async def test_server_constraint_rejection_triggers_alternatives_without_model_withholding_reason(monkeypatch):
    import json
    from edge_portfolio.agent import run_analysis
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'available', 'candidates': [{'candidate_id': 'c1'}], 'reason': None}
            self.batches = {'first'}
        def validate(self, final): return {'candidate_id': 'c1'} if final.recommendation else None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    monkeypatch.setattr('edge_portfolio.agent.check_constraints', lambda *args: [{'status': 'fail', 'reason': 'EXCLUDED_INSTRUMENT'}])
    stages, opinions, events = [], [], []
    async def model(payload):
        role = stages[-1]
        content = final_payload() if role == 'opinion' else final_payload()[role]
        if role == 'question': content = {**content, 'decision': {}}
        if role == 'opinion':
            opinions.append(1)
            if len(opinions) == 3: raise RuntimeError('ALTERNATIVE_REQUIRED')
            if len(opinions) == 2: content['recommendation'] = None
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(RuntimeError, match='ALTERNATIVE_REQUIRED'):
        await run_analysis(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), 'test', stages.append,
            model_call=model, evidence=Evidence(), on_event=events.append)
    assert any(event['event'] == 'research_recovery' for event in events)


def test_blank_answer_cannot_certify_a_customer_task_as_answered():
    from edge_portfolio.contracts import TaskAnswer
    with pytest.raises(ValueError, match='meaningful'):
        TaskAnswer(question_index=1, status='answered', answer=' ' * 10)
    with pytest.raises(ValueError, match='missing requirements'):
        TaskAnswer(question_index=1, status='answered', answer='The answer is incomplete.', missing=['funding amount'])
    from edge_portfolio.contracts import Section
    with pytest.raises(ValueError, match='Empty limitation'):
        Section.model_validate({**final_payload()['portfolio'], 'limitations': [' ']})
    for value in ['', ' ', '\n\t']:
        with pytest.raises(ValueError, match='Missing inputs must identify'):
            Decision(missing_inputs=[value])


def test_old_holdings_source_or_fetch_date_cannot_prove_current_coverage():
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    payload = final_payload()
    payload['evidence_coverage'] = [{'instrument_id': 'US:VOO', 'field': 'holdings', 'status': 'supported',
        'evidence': [{'source_id': 's1', 'passage_id': 'p1'}], 'claim_as_of': '2023-01-01'}]
    sources = [{'id': 's1', 'fetched_at': '2026-10-14', 'passages': [{'id': 'p1', 'text': 'Holdings as of 2023-01-01'}]}]
    result = assess_completion(FinalReport.model_validate(payload), request, Decision(),
        {'recommendation': {'allocations': [{'instrument_id': 'US:VOO'}]}}, sources, '2026-10-14')
    assert result['evidence_coverage'][0]['status'] == 'stale'
    assert result['status'] == 'partial'


def test_another_sources_date_cannot_make_an_undated_holdings_table_current():
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    payload = final_payload()
    payload['evidence_coverage'] = [{'instrument_id': 'US:VOO', 'field': 'holdings', 'status': 'supported',
        'claim_as_of': '2026-10-14', 'evidence': [{'source_id': 's1', 'passage_id': 'p1'}, {'source_id': 's2', 'passage_id': 'p1'}]}]
    sources = [{'id': 's1', 'passages': [{'id': 'p1', 'text': 'Market commentary as of 2026-10-14'}]},
               {'id': 's2', 'passages': [{'id': 'p1', 'text': 'Holdings: Apple 8%'}]}]
    def check():
        return assess_completion(FinalReport.model_validate(payload), request, Decision(),
            {'recommendation': {'allocations': [{'instrument_id': 'US:VOO'}]}}, sources, '2026-10-14')
    assert check()['evidence_coverage'][0]['status'] == 'date_unknown'
    sources[1]['passages'][0]['text'] += ' as of 2026-10-14'
    assert check()['evidence_coverage'][0]['status'] == 'supported'


def test_observed_history_overrides_the_initial_not_requested_state():
    market = SimpleNamespace(results={}, failures={})
    optimizer = SimpleNamespace(result=None)
    assert data_status(market, optimizer, [])['history'] == 'not_requested'
    optimizer.result = {'status': 'unavailable', 'reason': 'CURRENT_WEIGHTS_REQUIRED'}
    assert data_status(market, optimizer, [])['history'] == 'not_requested'
    market.results['portfolio'] = {'status': 'available'}
    assert data_status(market, optimizer, [])['history'] == 'available'
    assert data_status(market, optimizer, [])['forecast'] == 'not_estimated'
    market.results = {}
    optimizer.result = {'status': 'no_match', 'start_date': '2026-01-01', 'end_date': '2026-10-13'}
    assert data_status(market, optimizer, [])['history'] == 'available'
    market.failures = {'US:OTHER': 'PRICE_UNAVAILABLE'}
    assert data_status(market, optimizer, [])['history'] == 'partial'
    optimizer.result = None
    assert data_status(market, optimizer, [], {'history': 'available'})['history'] == 'partial'


def test_unavailable_comparison_is_not_evidence_that_no_candidate_qualifies():
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    result = assess_completion(FinalReport.model_validate({**final_payload(), 'recommendation': None}), request,
        Decision(), {'recommendation': None, 'optimization': {'status': 'unavailable', 'reason': 'BASELINE_PRICE_UNAVAILABLE'}}, [], '2026-10-14')
    assert result['status'] == 'partial'
    assert 'COMPARISON_DATA_UNAVAILABLE' in result['reasons']
    result = assess_completion(FinalReport.model_validate({**final_payload(), 'recommendation': None}), request,
        Decision(), {'recommendation': None, 'optimization': {'status': 'available', 'reason': 'RECOMMENDATION_WITHHELD'}}, [], '2026-10-14')
    assert result['status'] == 'partial'
    assert 'WITHHOLDING_REASON_MISSING' in result['reasons']


def test_unsuccessful_lookup_is_retained_even_without_cache_entries():
    market = SimpleNamespace(results={}, failures={})
    optimizer = SimpleNamespace(result=None)
    result = data_status(market, optimizer, [], {'sources': 'unavailable', 'history': 'unavailable'})
    assert result['sources'] == result['history'] == 'unavailable'


def test_explicit_written_dates_support_coverage_without_using_the_fetch_date():
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    payload = final_payload()
    payload['evidence_coverage'] = [{'instrument_id': 'US:VOO', 'field': 'holdings', 'status': 'supported',
        'evidence': [{'source_id': 's1', 'passage_id': 'p1'}, {'source_id': 's1', 'passage_id': 'p2'}], 'claim_as_of': '2026-09-30'}]
    for text in ['Holdings as of September 30, 2026', '30 Sep 2026', '2026년 9월 30일 보유내역', '2026/9/30', '09/30/2026', '30/09/2026', '2026. 09. 30.', 'Holdings as of 2026-09-30 16:00', '2026-09-30 005930 Samsung']:
        sources = [{'id': 's1', 'passages': [{'id': 'p1', 'text': text}, {'id': 'p2', 'text': '005930 Samsung Electronics 5%'}]}]
        result = assess_completion(FinalReport.model_validate(payload), request, Decision(),
            {'recommendation': {'allocations': [{'instrument_id': 'US:VOO'}]}}, sources, '2026-10-14')
        assert result['evidence_coverage'][0]['status'] == 'supported'
    sources[0]['passages'][0]['text'] = 'Published: 2026-09-30'
    result = assess_completion(FinalReport.model_validate(payload), request, Decision(),
        {'recommendation': {'allocations': [{'instrument_id': 'US:VOO'}]}}, sources, '2026-10-14')
    assert result['evidence_coverage'][0]['status'] != 'supported'
    payload['evidence_coverage'][0]['claim_as_of'] = '2026-10-10'
    sources[0]['passages'][0]['text'] = 'As of 10/10/2026'
    result = assess_completion(FinalReport.model_validate(payload), request, Decision(),
        {'recommendation': {'allocations': [{'instrument_id': 'US:VOO'}]}}, sources, '2026-10-14')
    assert result['evidence_coverage'][0]['status'] == 'supported'


@pytest.mark.parametrize('format_errors', [False, True])
async def test_missing_answer_reenters_research_without_consuming_format_repairs(monkeypatch, format_errors):
    import json
    from edge_portfolio.agent import run_analysis
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET'}
            self.batches = {'first', 'alternative'}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, final_calls, events = [], [], []
    async def model(payload):
        role = stages[-1]
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {'questions': ['어떤 대안을 확인했나요?']}}
        if role == 'opinion':
            final_calls.append(1)
            if format_errors and len(final_calls) in {1, 2, 4}:
                return {'choices': [{'message': {'role': 'assistant', 'content': '[]'}}]}
            if len(final_calls) > (3 if format_errors else 1):
                content['answers'] = [{'question_index': 1, 'status': 'answered', 'answer': '검토한 대안에서 서비스 비교 기준을 통과한 구성이 없어서 추천을 보류했어요.'}]
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상', question='어떤 대안을 확인했나요?')
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence(), on_event=events.append)
    assert len(final_calls) == (5 if format_errors else 2)
    assert result['completion']['status'] == 'no_match'
    assert any(event['event'] == 'research_recovery' for event in events)
    assert sum(event['event'] == 'validation_failed' for event in events) == (3 if format_errors else 0)


@pytest.mark.parametrize('missing_input', [False, True])
async def test_quality_withholding_requires_a_new_search_before_no_match(monkeypatch, missing_input):
    import json
    from edge_portfolio.agent import run_analysis
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET'}
            self.batches = {'first'}
        def validate(self, final): return None
        async def tool(self, args):
            assert args == {'combinations': [['new-alternative']]}
            self.batches.add('alternative')
            return self.result
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, opinions = [], []
    async def model(payload):
        role = stages[-1]
        if role == 'opinion':
            opinions.append(1)
            if len(opinions) == 2:
                assert 'ALTERNATIVE_SEARCH_NOT_PERFORMED' in payload['messages'][-1]['content']
                return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'alternative', 'type': 'function', 'function': {'name': 'optimize_portfolios', 'arguments': json.dumps({'combinations': [['new-alternative']]})}}]}}]}
        content = {**final_payload(), 'recommendation': None, 'withheld_reason': 'quality_failed'} if role == 'opinion' else final_payload()[role]
        if role == 'question': content = {**content, 'decision': {'missing_inputs': ['납입 금액이 필요해요.']} if missing_input else {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence())
    assert len(opinions) == (1 if missing_input else 3)
    assert result['completion']['status'] == ('needs_input' if missing_input else 'no_match')


async def test_truncated_output_recovers_without_discarding_prior_tool_evidence(monkeypatch):
    import json
    from edge_portfolio.agent import run_analysis
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CUSTOMER_POLICY_CONFLICT'}
            self.batches = {'first'}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, attempts = [], []
    async def model(payload):
        role = stages[-1]
        if role == 'opinion' and not attempts:
            attempts.append(1)
            return {'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': '{'}}]}
        if role == 'opinion':
            assert '길이 한도' in payload['messages'][-1]['content']
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question': content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence())
    assert result['sources'] == Evidence.sources
    assert result['completion']['status'] == 'no_match'
    assert any(event.get('code') == 'MODEL_TRUNCATED' for event in result['diagnostics']['events'])


@pytest.mark.parametrize('invalid', [None, []])
@pytest.mark.parametrize('policy_conflict', [True, False])
async def test_nonobject_source_free_response_uses_the_repair_path(monkeypatch, invalid, policy_conflict):
    import json
    from edge_portfolio.agent import run_analysis
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CUSTOMER_POLICY_CONFLICT' if policy_conflict else 'SERVICE_CRITERIA_NOT_MET'}
            self.batches = {'first'}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, broken = [], []
    async def model(payload):
        role = stages[-1]
        if role == 'portfolio' and not broken:
            broken.append(1)
            return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(invalid)}}]}
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'opinion':
            for name in ('question', 'portfolio', 'outlook', 'opinion'):
                content[name].update(evidence=[], limitations=['상품 자료를 확보하지 못했어요.'])
            content['current_evaluation'] = {'scores': None, 'reason': content['portfolio']}
            content['insights'][0]['content'] = content['portfolio']
        else:
            content.update(evidence=[], limitations=['상품 자료를 확보하지 못했어요.'])
        if role == 'question': content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    if not policy_conflict:
        with pytest.raises(ValueError, match='OUTPUT_INVALID'):
            await run_analysis(request, 'test', stages.append, model_call=model, evidence=SimpleNamespace(sources=[]))
        return  # A limitation cannot excuse research that was never attempted.
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=SimpleNamespace(sources=[]))
    assert any(event.get('code') == 'OUTPUT_CONTRACT' for event in result['diagnostics']['events'])


async def test_recovery_timeout_keeps_the_accepted_opinion_and_completion_checkpoint(monkeypatch):
    import json
    from copy import deepcopy
    from edge_portfolio.agent import run_analysis
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET'}
            self.batches = {'first', 'alternative'}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, opinions, snapshots = [], [], []
    async def model(payload):
        role = stages[-1]
        if role == 'opinion':
            opinions.append(1)
            if len(opinions) == 4: raise TimeoutError('recovery interrupted')
            if len(opinions) == 3:
                return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'catalog', 'type': 'function', 'function': {'name': 'search_etfs', 'arguments': '{"query":"VOO"}'}}]}}]}
        content = {**final_payload(), 'recommendation': None, 'withheld_reason': 'customer_failed'} if role == 'opinion' else final_payload()[role]
        if role == 'opinion' and len(opinions) == 2:
            content['evidence_coverage'] = [{'instrument_id': 'US:VOO', 'field': 'fees', 'status': 'supported',
                'claim_as_of': '2026-10-14', 'evidence': [{'source_id': 'rejected', 'passage_id': 'invalid'}]}]
        if role == 'question': content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상', question='어떤 대안을 검토했나요?')
    with pytest.raises(TimeoutError):
        await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence(), on_checkpoint=lambda value: snapshots.append(deepcopy(value)))
    assert 'opinion' in snapshots[-1]['sections']
    assert snapshots[-1]['completion']['status'] == 'partial'
    assert snapshots[-1]['completion']['tasks'][0]['question'] == request.question


@pytest.mark.parametrize('failed_urls,untried', [(1, False), (2, False), (2, True)])
async def test_one_failed_page_cannot_enable_source_free_research(monkeypatch, failed_urls, untried):
    import json
    from edge_portfolio.agent import run_analysis
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CRITERIA_NOT_MET'}
            self.batches = {'first', 'alternative'}
        def validate(self, final): return None
    class Evidence:
        sources = []
        discovered = {'https://issuer.example/untried'} if untried else set()
        async def call(self, *args): return {'status': 'unavailable', 'reason': 'EXA_FAILED'}
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, fetched = [], []
    async def model(payload):
        role = stages[-1]
        if role == 'portfolio' and not fetched:
            fetched.append(1)
            return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': str(i), 'type': 'function', 'function': {'name': 'fetch_source', 'arguments': json.dumps({'url': f'https://issuer.example/{i}'})}}
                for i in range(failed_urls)]}}]}
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'opinion':
            for name in ('question', 'portfolio', 'outlook', 'opinion'):
                content[name].update(evidence=[], limitations=['본문 자료를 확보하지 못했어요.'])
            content['current_evaluation'] = {'scores': None, 'reason': content['portfolio']}
            content['insights'][0]['content'] = content['portfolio']
        else:
            content.update(evidence=[], limitations=['본문 자료를 확보하지 못했어요.'])
        if role == 'question': content = {**content, 'decision': {}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    if failed_urls == 1 or untried:
        with pytest.raises(ValueError, match='OUTPUT_INVALID'):
            await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence())
    else:
        result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence())
        assert result['completion']['status'] == 'partial'
        assert 'MISSING_RESEARCH_DATA' in result['completion']['reasons']
