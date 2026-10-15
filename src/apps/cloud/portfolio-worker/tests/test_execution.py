from decimal import Decimal
import pytest

from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.execution import build_execution, parse_funding


def request(holdings, **kwargs):
    return AnalysisInput(holdings=[{'name': key, 'instrument_id': key, 'amount_krw': amount,
                                  'kind': 'cash' if key == 'CASH:KRW' else 'etf', 'account': account}
                                 for key, amount, account in holdings], horizon='10년 이상', **kwargs)


def target(**weights):
    return [{'instrument_id': key, 'account': '일반 계좌', 'weight_pct': value} for key, value in weights.items()]


def test_projection_rejection_cannot_block_the_worker_with_regex_backtracking():
    import subprocess
    import sys
    cases = ['5년 뒤 ' + '계산해' * 60 + 'X', ' ' * 900 + 'X']
    code = 'from edge_portfolio.execution import projection_question\n' + '\n'.join(
        f'assert not projection_question({value!r})' for value in cases)
    subprocess.run([sys.executable, '-X', 'utf8', '-c', code], check=True, capture_output=True, timeout=10)


@pytest.mark.parametrize('principal', ['', '원금은 1000만원입니다. '])
def test_initial_scenario_explicit_zero_overrides_saved_monthly_preference(principal):
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import resolve_funding
    req = request([('US:VOO', 1000000, '일반 계좌')],
        preferences={'contribution': '매월 10만원'},
        question=principal + '이번에는 추가 납입 없이 계산해 주세요. 5년 뒤 금액을 알려 주세요.')
    assert resolve_funding(req, Decision(kind='scenario')) == {'additional_krw': Decimal(0), 'monthly_krw': None}


def test_changed_funding_is_not_silently_replaced_by_an_older_zero_instruction():
    from edge_portfolio.decision import Decision
    from edge_portfolio.followup import FollowupInput
    from edge_portfolio.execution import zero_funding_quote
    req = FollowupInput(holdings=[], planning=True, horizon='10년 이상', question='매월 20만원 넣을게요.',
        funding_questions=['추가 납입 없이 계산해 주세요.', '매월 20만원 넣을게요. 10년 뒤 금액을 알려 주세요.'])
    assert zero_funding_quote(req, Decision(kind='scenario')) is None


@pytest.mark.parametrize('original', ['매달 10만원', '추가 납입 없이 계산해 주세요.'])
@pytest.mark.parametrize('qualification', ['', '납입은 3개월만 가능합니다.'])
def test_explicit_latest_monthly_amount_replaces_an_unconditional_prior_commitment(original, qualification):
    from edge_portfolio.decision import Decision
    from edge_portfolio.followup import FollowupInput
    from edge_portfolio.execution import resolve_funding
    question = '매달 20만원으로 계산해 주세요. 5년 뒤 금액을 알려 주세요.'
    req = FollowupInput(holdings=[], planning=True, horizon='10년 이상', question=question,
        funding_questions=[original, question], preferences={'contribution': '매달 10만원', 'note': qualification})
    result = resolve_funding(req, Decision(kind='scenario', funding_text='매달 20만원'))
    assert result is None if qualification else result == {'additional_krw': Decimal(0), 'monthly_krw': Decimal(200000)}


def test_rebalance_amounts_conserve_cash_and_explain_actual_buys_and_sales():
    req = request([('US:VOO', 600001, '일반 계좌'), ('US:QQQ', 200000, '일반 계좌'), ('CASH:KRW', 200000, '일반 계좌')])
    plan = build_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), additional_krw=100000)
    assert plan['status'] == 'ready'
    assert sum(int(row['target_krw']) for row in plan['rows']) == 1100001
    assert sum(int(row['buy_krw']) - int(row['sell_krw']) for row in plan['rows']) == 300000
    assert next(row for row in plan['rows'] if row['instrument_id'] == 'US:VOO')['sell_krw'] == '50000'


def test_zero_target_cannot_be_reached_with_contributions_and_no_sale():
    req = request([('US:VOO', 1000000, '일반 계좌')])
    plan = build_execution(req, target(**{'US:QQQ': 100}), no_sell=True, monthly_krw=100000)
    assert plan['status'] == 'infeasible'
    assert 'NO_SELL_ZERO_TARGET' in plan['reasons']
    assert plan['months'] is None


