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


@pytest.mark.parametrize('reject_first', [True, False])
async def test_service_eligible_candidate_cannot_publish_after_customer_constraint_failure(monkeypatch, reject_first):
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
                {'kind': 'max_weight', 'instrument_ids': ['US:VOO'], 'value': 30, 'source': 'VOO는 30% 이하'}]}}
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
    assert result['optimization']['reason'] == 'CUSTOMER_CONSTRAINT_NOT_SATISFIED'
    assert result['input']['question'] == 'VOO는 30% 이하'
