from copy import deepcopy
import pytest

from edge_portfolio.followup import answer_question


async def test_followup_reuses_research_checks_without_overwriting_the_original(monkeypatch):
    report = {'input': {'holdings': [{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}],
        'horizon': '10년 이상', 'question': '매도 금지'}, 'as_of': '2026-10-12',
        'current_evaluation': {'overall': 3.5}, 'recommendation': {'title': 'saved target'},
        'decision': {'constraints': [{'kind': 'no_sell', 'source': '매도 금지'}]}}
    original = deepcopy(report)
    async def analyze(request, key, on_stage, **kwargs):
        assert request.holdings[0].amount_krw == 1000000
        assert '매도 금지' in request.question
        assert kwargs['followup_question'] == '리밸런싱은 어떻게 해요?'
        assert kwargs['prior_report']['current_evaluation']['overall'] == 3.5
        kwargs['prior_report']['recommendation']['title'] = 'changed in isolated context'
        return {'sections': {'opinion': {'paragraphs': ['answer']}}, 'sources': [],
            'as_of': '2026-10-14', 'completion': {'status': 'needs_input'}, 'elapsed_seconds': 1, 'diagnostics': {}}
    monkeypatch.setattr('edge_portfolio.followup.run_analysis', analyze)
    result = await answer_question('리밸런싱은 어떻게 해요?', report, 'test', revision_id='revision-1')
    assert report == original
    assert result['parent_as_of'] == '2026-10-12'
    assert result['as_of'] == '2026-10-14'
    assert result['status'] == 'needs_input'
    assert result['revision_id'] == 'revision-1'
    assert result['revision']['sections']['opinion'] == result['answer']


async def test_full_length_questions_keep_both_customer_messages(monkeypatch):
    report = {'input': {'holdings': [], 'planning': True, 'horizon': '10년 이상', 'question': 'a' * 1000}}
    async def analyze(request, *args, **kwargs):
        assert 'a' * 1000 in request.question and 'b' * 1000 in request.question
        return {'sections': {'opinion': {}}, 'sources': [], 'as_of': '2026-10-14',
            'completion': {'status': 'partial'}, 'elapsed_seconds': 1, 'diagnostics': {}}
    monkeypatch.setattr('edge_portfolio.followup.run_analysis', analyze)
    assert (await answer_question('b' * 1000, report, 'test'))['status'] == 'partial'