def test_scoped_no_sale_allows_other_positions_to_be_replaced():
    req = request([('US:VOO', 500000, '일반 계좌'), ('US:QQQ', 500000, '일반 계좌')])
    plan = build_execution(req, target(**{'US:VOO': 50, 'US:VTI': 50}), no_sell=True, no_sell_ids={'US:VOO'})
    assert plan['status'] == 'ready'
    assert next(row for row in plan['rows'] if row['instrument_id'] == 'US:QQQ')['sell_krw'] == '500000'


def test_monthly_only_rebalance_states_required_cash_and_constant_price_assumption():
    req = request([('US:VOO', 1000000, '일반 계좌')])
    plan = build_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), no_sell=True, monthly_krw=200000)
    assert plan['status'] == 'conditional'
    assert plan['required_additional_krw'] == '999999'  # Whole-won allocation preserves the existing 1,000,000.
    assert plan['months'] == 5
    assert all(row['sell_krw'] == '0' for row in plan['rows'])
    assert plan['assumptions']


def test_new_portfolio_monthly_amount_is_an_explicit_first_installment():
    req = request([], planning=True)
    plan = build_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), monthly_krw=100000)
    assert plan['status'] == 'conditional'
    assert plan['months'] == 1
    assert sum(int(row['buy_krw']) for row in plan['rows']) == 100000
    assert '첫 월 납입금' in plan['assumptions'][-1]


def test_monthly_contribution_serialized_by_the_input_form_is_executable():
    from edge_portfolio.execution import plan_execution
    from edge_portfolio.decision import Decision
    req = request([], planning=True, preferences={'contribution': '매달 꾸준히 100만 원'})
    for decision in (Decision(), Decision(funding_text='매달 꾸준히 100만 원')):
        plan = plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)
        assert plan['status'] == 'conditional'
        assert plan['monthly_krw'] == '1000000'
        assert sum(int(row['buy_krw']) for row in plan['rows']) == 1000000
    req.preferences.interests = ['추가 납입은 취소했습니다.']
    assert plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), Decision())['status'] == 'needs_input'
    req.preferences.interests = []
    for refusal in ('추가 납입은 안 합니다.', '추가 납입을 못 합니다.', '추가 납입은 하지 않겠습니다.', '추가 투자금은 안 넣습니다.'):
        req.question = refusal + ' 매도 금지.'
        plan = plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), Decision())
        assert plan['status'] == 'needs_input', refusal
        assert plan['rows'] == []


def test_percent_input_does_not_become_krw_principal():
    req = request([('US:VOO', 100, '일반 계좌')], input_mode='percent')
    plan = build_execution(req, target(**{'US:QQQ': 100}))
    assert plan['status'] == 'needs_input'
    assert plan['rows'] == []
    assert 'CURRENT_AMOUNTS_REQUIRED' in plan['reasons']


def test_unknown_account_limit_does_not_become_an_executable_deposit():
    req = request([('CASH:KRW', 1000000, '일반 계좌')], preferences={'accounts': ['ISA']})
    plan = build_execution(req, [{'instrument_id': 'KR:069500', 'account': 'ISA', 'weight_pct': 100}])
    assert plan['status'] == 'needs_input'
    assert 'ACCOUNT_OPENING_REQUIRED:ISA' in plan['reasons']
    assert 'ACCOUNT_CONTRIBUTION_LIMIT_REQUIRED:ISA' in plan['reasons']


def test_funding_extraction_does_not_guess_between_monthly_and_one_time_money():
    assert parse_funding('매월 100만 원') == {'monthly_krw': Decimal(1000000), 'additional_krw': Decimal(0)}
    assert parse_funding('추가로 1,000만원') == {'monthly_krw': None, 'additional_krw': Decimal(10000000)}
    assert parse_funding('100만 원') is None
    assert parse_funding('매월 100만원 또는 200만원') is None
    assert parse_funding('추가 -100만원') is None
    assert parse_funding('추가 1e6원') is None
    assert parse_funding('추가 약100만원') is None
    assert parse_funding('추가 1억 5,000만 원') == {'monthly_krw': None, 'additional_krw': Decimal(150000000)}
    assert parse_funding('추가 100만 5000원') == {'monthly_krw': None, 'additional_krw': Decimal(1005000)}
    assert parse_funding('추가 3천 500만원') is None
    assert parse_funding('매월 2회 100만원') is None
    for qualifier in ['내외', '가량', '쯤', '정도', '예상', '한도', '미만']:
        assert parse_funding('추가 100만원 ' + qualifier) is None


