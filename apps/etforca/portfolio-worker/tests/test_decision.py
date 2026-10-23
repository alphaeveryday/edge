import pytest

from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.decision import Decision, check_constraints, validate_decision


def request(**kwargs):
    return AnalysisInput(holdings=[], planning=True, horizon='10년 이상', **kwargs)


def test_new_design_label_does_not_discard_or_reject_existing_holdings():
    req = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}], horizon='10년 이상')
    assert validate_decision(Decision(kind='new_portfolio'), req, {'US:VOO': {}}).kind == 'new_portfolio'
    assert req.holdings[0].amount_krw == 1000000


def test_customer_weight_cap_is_checked_on_aggregate_not_split_accounts():
    decision = Decision(constraints=[{'kind': 'max_weight', 'instrument_ids': ['US:VOO'],
        'value': 30, 'source': 'VOO는 30% 이하'}])
    rows = [{'instrument_id': 'US:VOO', 'weight_pct': 20, 'account': account} for account in ['일반 계좌', 'ISA']]
    checks = check_constraints(decision, rows, request(), {})
    assert checks[0]['status'] == 'fail'


def test_semantic_constraint_is_preserved_for_model_judgment_without_claiming_code_verification():
    decision = Decision(constraints=[{'kind': 'unverified', 'source': '기술주 노출은 30% 이하'}])
    check = check_constraints(decision, [], request(), {})[0]
    assert check['status'] == 'model_review'
    assert check['source'] == '기술주 노출은 30% 이하'


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


def holder(holdings, chips=(), **kwargs):
    return AnalysisInput(holdings=[{'name': key, 'instrument_id': key, 'amount_krw': amount, 'kind': 'cash' if key == 'CASH:KRW' else 'etf'}
                                   for key, amount in holdings], horizon='10년 이상', constraints=list(chips), **kwargs)


CATALOG = {'US:QLD': {'name': 'ProShares Ultra QQQ', 'market': 'US', 'kind': 'etf'},
           'US:VOO': {'name': 'Vanguard S&P 500 ETF', 'market': 'US', 'kind': 'etf'},
           'KR:360750': {'name': 'TIGER 미국S&P500', 'market': 'KR', 'kind': 'etf'},
           'KR:148070': {'name': 'KIWOOM 국고채10년', 'market': 'KR', 'kind': 'etf'},
           'KR:458730': {'name': 'TIGER 미국배당다우존스', 'market': 'KR', 'kind': 'etf'}}


def chip_checks(req, rows):
    decision = validate_decision(Decision(), req, CATALOG)
    return {check['kind']: check['status'] for check in check_constraints(decision, [{'instrument_id': key, 'weight_pct': weight} for key, weight in rows], req, CATALOG)}


def test_only_chips_become_constraints_and_written_conditions_are_reference_only():
    # 8차 결정: 글(질문·원하는 포트폴리오)에서 조건을 뽑으면 고객이 고르지 않은 조건으로 추천을 보류하게 된다(실제 customer_failed 2건)
    req = holder([('US:VOO', 1000)], question='갖고 있는 건 팔지 마세요', goal_text='레버리지는 빼고 싶어요')
    model_rule = {'kind': 'no_sell', 'source': '갖고 있는 건 팔지 마세요'}
    assert validate_decision(Decision(constraints=[model_rule]), req, CATALOG).constraints == []
    chips = holder([('US:VOO', 1000)], [{'kind': 'max_positions', 'value': 3}, {'kind': 'no_sell'}])
    rules = validate_decision(Decision(constraints=[model_rule]), chips, CATALOG).constraints
    assert [(rule.kind, rule.source, rule.value) for rule in rules] == [('max_positions', '종목 3개 이하', 3), ('no_sell', '갖고 있는 건 팔지 않기', None)]


@pytest.mark.parametrize('chips', [[{'kind': 'no_sell'}, {'kind': 'no_sell'}], [{'kind': 'max_positions'}], [{'kind': 'no_sell', 'value': 2}],
                                   [{'kind': 'max_positions', 'value': 21}], [{'kind': 'unknown'}]])
def test_chip_list_is_strict(chips):
    # 화면과 서버가 같은 칩 뜻을 써야 한다. 모르는 칩·중복·값 없는 개수 칩은 받지 않는다
    with pytest.raises(ValueError):
        holder([('US:VOO', 1000)], chips)