@pytest.mark.parametrize('has_principal,supplied,retained,withdrawn', [(True, False, False, False), (False, False, False, False), (False, True, False, False), (False, False, True, False), (False, False, True, True)])
async def test_scenario_followup_uses_verified_principal_and_retains_original_constraints(monkeypatch, has_principal, supplied, retained, withdrawn):
    import json
    from report_fixtures import final_payload
    from test_agent import Evidence
    stages, calculated = [], []
    question = '5년 10년 15년 후 금액을 가정별로 계산해 주세요.'
    if supplied:
        question = '원금은 1000만원입니다. ' + question
    if withdrawn:
        question = '앞서 말한 원금은 취소합니다. ' + question
    report = {'input': {'holdings': [{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}],
        'horizon': '10년 이상', 'question': '매도 금지'}, 'as_of': '2026-10-12',
        'sources': Evidence.sources, 'decision': {'constraints': [{'kind': 'no_sell', 'source': '매도 금지'}]}}
    if not has_principal:
        report['input'].update(holdings=[], planning=True, preferences={'contribution': '매월 10만원'})
    if retained:
        report['input']['question'] = '원금은 1000만원입니다. 매도 금지'
    if withdrawn:
        report['input']['preferences']['contribution'] = ''
    report['recommendation'] = {'allocations': [], 'historical_performance': {
        'points': [{'date': '2000-01-01', 'recommended': 1000000, 'current': 1000000}] * 10000}}
    async def model(payload):
        role = stages[-1]
        assert 'historical_performance' not in json.loads(payload['messages'][1]['content'])['prior_report']['recommendation']
        if role == 'portfolio' and not calculated:
            calculated.append(1)
            context = json.loads(payload['messages'][1]['content'])
            assert context['active_decision']['constraints'][0]['kind'] == 'no_sell'
            return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 'scenario', 'type': 'function', 'function': {'name': 'calculate_scenarios',
                    'arguments': json.dumps({'annual_returns': [0], 'years': [5, 10, 15]})}}]}}]}
        if role == 'outlook' and len(calculated) == 1:
            calculated.append(2)
            return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [{
                'id': 'comparison', 'type': 'function', 'function': {'name': 'calculate_scenarios',
                    'arguments': json.dumps({'annual_returns': [3], 'years': [5]})}}]}}]}
        content = {**final_payload(), 'recommendation': None,
            'answers': [{'question_index': 1, 'status': 'answered', 'answer': '수익률이 0%이고 추가 납입이 없다면 각 기간의 금액은 현재 원금과 같아요.'}]} if role == 'opinion' else final_payload()[role]
        if role == 'opinion':
            for name in ('question', 'portfolio', 'outlook', 'opinion'):
                content[name].update(evidence=[], limitations=['수익률이 일정하다는 계산 가정이에요.'])
            content['current_evaluation'] = {'scores': None, 'reason': content['portfolio']}
            content['insights'][0]['content'] = content['portfolio']
        else:
            content.update(evidence=[], limitations=['수익률이 일정하다는 계산 가정이에요.'])
        if role == 'question': content = {**content, 'decision': {'kind': 'scenario'}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    async def forbidden_price_lookup(*args): raise AssertionError('A cash-flow scenario needs no new ETF comparison')
    monkeypatch.setattr('edge_portfolio.market_data.MarketData.get', forbidden_price_lookup)
    def forbidden_evidence_connection(*args, **kwargs): raise AssertionError('Arithmetic must not require an Exa connection')
    monkeypatch.setattr('edge_portfolio.agent.streamable_http_client', forbidden_evidence_connection)
    result = await answer_question(question, report, 'test', model_call=model, on_stage=stages.append)
    assert result['status'] == ('complete' if (has_principal or supplied or retained) and not withdrawn else 'needs_input')
    assert [row['future_krw'] for row in result['revision']['scenarios']['rows'] if row['annual_return_pct'] == '0'] == ([] if withdrawn else ['16000000', '22000000', '28000000'] if supplied or retained else ['1000000'] * 3 if has_principal else [])
    if withdrawn:
        assert result['revision']['scenarios']['reason'] == 'CURRENT_AMOUNTS_REQUIRED'
    assert result['revision']['decision']['constraints'][0]['kind'] == 'no_sell'
    assert result['revision']['recommendation'] is None


def test_projection_horizon_does_not_cancel_a_separate_monthly_commitment():
    from edge_portfolio.followup import FollowupInput
    from edge_portfolio.execution import resolve_funding
    from edge_portfolio.decision import Decision
    from decimal import Decimal
    request = FollowupInput(holdings=[], planning=True, horizon='10년 이상',
        question='5년 뒤 금액을 가정별로 계산해 주세요', funding_questions=['', '5년 뒤 금액을 가정별로 계산해 주세요'],
        preferences={'contribution': '매월 10만원'})
    decision = Decision(kind='scenario', funding_text='매월 10만원')
    assert resolve_funding(request, decision)['monthly_krw'] == Decimal(100000)
    request.funding_questions = ['', '연 5% 수익률을 가정하면 10년 뒤 얼마인가요?']
    assert resolve_funding(request, decision)['monthly_krw'] == Decimal(100000)
    request.funding_questions = ['', '납입은 3개월만 할게요. 5년 뒤 금액을 계산해 주세요.']
    assert resolve_funding(request, decision) is None
    request.funding_questions = ['', '투자는 3년 후에 시작할 계획이고, 5년 후 자산을 계산해 주세요.']
    assert resolve_funding(request, decision) is None


@pytest.mark.parametrize('available', [False, True])
async def test_saved_target_is_checked_without_aborting_recovery(monkeypatch, available):
    import json
    from report_fixtures import final_payload
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'no_match', 'candidates': [], 'reason': 'SERVICE_CUSTOMER_POLICY_CONFLICT'}
            self.batches = set()
        def validate(self, final): return None
    if not available:
        monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    question = '기존 목표로 리밸런싱하려면 어떻게 해야 하나요?'
    report = {'input': {'holdings': [{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}],
        'horizon': '10년 이상'}, 'sources': Evidence.sources, 'completion': {'status': 'complete'}, 'recommendation': {'allocations': [
        {'instrument_id': 'US:VOO' if available else 'US:REMOVED', 'account': '일반 계좌', 'weight_pct': 100}]}}
    stages = []
    async def model(payload):
        role = stages[-1]
        if role != 'question':
            context = json.loads(payload['messages'][1]['content'])
            assert context['saved_target_execution']['status'] == ('ready' if available else 'needs_input')
        content = {**final_payload(), 'recommendation': None, 'answers': [
            {'question_index': 1, 'status': 'answered', 'answer': '기존 목표의 종목을 사용할 수 없어 새로운 대안을 확인해야 해요.'}]} if role == 'opinion' else final_payload()[role]
        if role == 'question': content = {**content, 'decision': {'kind': 'rebalance', 'execute_saved_target': True}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    result = await answer_question(question, report, 'test', model_call=model, evidence=Evidence(), on_stage=stages.append)
    if not available:
        assert result['revision']['execution']['reasons'] == ['SAVED_TARGET_UNAVAILABLE']
        assert result['revision']['execution']['rows'] == []
        assert result['revision']['execution']['assumptions'] == []
    assert result['status'] == ('complete' if available else 'needs_input')
    assert 'opinion' in stages


@pytest.mark.parametrize('parent_state,completion,execute_saved', [('failed', 'complete', True), ('succeeded', 'partial', True), ('succeeded', 'complete', False)])
async def test_tentative_or_replacement_target_cannot_skip_research(parent_state, completion, execute_saved):
    import json
    from report_fixtures import final_payload
    from test_agent import Evidence
    report = {'input': {'holdings': [{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}],
        'horizon': '10년 이상'}, 'sources': Evidence.sources, 'parent_state': parent_state,
        'completion': {'status': completion, 'missing_research': [{'instrument_id': 'US:VOO', 'field': 'holdings'}]},
        'recommendation': {'allocations': [{'instrument_id': 'US:VOO', 'account': '일반 계좌', 'weight_pct': 100}]}}
    stages = []
    async def model(payload):
        role = stages[-1]
        content = final_payload()[role]
        if role == 'question': content = {**content, 'decision': {'kind': 'rebalance', 'execute_saved_target': execute_saved}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(ValueError, match='Before completing portfolio'):
        await answer_question('기존 목표를 실행해도 되나요?' if execute_saved else '새로운 대안을 추천해 주세요.', report, 'test', model_call=model, evidence=Evidence(), on_stage=stages.append)


async def test_followup_retains_twenty_existing_rules_and_adds_new_distinct_rules():
    import json
    from report_fixtures import final_payload
    from test_agent import Evidence
    rules = [{'kind': 'unverified', 'source': f'기존 필수 조건 {i}'} for i in range(20)]
    report = {'input': {'holdings': [], 'planning': True, 'horizon': '10년 이상',
        'question': '\n'.join(rule['source'] for rule in rules)}, 'decision': {'constraints': rules}, 'sources': Evidence.sources}
    stages = []
    async def model(payload):
        if stages[-1] != 'question':
            active = json.loads(payload['messages'][1]['content'])['active_decision']['constraints']
            assert len(active) == 21
            assert {rule['source'] for rule in active} == {*(rule['source'] for rule in rules), '새 필수 조건'}
            raise RuntimeError('MERGE_CHECKED')
        content = {**final_payload()['question'], 'decision': {'kind': 'scenario', 'constraints': [rules[0], {'kind': 'unverified', 'source': '새 필수 조건'}]}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(RuntimeError, match='MERGE_CHECKED'):
        await answer_question('새 필수 조건', report, 'test', model_call=model, evidence=Evidence(), on_stage=stages.append)


async def test_distinct_followup_questions_keep_separate_completion_tasks():
    import json
    from report_fixtures import final_payload
    from test_agent import Evidence
    original, first, second = '처음 질문은 무엇인가요?', '5년 뒤 금액은 얼마인가요?', '10년 뒤 금액은 얼마인가요?'
    report = {'input': {'holdings': [], 'planning': True, 'horizon': '10년 이상', 'question': original}, 'sources': Evidence.sources}
    stages = []
    async def model(payload):
        if stages[-1] != 'question':
            assert json.loads(payload['messages'][1]['content'])['active_decision']['questions'] == [first, second]
            raise RuntimeError('QUESTIONS_CHECKED')
        content = {**final_payload()['question'], 'decision': {'kind': 'scenario', 'questions': [original, first, second]}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(RuntimeError, match='QUESTIONS_CHECKED'):
        await answer_question(first + ' ' + second, report, 'test', model_call=model, evidence=Evidence(), on_stage=stages.append)


@pytest.mark.parametrize('same_source', [True, False])
async def test_recovery_resolves_only_the_exact_inherited_unknown_condition(same_source):
    import json
    from report_fixtures import final_payload
    from test_agent import Evidence
    original, other = 'VOO를 반드시 포함해 주세요.', '다른 조건도 지켜 주세요.'
    report = {'input': {'holdings': [], 'planning': True, 'horizon': '10년 이상', 'question': original + other},
        'decision': {'constraints': [{'kind': 'unverified', 'source': original}, {'kind': 'unverified', 'source': other}]},
        'sources': Evidence.sources}
    stages = []
    followup = 'VOO는 미국 Vanguard S&P 500 ETF입니다.'
    async def model(payload):
        if stages[-1] != 'question':
            rules = json.loads(payload['messages'][1]['content'])['active_decision']['constraints']
            assert len(rules) == (2 if same_source else 3)
            unknown = {rule['source'] for rule in rules if rule['kind'] == 'unverified'}
            assert unknown == ({other} if same_source else {original, other})
            assert any(rule['kind'] == 'require' and rule['instrument_ids'] == ['US:VOO'] for rule in rules)
            raise RuntimeError('RESOLUTION_CHECKED')
        content = {**final_payload()['question'], 'decision': {'kind': 'scenario', 'constraints': [
            {'kind': 'require', 'source': original if same_source else followup, 'instrument_ids': ['US:VOO']}]}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(RuntimeError, match='RESOLUTION_CHECKED'):
        await answer_question(followup, report, 'test', model_call=model, evidence=Evidence(), on_stage=stages.append)