def test_known_remaining_contribution_limit_is_enforced_without_inventing_tax_rules():
    req = request([('CASH:KRW', 1000000, 'ISA')], preferences={'accounts': ['ISA'], 'tax_profile': {
        'residence': '대한민국 거주자', 'age': '19~54세', 'income': '확인 필요', 'financial_income_tax': '확인 필요',
        'accounts': [{'account': 'ISA', 'subtype': '중개형', 'remaining_man': '10'}]}})
    plan = build_execution(req, [{'instrument_id': 'KR:069500', 'account': 'ISA', 'weight_pct': 100}], additional_krw=200000)
    assert plan['status'] == 'infeasible'
    assert plan['reasons'] == ['ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:ISA']


def test_same_account_existing_cash_does_not_consume_new_contribution_allowance():
    req = request([('CASH:KRW', 1000000, 'ISA')])
    plan = build_execution(req, [{'instrument_id': 'KR:069500', 'account': 'ISA', 'weight_pct': 100}])
    assert plan['status'] == 'ready'
    assert plan['accounts'][0]['deposit_krw'] == '0'


def test_whole_won_allocation_conserves_money_for_tiny_and_large_principals():
    for principal in (1, 7, 101, 10000000000000):
        req = request([('CASH:KRW', principal, '일반 계좌')])
        plan = build_execution(req, target(**{'US:VOO': 33.33, 'US:QQQ': 33.33, 'US:VTI': 33.34}))
        assert sum(int(row['target_krw']) for row in plan['rows']) == principal
        assert sum(int(row['buy_krw']) for row in plan['rows']) == principal


def test_funded_no_sale_is_checked_on_amounts_not_diluted_weights():
    from edge_portfolio.decision import Decision, check_constraints
    req = request([('US:VOO', 1000000, '일반 계좌')], question='추가 100만원 매도 금지')
    decision = Decision(funding_text='추가 100만원', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    rows = target(**{'US:VOO': 50, 'US:QQQ': 50})
    assert check_constraints(decision, rows, req, {})[0]['status'] == 'pass'
    decision.funding_text = '추가 10만원'
    req.question = '추가 10만원 매도 금지'
    assert check_constraints(decision, rows, req, {})[0]['status'] == 'fail'
    decision.funding_text = '추가 금액 미정'
    assert check_constraints(decision, rows, req, {})[0]['status'] == 'unknown'


def test_model_cannot_trim_an_approximate_funding_quote_into_a_commitment():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    for question in ['추가 100만원 정도', '추가 100만원 가량', '추가 100만원 넣지 않을 거예요', '추가 100만원에서 200만원 사이로 넣을 예정입니다.', '추가 100만원을 넣고 싶지만 지금은 자금이 없어요.']:
        req = request([('CASH:KRW', 1000000, '일반 계좌')], question=question)
        result = plan_execution(req, target(**{'US:VOO': 100}), Decision(funding_text='추가 100만원'))
        assert result['status'] == 'needs_input'
        assert result['rows'] == []


def test_account_readiness_does_not_change_the_separate_no_sale_verdict():
    from edge_portfolio.decision import Decision, check_constraints
    from edge_portfolio.execution import plan_execution
    req = request([('CASH:KRW', 1000000, '일반 계좌')], preferences={'accounts': ['ISA']})
    decision = Decision(constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    rows = [{'instrument_id': 'KR:069500', 'account': 'ISA', 'weight_pct': 100}]
    assert plan_execution(req, rows, decision)['status'] == 'needs_input'
    assert check_constraints(decision, rows, req, {})[0]['status'] == 'pass'


def test_finite_monthly_commitment_cannot_become_unlimited_contributions():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    for question in ['매월 10만원씩 3개월만 납입', '세 달 동안 매월 10만원씩', '매월 10만원씩 연말까지']:
        req = request([('US:VOO', 1000000, '일반 계좌')], question=question)
        decision = Decision(funding_text='매월 10만원씩', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
        plan = plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)
        assert plan['status'] == 'needs_input'
        assert plan['months'] is None


def test_whole_won_rounding_does_not_reject_an_unchanged_no_sale_portfolio():
    req = request([('US:VOO', 500001, '일반 계좌'), ('US:QQQ', 500000, '일반 계좌')])
    plan = build_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), no_sell=True)
    assert plan['status'] == 'ready'
    assert plan['required_additional_krw'] == '0'
    assert all(row['sell_krw'] == row['buy_krw'] == '0' for row in plan['rows'])


def test_competing_funding_statements_are_not_silently_reduced_to_one_option():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    req = request([('CASH:KRW', 1000000, '일반 계좌')], question='추가 100만원. 또는 매월 10만원.')
    assert plan_execution(req, target(**{'US:VOO': 100}), Decision(funding_text='추가 100만원'))['status'] == 'needs_input'