def test_old_input_without_chips_has_no_constraints():
    # 옛 저장 입력(칩 없음)은 조건 없이 동작해야 후속 질문이 깨지지 않는다
    old = AnalysisInput.model_validate({'holdings': [{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000}], 'horizon': '10년 이상'},
                                       context={'persisted_input': True})
    assert old.constraints == [] and old.monthly_krw is None and old.goal_text == ''
    assert validate_decision(Decision(), old, CATALOG).constraints == []


def test_no_leverage_blocks_new_buys_but_lets_holders_sell():
    req = holder([('US:QLD', 600), ('KR:360750', 400)], [{'kind': 'no_leverage'}])
    assert chip_checks(req, [('KR:360750', 100)])['no_leverage'] == 'pass'  # 가진 레버리지를 다 팔아도 된다
    assert chip_checks(req, [('US:QLD', 60), ('KR:360750', 40)])['no_leverage'] == 'pass'  # 그대로 둬도 새로 산 게 아니다
    assert chip_checks(req, [('US:QLD', 70), ('KR:360750', 30)])['no_leverage'] == 'fail'  # 늘리면 새로 산 것


def test_no_sell_wins_over_no_leverage_and_monthly_money_skips_leverage():
    # 시안 R5: 팔지 않기가 이기고 레버리지는 매달 넣는 돈을 넣지 않아 줄인다
    from edge_portfolio.execution import build_execution
    req = holder([('US:QLD', 6000000), ('KR:360750', 4000000)], [{'kind': 'no_leverage'}, {'kind': 'no_sell'}], monthly_krw=300000)
    assert chip_checks(req, [('US:QLD', 60), ('KR:360750', 40)]) == {'no_leverage': 'substituted', 'no_sell': 'pass'}
    plan = build_execution(req, [{'instrument_id': 'US:QLD', 'account': '일반 계좌', 'weight_pct': 60},
                                 {'instrument_id': 'KR:360750', 'account': '일반 계좌', 'weight_pct': 40}], monthly_krw=300000, no_sell=True)
    assert plan['status'] == 'ready'
    assert plan['monthly_plan'] == [{'account': '일반 계좌', 'instrument_id': 'KR:360750', 'krw': '300000'}]


def test_bonds_and_dividend_are_substituted_only_when_no_sell_makes_them_impossible():
    rows = [('KR:360750', 95), ('KR:148070', 5)]
    # 현금 20%가 있으면 채권 10%를 채울 수 있으니 대신하지 않는다
    assert chip_checks(holder([('KR:360750', 800), ('CASH:KRW', 200)], [{'kind': 'some_bonds'}, {'kind': 'no_sell'}]), rows)['some_bonds'] == 'fail'
    # 가진 주식 ETF가 95%라 팔지 않으면 채권 10%를 채울 수 없다
    assert chip_checks(holder([('KR:360750', 950), ('CASH:KRW', 50)], [{'kind': 'some_bonds'}, {'kind': 'no_sell'}]), rows)['some_bonds'] == 'substituted'
    assert chip_checks(holder([('KR:360750', 950), ('CASH:KRW', 50)], [{'kind': 'some_bonds'}]), rows)['some_bonds'] == 'fail'
    assert chip_checks(holder([('CASH:KRW', 1000)], [{'kind': 'some_bonds'}]), [('KR:360750', 90), ('KR:148070', 10)])['some_bonds'] == 'pass'
    assert chip_checks(holder([('CASH:KRW', 1000)], [{'kind': 'dividend'}]), [('KR:360750', 50), ('KR:458730', 50)])['dividend'] == 'pass'
    assert chip_checks(holder([('CASH:KRW', 1000)], [{'kind': 'dividend'}]), [('KR:360750', 60), ('KR:458730', 40)])['dividend'] == 'fail'


