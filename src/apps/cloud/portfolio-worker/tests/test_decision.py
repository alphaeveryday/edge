import pytest

from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.decision import Decision, check_constraints, validate_decision


def request(**kwargs):
    return AnalysisInput(holdings=[], planning=True, horizon='10년 이상', **kwargs)


def test_customer_weight_cap_is_checked_on_aggregate_not_split_accounts():
    decision = Decision(constraints=[{'kind': 'max_weight', 'instrument_ids': ['US:VOO'],
        'value': 30, 'source': 'VOO는 30% 이하'}])
    rows = [{'instrument_id': 'US:VOO', 'weight_pct': 20, 'account': account} for account in ['일반 계좌', 'ISA']]
    checks = check_constraints(decision, rows, request(), {})
    assert checks[0]['status'] == 'fail'


def test_unverifiable_sector_constraint_can_never_pass_by_model_assertion():
    decision = Decision(constraints=[{'kind': 'unverified', 'source': '기술주 노출은 30% 이하'}])
    assert check_constraints(decision, [], request(), {})[0]['status'] == 'unknown'


def test_extracted_rules_must_quote_customer_input_and_use_registered_ids():
    decision = Decision(constraints=[{'kind': 'max_weight', 'instrument_ids': ['US:VOO'],
        'value': 30, 'source': 'VOO는 30% 이하'}])
    with pytest.raises(ValueError, match='CUSTOMER_CONSTRAINT_SOURCE'):
        validate_decision(decision, request(question='잘 분석해주세요'), {'US:VOO': {}})
    with pytest.raises(ValueError, match='UNREGISTERED'):
        validate_decision(decision, request(question='VOO는 30% 이하'), {})


def test_structured_account_selection_is_a_valid_customer_constraint_source():
    req = request(preferences={'accounts': ['ISA']})
    decision = Decision(constraints=[{'kind': 'accounts', 'accounts': ['ISA'], 'source': 'ISA'}])
    assert validate_decision(decision, req, {}) == decision


def test_no_sell_with_unknown_holdings_is_unknown_and_does_not_become_cash():
    req = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO'}], input_mode='names',
        horizon='10년 이상', question='매도 금지')
    decision = Decision(constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    assert check_constraints(decision, [{'instrument_id': 'US:VOO', 'weight_pct': 100}], req, {})[0]['status'] == 'unknown'


def test_planning_without_holdings_uses_explicit_new_cash_comparison():
    from edge_portfolio.market_data import MarketData
    market = MarketData(request())
    assert market.current == {'CASH:KRW': 1}
    assert market.baseline_kind == 'new_cash'


def test_no_sell_cannot_move_existing_positions_to_another_account():
    req = AnalysisInput(holdings=[{'name': '069500', 'instrument_id': 'KR:069500', 'amount_krw': 100, 'account': 'ISA'}], horizon='10년 이상')
    decision = Decision(constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    checks = check_constraints(decision, [{'instrument_id': 'KR:069500', 'weight_pct': 100, 'account': '일반 계좌'}], req, {})
    assert checks[0]['status'] == 'fail'


@pytest.mark.parametrize('kind,scope,status', [('max_weight', 'each', 'pass'), ('max_weight', 'combined', 'fail'),
                                              ('min_weight', 'each', 'fail'), ('min_weight', 'combined', 'pass')])
def test_individual_and_combined_weight_limits_have_distinct_meanings(kind, scope, status):
    decision = Decision(constraints=[{'kind': kind, 'instrument_ids': ['US:VOO', 'US:QQQ'],
                                     'value': 30, 'scope': scope, 'source': '고객 비중 조건'}])
    rows = [{'instrument_id': key, 'weight_pct': 20} for key in ['US:VOO', 'US:QQQ']]
    assert check_constraints(decision, rows, request(), {})[0]['status'] == status


def test_multiple_instrument_thresholds_require_explicit_scope():
    with pytest.raises(ValueError, match='scope=each or combined'):
        Decision(constraints=[{'kind': 'max_weight', 'instrument_ids': ['US:VOO', 'US:QQQ'], 'value': 30, 'source': '각각 30% 이하'}])


def test_no_sell_scope_does_not_prohibit_changes_to_other_holdings():
    req = AnalysisInput(holdings=[{'name': key, 'instrument_id': key, 'amount_krw': amount}
        for key, amount in [('US:VOO', 50), ('US:QQQ', 40), ('CASH:KRW', 10)]], horizon='10년 이상')
    rows = [{'instrument_id': key, 'weight_pct': 50, 'account': '일반 계좌'} for key in ['US:VOO', 'US:VTI']]
    scoped = Decision(constraints=[{'kind': 'no_sell', 'instrument_ids': ['US:VOO'], 'source': 'VOO 매도 금지'}])
    assert check_constraints(scoped, rows, req, {})[0]['status'] == 'pass'
    global_rule = Decision(constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    assert check_constraints(global_rule, rows, req, {})[0]['status'] == 'fail'


def test_repeated_holdings_do_not_fail_no_sell_due_to_fractional_percentage_rounding():
    req = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}] * 3
        + [{'name': 'QQQ', 'instrument_id': 'US:QQQ', 'amount_krw': 3000000}], horizon='10년 이상')
    decision = Decision(constraints=[{'kind': 'no_sell', 'instrument_ids': ['US:VOO'], 'source': 'VOO 매도 금지'}])
    rows = [{'instrument_id': key, 'weight_pct': 50, 'account': '일반 계좌'} for key in ['US:VOO', 'US:QQQ']]
    assert check_constraints(decision, rows, req, {})[0]['status'] == 'pass'