def test_allocation_percentages_are_not_mistaken_for_payment_duration():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    req = request([('US:VOO', 1000000, '일반 계좌'), ('CASH:KRW', 1000000, '일반 계좌')],
                  question='VOO 50%, QQQ 50%로 구성하고 추가 100만원. 매도 금지.')
    decision = Decision(funding_text='추가 100만원', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    assert plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)['status'] == 'ready'


def test_refusing_to_sell_does_not_negate_a_separate_funding_commitment():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    req = request([('US:VOO', 1000000, '일반 계좌')], question='추가 100만원을 넣고 VOO는 팔지 않을게요.')
    decision = Decision(funding_text='추가 100만원', constraints=[{'kind': 'no_sell', 'source': '팔지 않을게요'}])
    result = plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)
    assert result['status'] == 'ready'
    assert all(row['sell_krw'] == '0' for row in result['rows'])


def test_first_monthly_installment_is_also_checked_for_rounding_preservation():
    req = request([('US:QQQ', 1, '일반 계좌'), ('US:VTI', 1, '일반 계좌'), ('US:VOO', 2, '일반 계좌')])
    plan = build_execution(req, target(**{'US:QQQ': 30, 'US:VTI': 10, 'US:VOO': 60}), no_sell=True, monthly_krw=1)
    assert plan['status'] == 'conditional'
    assert plan['months'] == 2
    assert all(row['sell_krw'] == '0' for row in plan['rows'])


def test_conditional_income_is_not_spendable_even_when_the_quote_omits_it():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    for prefix in ['보너스를 받으면', '돈이 생기는 경우', '대출 승인 후에는']:
        req = request([('US:VOO', 1000000, '일반 계좌')], question=f'{prefix} 추가 100만원을 넣겠습니다. 매도 금지.')
        decision = Decision(funding_text='추가 100만원', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
        result = plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)
        assert result['status'] == 'needs_input'
        assert result['rows'] == []


def test_funding_restrictions_survive_sentence_and_input_field_boundaries():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    for question, contribution, quote in [
        ('매월 10만원씩. 납입은 3개월만 할게요. 매도 금지.', '', '매월 10만원씩'),
        ('납입은 3개월만 할게요. 매도 금지.', '매월 10만원', ''),
        ('추가 납입할 자금이 없어요. 매도 금지.', '매월 10만원', '매월 10만원'),
        ('보너스를 받으면요. 추가 100만원을 넣겠습니다.', '', '추가 100만원'),
        ('추가 100만원을 넣겠습니다. 대출 승인 후에만 납입할 수 있습니다.', '', '추가 100만원'),
    ]:
        req = request([('US:VOO', 1000000, '일반 계좌')], question=question, preferences={'contribution': contribution})
        decision = Decision(funding_text=quote, constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
        result = plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)
        assert result['status'] == 'needs_input'
        assert result['rows'] == []


def test_standard_affirmative_funding_can_be_cancelled_by_another_input_field():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    for verb in ['납입하겠습니다', '투자하겠습니다', '입금하겠습니다']:
        req = request([('US:VOO', 1000000, '일반 계좌')], question=f'추가 100만원을 {verb}.')
        decision = Decision(funding_text='추가 100만원', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
        assert plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)['status'] == 'ready'
        req.preferences.note = '추가 납입은 취소했습니다.'
        assert plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)['status'] == 'needs_input'
        req.preferences.note = '대출 승인 후에만 납입할 수 있습니다.'
        assert plan_execution(req, target(**{'US:VOO': 50, 'US:QQQ': 50}), decision)['status'] == 'needs_input'


def test_unknown_new_money_does_not_hide_an_impossible_zero_target():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    req = request([('US:VOO', 1000000, '일반 계좌')])
    plan = plan_execution(req, target(**{'US:QQQ': 100}), Decision(kind='new_money',
        constraints=[{'kind': 'no_sell', 'source': '매도 금지'}]))
    assert plan['status'] == 'infeasible'
    assert plan['reasons'] == ['NO_SELL_ZERO_TARGET']


def test_new_money_classification_does_not_invent_a_sell_prohibition():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    req = request([('US:VOO', 1000000, '일반 계좌')], question='추가 100만원')
    decision = Decision(kind='new_money', funding_text='추가 100만원')
    plan = plan_execution(req, target(**{'US:QQQ': 100}), decision)
    assert plan['status'] == 'ready'
    assert sum(int(row['sell_krw']) for row in plan['rows']) == 1000000