def test_max_positions_counts_distinct_instruments_and_yields_to_no_sell():
    held = [('US:QLD', 300), ('KR:360750', 300), ('KR:148070', 400)]
    assert chip_checks(holder(held, [{'kind': 'max_positions', 'value': 2}]), [('KR:360750', 50), ('KR:148070', 50)])['max_positions'] == 'pass'
    assert chip_checks(holder(held, [{'kind': 'max_positions', 'value': 2}]), [('US:QLD', 30), ('KR:360750', 30), ('KR:148070', 40)])['max_positions'] == 'fail'
    both = holder(held, [{'kind': 'max_positions', 'value': 2}, {'kind': 'no_sell'}])
    assert chip_checks(both, [('US:QLD', 30), ('KR:360750', 30), ('KR:148070', 40)])['max_positions'] == 'substituted'
    assert chip_checks(both, [('US:QLD', 30), ('KR:360750', 30), ('KR:148070', 30), ('KR:458730', 10)])['max_positions'] == 'fail'


def test_kr_listed_only_allows_us_index_etfs_listed_in_korea():
    req = holder([('US:VOO', 500), ('CASH:KRW', 500)], [{'kind': 'kr_listed_etf_only'}])
    assert chip_checks(req, [('US:VOO', 50), ('KR:360750', 50)])['kr_listed_etf_only'] == 'pass'  # 가진 미국 상장은 둬도 된다
    assert chip_checks(req, [('US:VOO', 70), ('KR:360750', 30)])['kr_listed_etf_only'] == 'fail'


def test_monthly_krw_input_wins_over_written_contribution():
    from edge_portfolio.execution import resolve_funding
    req = holder([('KR:360750', 1000)], monthly_krw=500000, preferences={'contribution': '매달 30만원'})
    assert resolve_funding(req)['monthly_krw'] == 500000
    assert resolve_funding(holder([('KR:360750', 1000)], preferences={'contribution': '매달 30만원'}))['monthly_krw'] == 300000


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
    assert result['decision']['constraints'] == []  # 글에서 뽑은 조건은 버린다(칩만 조건)


@pytest.mark.parametrize('reject_first', [True, False])
async def test_service_eligible_candidate_cannot_publish_after_customer_constraint_failure(monkeypatch, reject_first):
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
            content = {**content, 'decision': {'kind': 'rebalance'}}
        if role == 'opinion':
            if repairs or not reject_first:
                if repairs:
                    assert 'CUSTOMER_CONSTRAINT_NOT_SATISFIED' in payload['messages'][-1]['content']
                content['recommendation'] = None
            content['answers'] = [{'question_index': 1, 'status': 'answered', 'answer': '필수 비중 조건을 확인했으며 이를 만족하는 추천이 검증되지 않아 보류합니다.'}]
            content['withheld_reason'] = 'customer_failed'
            repairs.append(1)
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    # 미국 상장 VOO 100% 추천은 '국내 상장 ETF만' 칩을 어긴다. 서버가 막고 다른 후보를 찾게 해야 한다
    result = await run_analysis(request(question='국내 상장만 담아 주세요', constraints=[{'kind': 'kr_listed_etf_only'}]), 'test-only', phases.append,
                                model_call=model, evidence=Evidence())
    assert len(repairs) == (4 if reject_first else 1), 'Customer constraint failures still receive recovery after two search batches'
    assert result['recommendation'] is None
    assert result['customer_constraint_checks'][0]['status'] == ('fail' if reject_first else 'unknown')
    assert result['optimization']['reason'] == ('CUSTOMER_CONSTRAINT_NOT_SATISFIED' if reject_first else 'RECOMMENDATION_WITHHELD')


async def test_written_condition_without_chip_does_not_block_publication(monkeypatch):
    # 글의 '레버리지 제외'는 참고다. 칩이 없으면 조건 검사 없이 추천을 낸다
    import json
    from edge_portfolio.agent import run_analysis
    from report_fixtures import final_payload
    from test_agent import Evidence

    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
            self.batches = {'first'}
        def validate(self, final):
            return self.result['candidates'][0] if final.recommendation else None

    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    stages = []
    async def model(payload):
        role = stages[-1]
        content = final_payload() if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {'constraints': [{'kind': 'unverified', 'source': '레버리지 제외'}]}}
        else:
            active = json.loads(payload['messages'][1]['content'])['active_decision']
            assert active['constraints'] == []
        if role == 'opinion':
            content['answers'] = [{'question_index': 1, 'status': 'answered', 'answer': '레버리지 상품을 제외한 ETF 구성과 비중을 추천합니다.'}]
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}

    result = await run_analysis(request(question='레버리지 제외'), 'test', stages.append, model_call=model, evidence=Evidence())
    assert result['recommendation']['allocations']
    assert result['customer_constraint_checks'] == []
    assert stages.count('opinion') == 1


