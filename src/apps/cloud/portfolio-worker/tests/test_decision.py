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


@pytest.mark.parametrize('source', ['accounts: 일반 계좌', 'accounts": ["일반 계좌"]', '계좌를 일반 계좌로 입력했어요.'])
def test_verified_account_selection_does_not_depend_on_model_source_format(source):
    req = request(preferences={'accounts': ['일반 계좌']})
    decision = Decision(constraints=[{'kind': 'accounts', 'accounts': ['일반 계좌'], 'source': source}])
    result = validate_decision(decision, req, {})
    assert result.constraints[0].source == '일반 계좌'
    assert result.constraints[0].accounts == ['일반 계좌']


def test_unselected_or_arbitrary_subset_accounts_still_need_customer_evidence():
    req = request(preferences={'accounts': ['일반 계좌', 'ISA']})
    for accounts, source in [(['IRP'], 'invented'), (['ISA'], 'invented'), (['ISA'], 'ISA')]:
        decision = Decision(constraints=[{'kind': 'accounts', 'accounts': accounts, 'source': source}])
        with pytest.raises(ValueError, match='CUSTOMER_CONSTRAINT_SOURCE'):
            validate_decision(decision, req, {})


def test_explicit_written_account_subset_is_preserved():
    req = request(question='이번에는 ISA에서만 투자할게요.', preferences={'accounts': ['일반 계좌', 'ISA']})
    decision = Decision(constraints=[{'kind': 'accounts', 'accounts': ['ISA'], 'source': req.question}])
    assert validate_decision(decision, req, {}).constraints[0].accounts == ['ISA']


async def test_later_roles_receive_the_repaired_full_customer_question():
    import json
    from edge_portfolio.agent import run_analysis
    from report_fixtures import final_payload
    from test_agent import Evidence
    stages = []
    original = '추천 비중을 알려주세요. 리밸런싱 금액도 알려주세요.'
    async def model(payload):
        if stages[-1] != 'question':
            active = json.loads(payload['messages'][1]['content'])['active_decision']
            assert active['questions'] == [original]
            raise RuntimeError('CANONICAL_QUESTION_CHECKED')
        content = {**final_payload()['question'], 'decision': {'questions': ['추천 비중을 알려주세요.']}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(RuntimeError, match='CANONICAL_QUESTION_CHECKED'):
        await run_analysis(request(question=original), 'test', stages.append, model_call=model, evidence=Evidence())


@pytest.mark.parametrize('interest', ['균형 잡힌 분산', '성장', '배당', '안정 (채권·금)', '반도체', '미국 주식'])
def test_standard_interest_is_a_ranking_preference_not_an_unknown_hard_constraint(interest):
    req = request(preferences={'interests': [interest]})
    decision = Decision(objectives=['growth', 'stability'], constraints=[{'kind': 'unverified', 'source': interest}])
    result = validate_decision(decision, req, {})
    assert result.constraints == []
    assert result.objectives == ['growth', 'stability']
    assert check_constraints(result, [], req, {}) == []


def test_explicit_customer_limit_is_preserved_even_when_it_mentions_a_standard_interest():
    req = request(question='반도체 비중은 반드시 20% 이하로 유지', preferences={'interests': ['반도체']})
    rule = {'kind': 'unverified', 'source': '반도체 비중은 반드시 20% 이하로 유지'}
    result = validate_decision(Decision(constraints=[rule]), req, {})
    assert len(result.constraints) == 1
    assert check_constraints(result, [], req, {})[0]['status'] == 'unknown'


def test_custom_interest_with_an_explicit_limit_remains_a_customer_constraint():
    text = '기술주 비중 30% 이하'
    req = request(preferences={'interests': [text]})
    result = validate_decision(Decision(constraints=[{'kind': 'unverified', 'source': text}]), req, {})
    assert len(result.constraints) == 1


def test_followup_written_limit_is_preserved_when_source_is_also_an_interest_chip():
    req = request(preferences={'interests': ['반도체']})
    result = validate_decision(Decision(constraints=[{'kind': 'unverified', 'source': '반도체'}]),
                               req, {}, question_text='반도체 비중은 20% 이하로 제한해 주세요.')
    assert len(result.constraints) == 1


def test_selected_us_interest_does_not_prohibit_korean_etfs():
    req = request(preferences={'interests': ['미국 주식']})
    result = validate_decision(Decision(constraints=[{'kind': 'markets', 'source': '미국 주식', 'markets': ['US']}]), req, {})
    assert result.constraints == []


def test_written_dividend_condition_is_not_erased_by_a_matching_interest_chip():
    req = request(preferences={'interests': ['배당'], 'dividend_goal': '배당 월 100만원 이상 필수'})
    result = validate_decision(Decision(constraints=[{'kind': 'unverified', 'source': '배당'}]), req, {})
    assert len(result.constraints) == 1


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
            self.batches = {'first', 'alternative'}
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
            content['answers'] = [{'question_index': 1, 'status': 'answered', 'answer': '필수 비중 조건을 확인했으며 이를 만족하는 추천이 검증되지 않아 보류합니다.'}]
            content['withheld_reason'] = 'customer_failed'
            repairs.append(1)
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    result = await run_analysis(request(question='VOO는 30% 이하'), 'test-only', phases.append,
                                model_call=model, evidence=Evidence())
    assert len(repairs) == (2 if reject_first else 1)
    assert result['recommendation'] is None
    assert result['customer_constraint_checks'][0]['status'] == ('fail' if reject_first else 'unknown')
    assert result['optimization']['reason'] == ('CUSTOMER_CONSTRAINT_NOT_SATISFIED' if reject_first or unverified else 'RECOMMENDATION_WITHHELD')
    assert result['input']['question'] == 'VOO는 30% 이하'