def test_unknown_funding_does_not_hide_an_already_exceeded_account_limit():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    req = request([('CASH:KRW', 1000000, '일반 계좌')], preferences={'accounts': ['ISA'], 'tax_profile': {
        'residence': '대한민국 거주자', 'age': '19~54세', 'income': '확인 필요', 'financial_income_tax': '확인 필요',
        'accounts': [{'account': 'ISA', 'opened': '2020-01-01', 'remaining_man': '0'}]}})
    plan = plan_execution(req, [{'instrument_id': 'KR:069500', 'account': 'ISA', 'weight_pct': 100}], Decision(funding_text='추가 금액 미정'))
    assert plan['status'] == 'infeasible'
    assert plan['reasons'] == ['ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:ISA']


@pytest.mark.parametrize('feasible_retry', [False, True])
async def test_execution_rejection_is_not_misreported_as_a_low_rating(monkeypatch, feasible_retry):
    import json
    from edge_portfolio.agent import run_analysis
    from report_fixtures import final_payload
    from test_agent import Evidence
    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1',
                'allocations': [{'instrument_id': 'KR:069500', 'weight_pct': 100}]}]}
            self.batches = {'first', 'alternative'}
        def validate(self, final):
            return self.result['candidates'][0] if final.recommendation else None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    phases, repairs = [], []
    async def model(payload):
        role = phases[-1]
        content = final_payload() if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {}}
        if role == 'opinion':
            if feasible_retry and len(repairs) == 1:
                repairs.append('retry')
                return {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'retry-account', 'type': 'function', 'function': {'name': 'calculate_rebalance',
                        'arguments': json.dumps({'allocations': [{'instrument_id': 'KR:069500', 'account': '일반 계좌', 'weight_pct': 100}]})}}]}}]}
            content['recommendation']['allocations'] = [{'instrument_id': 'KR:069500', 'account': 'ISA', 'weight_pct': 100}]
            content['selected_candidate_id'] = 'candidate-1'
            if repairs:
                assert ('ready' if feasible_retry else 'EXECUTION_INFEASIBLE') in payload['messages'][-1]['content']
                content['recommendation'] = None
                content['selected_candidate_id'] = None
                if feasible_retry:
                    content['withheld_reason'] = 'quality_failed'
            repairs.append(1)
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    req = request([('CASH:KRW', 1000000, '일반 계좌')], preferences={'accounts': ['ISA', '일반 계좌'], 'tax_profile': {
        'residence': '대한민국 거주자', 'age': '19~54세', 'income': '확인 필요', 'financial_income_tax': '확인 필요',
        'accounts': [{'account': 'ISA', 'opened': '2020-01-01', 'remaining_man': '0'}]}})
    result = await run_analysis(req, 'test-only', phases.append, model_call=model, evidence=Evidence())
    assert len(repairs) == (3 if feasible_retry else 4), 'Execution failure still receives recovery after two search batches'
    assert result['recommendation'] is None
    assert result['optimization']['reason'] == ('RATING_BELOW_MINIMUM' if feasible_retry else 'EXECUTION_INFEASIBLE')
    if feasible_retry:
        assert 'execution_rejection' not in result
        execution_events = [event for event in result['diagnostics']['events'] if event['event'] == 'tool_finished' and event['tool'] == 'calculate_rebalance']
        assert execution_events[-1]['status'] == 'ready'
    else:
        assert result['execution_rejection']['reasons'] == ['ACCOUNT_CONTRIBUTION_LIMIT_EXCEEDED:ISA']


def test_rebalance_tool_does_not_accept_model_invented_funding_or_accounts():
    import pytest
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import execution_tool
    req = request([('US:VOO', 1000000, '일반 계좌')], question='추가 100만원')
    decision = Decision(kind='new_money', funding_text='추가 100만원')
    catalog = {key: {'name': key, 'market': 'US', 'kind': 'etf'} for key in ['US:VOO', 'US:QQQ']}
    args = {'allocations': target(**{'US:VOO': 50, 'US:QQQ': 50})}
    plan = execution_tool(req, args, decision, catalog)
    assert plan['status'] == 'ready'
    assert sum(int(row['buy_krw']) for row in plan['rows']) == 1000000
    with pytest.raises(ValueError):
        execution_tool(req, {**args, 'additional_krw': 99999999}, decision, catalog)
    args['allocations'][0]['account'] = 'ISA'
    with pytest.raises(ValueError):
        execution_tool(req, args, decision, catalog)