@pytest.mark.parametrize('tool_name', ['search_etfs', 'fetch_source'])
async def test_question_stage_resolves_registered_ids_without_external_research(monkeypatch, tool_name):
    import json
    from edge_portfolio.agent import run_analysis
    from report_fixtures import final_payload
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args): self.result = {'status': 'no_match', 'candidates': []}
        def validate(self, final): return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages, calls = [], []
    async def model(payload):
        role = stages[-1]
        if role == 'question':
            assert [tool['function']['name'] for tool in payload['tools']] == ['search_etfs']
            if not calls:
                calls.append(1)
                return {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{'id': 'identify', 'type': 'function',
                    'function': {'name': tool_name, 'arguments': json.dumps({'query': 'VOO'})}}]}}]}
            answer = json.loads(payload['messages'][-1]['content'])
            if tool_name == 'search_etfs':
                assert answer[0]['instrument_id'] == 'US:VOO'
            else:
                assert answer['reason'] == 'QUESTION_STAGE_CATALOG_ONLY'
        content = {**final_payload(), 'recommendation': None} if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {'constraints': [{'kind': 'max_weight', 'instrument_ids': ['US:VOO'], 'value': 30, 'source': 'VOO는 30% 이하'}]}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    result = await run_analysis(request(question='VOO는 30% 이하'), 'test-only', stages.append, model_call=model, evidence=Evidence())
    assert result['decision']['constraints'][0]['kind'] == 'max_weight'


@pytest.mark.parametrize('reject_first,unverified', [(True, False), (False, False), (False, True)])
async def test_service_eligible_candidate_cannot_publish_after_customer_constraint_failure(monkeypatch, reject_first, unverified):
    import json
    from edge_portfolio.agent import run_analysis
    from report_fixtures import final_payload
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
        def validate(self, final):
            return self.result['candidates'][0] if final.recommendation else None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    phases, repairs = [], []
    async def model(payload):
        role = phases[-1]
        content = final_payload() if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {'kind': 'rebalance', 'constraints': [
                {'kind': 'unverified' if unverified else 'max_weight', 'instrument_ids': ['US:VOO'], 'value': 30, 'source': 'VOO는 30% 이하'}]}}
        if role == 'opinion':
            if repairs or not reject_first:
                if repairs:
                    assert 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' in payload['messages'][-1]['content']
                content['recommendation'] = None
            repairs.append(1)
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    result = await run_analysis(request(question='VOO는 30% 이하'), 'test-only', phases.append,
                                model_call=model, evidence=Evidence())
    assert len(repairs) == (2 if reject_first else 1)
    assert result['recommendation'] is None
    assert result['customer_constraint_checks'][0]['status'] == ('fail' if reject_first else 'unknown')
    assert result['optimization']['reason'] == ('CUSTOMER_CONSTRAINT_NOT_SATISFIED' if reject_first or unverified else 'RECOMMENDATION_WITHHELD')
    assert result['input']['question'] == 'VOO는 30% 이하'