def test_one_won_of_new_leverage_is_a_new_buy():
    # '새로 사는 레버리지 0원' 계약: 비중 반올림 허용으로 몇백 원 매수가 지나가면 안 된다
    req = holder([('US:QLD', 6000000), ('KR:360750', 4000000)], [{'kind': 'no_leverage'}])
    assert chip_checks(req, [('US:QLD', 60.004), ('KR:360750', 39.996)])['no_leverage'] == 'fail'
    assert chip_checks(req, [('US:QLD', 60.00001), ('KR:360750', 39.99999)])['no_leverage'] == 'fail'  # 1원 매수
    assert chip_checks(req, [('US:QLD', 60.000004), ('KR:360750', 39.999996)])['no_leverage'] == 'pass'  # 1원이 안 되는 끝자리


def test_leverage_only_holder_with_no_sell_is_not_made_infeasible_by_monthly_money():
    # 팔지 않기가 이기는 결정: 가진 레버리지만 남는 구성도 추천이어야 한다. 매달 넣을 곳이 없을 뿐 실행 불가가 아니다
    from edge_portfolio.execution import build_execution
    req = holder([('US:QLD', 1000000)], [{'kind': 'no_leverage'}, {'kind': 'no_sell'}], monthly_krw=300000)
    plan = build_execution(req, [{'instrument_id': 'US:QLD', 'account': '일반 계좌', 'weight_pct': 100}], monthly_krw=300000, no_sell=True)
    assert (plan['status'], plan['reasons'], plan['monthly_plan']) == ('ready', [], [])


def test_followup_funding_change_beats_the_saved_monthly_amount():
    # 저장된 monthly_krw 가 후속 질문의 '매달 50만 원이면?'을 덮으면 견주기 답이 틀린다
    from edge_portfolio.decision import Funding
    from edge_portfolio.execution import resolve_funding
    from edge_portfolio.followup import FollowupInput
    req = FollowupInput(holdings=[], planning=True, horizon='10년 이상', monthly_krw=300000,
                        question='처음\n\n후속 질문: 매달 50만원이면?', funding_questions=['처음', '매달 50만원이면?'])
    changed = Decision(funding=Funding(source='매달 50만원', monthly_krw=500000))
    assert resolve_funding(req, changed)['monthly_krw'] == 500000
    assert resolve_funding(req, Decision())['monthly_krw'] == 300000


def test_model_may_structure_the_numeric_monthly_amount():
    # 숫자 칸의 월 납입은 글에 없지만 고객 입력이다. 모델이 그대로 옮겨도 인용 검사로 거절하지 않는다
    req = holder([('KR:360750', 1000)], monthly_krw=500000)
    decision = Decision(funding={'source': '매달 50만원', 'monthly_krw': 500000})
    assert validate_decision(decision, req, CATALOG).funding.monthly_krw == 500000


async def test_followup_without_new_funding_keeps_the_saved_monthly_amount():
    # 후속 질문이 납입을 말하지 않으면 옛 글 해석(30만)이 숫자 칸(50만)을 뒤집으면 안 된다
    import json
    from edge_portfolio.followup import answer_question
    from report_fixtures import final_payload
    from test_agent import Evidence
    report = {'input': {'holdings': [], 'planning': True, 'horizon': '10년 이상', 'monthly_krw': 500000,
                        'preferences': {'contribution': '매달 30만원'}},
              'decision': {'funding': {'source': '매달 30만원', 'monthly_krw': 300000}}, 'sources': Evidence.sources}
    stages = []
    async def model(payload):
        if stages[-1] != 'question':
            assert json.loads(payload['messages'][1]['content'])['active_decision']['funding'] is None
            raise RuntimeError('FUNDING_CHECKED')
        content = {**final_payload()['question'], 'decision': {'kind': 'review'}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    with pytest.raises(RuntimeError, match='FUNDING_CHECKED'):
        await answer_question('세금은 얼마나 나와요?', report, 'test', model_call=model, evidence=Evidence(), on_stage=stages.append)


def test_followup_estimated_monthly_amount_keeps_its_assumption():
    # 후속 질문의 추정 납입(월 50 정도)을 쓰면 추정이라는 설명도 함께 실행표에 남는다
    from edge_portfolio.decision import Funding
    from edge_portfolio.execution import plan_execution
    from edge_portfolio.followup import FollowupInput
    req = FollowupInput(holdings=[{'name': 'KR:360750', 'instrument_id': 'KR:360750', 'amount_krw': 1000000}], horizon='10년 이상', monthly_krw=300000,
                        question='처음\n\n후속 질문: 월 50 정도면?', funding_questions=['처음', '월 50 정도면?'])
    changed = Decision(funding=Funding(source='월 50 정도면?', monthly_krw=500000, estimated_fields=['monthly_krw'], assumptions=['월 50만원으로 가정']))
    plan = plan_execution(req, [{'instrument_id': 'KR:360750', 'account': '일반 계좌', 'weight_pct': 100}], changed)
    assert plan['monthly_plan'][0]['krw'] == '500000'
    assert '월 50만원으로 가정' in plan['assumptions']


def test_new_leverage_is_judged_on_the_same_won_split_as_the_trade_table():
    # 비중 끝자리가 1원 미만이어도 원 단위 배분(최대 나머지)에서 1원 매수가 생기면 새로 산 것이다
    req = holder([('US:QLD', 6000000), ('KR:360750', 4000000)], [{'kind': 'no_leverage'}])
    assert chip_checks(req, [('US:QLD', 60.000006), ('KR:360750', 39.999994)])['no_leverage'] == 'fail'
    assert chip_checks(req, [('US:QLD', 60), ('KR:360750', 40)])['no_leverage'] == 'pass'


def test_no_sell_with_a_position_cap_substitutes_bonds_instead_of_blocking_every_candidate():
    # 주식 ETF 90%·현금 10%에 팔지 않기·종목 1개·채권도 조금: 새 종목을 못 더하니 채권은 대신 지키고 추천은 나와야 한다
    req = holder([('KR:360750', 900), ('CASH:KRW', 100)], [{'kind': 'no_sell'}, {'kind': 'max_positions', 'value': 1}, {'kind': 'some_bonds'}])
    assert chip_checks(req, [('KR:360750', 100)]) == {'no_sell': 'pass', 'max_positions': 'pass', 'some_bonds': 'substituted'}
    # 종목 수 제한이 없으면 현금 10%로 채권 10%를 채울 수 있으니 대신하지 않는다
    free = holder([('KR:360750', 900), ('CASH:KRW', 100)], [{'kind': 'no_sell'}, {'kind': 'some_bonds'}])
    assert chip_checks(free, [('KR:360750', 100)])['some_bonds'] == 'fail'


def test_bond_and_dividend_reachability_respects_the_other_buy_blocking_chips():
    # 미국 배당 ETF는 국내 상장 칩 때문에, 새 배당 ETF는 종목 수 칩 때문에 더 못 산다. 배당은 대신 지켜야 추천이 남는다
    catalog = {**CATALOG, 'US:SCHD': {'name': 'Schwab US Dividend Equity ETF', 'market': 'US', 'kind': 'etf'}}
    req = holder([('US:SCHD', 400), ('KR:360750', 400), ('CASH:KRW', 200)],
                 [{'kind': 'no_sell'}, {'kind': 'max_positions', 'value': 2}, {'kind': 'dividend'}, {'kind': 'kr_listed_etf_only'}])
    decision = validate_decision(Decision(), req, catalog)
    checks = check_constraints(decision, [{'instrument_id': 'US:SCHD', 'weight_pct': 40}, {'instrument_id': 'KR:360750', 'weight_pct': 60}], req, catalog)
    assert {check['kind']: check['status'] for check in checks}['dividend'] == 'substituted'


def test_followup_principal_only_funding_keeps_the_saved_monthly_amount():
    # 원금만 다시 말한 후속 질문이 저장된 월 납입을 지우면 매달 칸이 사라진다
    from edge_portfolio.decision import Funding
    from edge_portfolio.execution import resolve_funding
    from edge_portfolio.followup import FollowupInput
    req = FollowupInput(holdings=[], planning=True, horizon='10년 이상', monthly_krw=300000,
                        question='처음\n\n후속 질문: 원금은 1000만원입니다', funding_questions=['처음', '원금은 1000만원입니다'])
    principal = Decision(funding=Funding(source='원금은 1000만원입니다', principal_krw=10000000))
    assert resolve_funding(req, principal)['monthly_krw'] == 300000
