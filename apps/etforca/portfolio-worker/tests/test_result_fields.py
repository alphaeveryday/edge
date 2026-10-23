"""8차 결과 칸 필드: 화면이 바로 꽂는 값은 서버가 결정적으로 만들고, 모델이 쓴 금액은 칸의 값과 대조한다."""
import json
from datetime import date

import pandas as pd
import pytest

from edge_portfolio.contracts import AnalysisInput, FinalReport
from edge_portfolio.market_data import comparison_period, past_amounts
from edge_portfolio.recommendation import answer_amount_error, clean_quote, result_fields, type_card, verdict_kind

CATALOG = {'US:QLD': {'name': 'ProShares Ultra QQQ', 'market': 'US', 'kind': 'etf'},
           'KR:360750': {'name': 'TIGER 미국S&P500', 'market': 'KR', 'kind': 'etf'},
           'KR:148070': {'name': 'KIWOOM 국고채10년', 'market': 'KR', 'kind': 'etf'},
           'KR:069500': {'name': 'KODEX 200', 'market': 'KR', 'kind': 'etf'},
           'KR:005930': {'name': '삼성전자', 'market': 'KR', 'kind': 'stock'}}


def holder(holdings, **kwargs):
    return AnalysisInput(holdings=[{'name': key, 'instrument_id': key, 'amount_krw': amount, 'account': account,
                                    'kind': 'cash' if key == 'CASH:KRW' else CATALOG.get(key, {}).get('kind', 'etf')}
                                   for key, amount, account in holdings], horizon='10년 이상', **kwargs)


def row(key, before, target, buy=0, sell=0):
    return {'instrument_id': key, 'account': '일반 계좌', 'before_krw': str(before), 'target_krw': str(target),
            'buy_krw': str(buy), 'sell_krw': str(sell), 'cash_used_krw': '0'}


def test_past_amounts_use_the_chart_points_scaled_to_the_customers_money():
    # 4번 칸의 금액은 차트(historical_performance)와 같은 점에서 나와야 두 숫자가 어긋나지 않는다
    history = {'points': [{'date': f'2026-01-0{i + 1}', 'recommended': value, 'current': 1000000}
                          for i, value in enumerate([1000000, 1200000, 900000, 1100000])]}
    assert past_amounts(history, 10000000) == {'rec_end_krw': '11000000', 'cur_end_krw': '10000000',
                                               'rec_max_loss_krw': '2500000', 'cur_max_loss_krw': '0'}
    history['points'][0]['current'] = None
    assert past_amounts(history, 10000000)['cur_end_krw'] is None  # 지금 구성을 비교하지 못했으면 지어내지 않는다


def frame(start, end):
    index = pd.bdate_range(start, end)
    return {'frame': pd.DataFrame({'Adj Close': [100.0] * len(index)}, index=index)}


@pytest.mark.parametrize('first,label,shortest', [('2026-06-15', '상장 뒤 4개월', 'KR:999999'), ('2023-06-01', '최근 3년', 'KR:999999'),
                                                  ('2026-10-01', '상장 뒤 20일', 'KR:999999')])
def test_period_is_set_by_the_latest_listed_instrument(first, label, shortest):
    # 계획 위험 '짧은 기간': 기간을 숨기지 않고 어느 종목 때문에 짧은지 머리말에 적는다
    records = {'KR:069500': frame('2010-01-04', '2026-10-21'), 'KR:999999': frame(first, '2026-10-21')}
    history = {'start_date': first, 'end_date': '2026-10-21', 'sources': [{'instrument_id': 'KR:069500'}, {'instrument_id': 'KR:999999'}]}
    assert comparison_period(history, records) == {'start': first, 'end': '2026-10-21', 'label': label, 'shortest_instrument_id': shortest}


def test_period_without_a_limiting_instrument_says_recent():
    records = {'KR:069500': frame('2010-01-04', '2026-10-21')}
    history = {'start_date': '2026-08-01', 'end_date': '2026-10-21', 'sources': [{'instrument_id': 'KR:069500'}, {'instrument_id': 'USD/KRW'}]}
    assert comparison_period(history, records)['label'] == '최근 2개월'
    assert comparison_period(history, records)['shortest_instrument_id'] is None


def test_verdict_kind_boundaries():
    # 계약 기본값 2번. 시안 R1 처럼 하나를 팔아 하나를 사는 교체는 '하나만'이다
    held = holder([('KR:069500', 500, '일반 계좌'), ('KR:360750', 500, '일반 계좌')])
    assert verdict_kind(holder([('CASH:KRW', 1000, '일반 계좌')]), {'rows': [row('KR:069500', 0, 1000, buy=1000)]}) == 'start'
    assert verdict_kind(held, {'rows': [row('KR:069500', 500, 500), row('KR:360750', 500, 500)]}) == 'keep'
    assert verdict_kind(held, {'rows': [row('KR:069500', 500, 0, sell=500), row('KR:148070', 0, 500, buy=500)]}) == 'one'
    assert verdict_kind(held, {'rows': [row('KR:069500', 500, 0, sell=500), row('KR:360750', 500, 0, sell=500),
                                        row('KR:148070', 0, 1000, buy=1000)]}) == 'rebuild'


def test_answer_amounts_must_match_final_and_past_values():
    # 계획 2단계 통과 조건: 답 문장의 금액이 2·4번 칸 값과 같아야 한다. 만 원으로 반올림한 표기는 같다고 본다
    request = holder([('KR:069500', 2500000, '일반 계좌'), ('KR:360750', 10000000, '일반 계좌')])
    published = {'recommendation': {'execution': {'rows': [row('KR:069500', 2500000, 0, sell=2500000), row('KR:148070', 0, 2500000, buy=2500000)],
                                                  'monthly_plan': [{'account': 'ISA', 'instrument_id': 'KR:360750', 'krw': '300000'}], 'monthly_krw': '300000'}},
                 'past': {'rec_end_krw': '21781234', 'cur_end_krw': '18910000', 'rec_max_loss_krw': '2050000', 'cur_max_loss_krw': '2610000'}}
    published['answer'] = 'KODEX 200 250만 원만 채권으로 옮기세요. 매달 30만원은 ISA에 넣고, 지난 기간이었다면 287만 원을 더 벌고 56만 원 덜 잃었어요.'
    assert answer_amount_error(published, request) is None
    published['answer'] = '300만 원만 옮기세요.'
    assert '300만 원' in answer_amount_error(published, request)
    published['answer'] = '1억 4,850만 원이 돼요.'
    assert answer_amount_error(published, request)


def test_evidence_quotes_drop_markdown_headers_and_table_fragments():
    assert clean_quote('##### 총보수 **0.03%**') == '총보수 0.03%'
    assert clean_quote('| 종목 | 비중 |') == ''
    assert clean_quote('[공식 자료](https://example.com) 기준 분배금 120원') == '공식 자료 기준 분배금 120원'


@pytest.mark.parametrize('holdings,key', [
    ([('CASH:KRW', 1000, '일반 계좌')], 'first_start'),
    ([('US:QLD', 450, '일반 계좌'), ('KR:360750', 550, '일반 계좌')], 'leverage'),
    ([('KR:005930', 500, '일반 계좌'), ('KR:069500', 500, '일반 계좌')], 'stock_picker'),
    ([('KR:148070', 300, '일반 계좌'), ('KR:069500', 700, '일반 계좌')], 'bond_shield'),
    ([('KR:069500', 600, 'IRP'), ('KR:360750', 400, '일반 계좌')], 'pension'),
    ([('KR:069500', 600, '일반 계좌'), ('KR:360750', 400, '일반 계좌')], 'korea'),
    ([('KR:360750', 800, '일반 계좌'), ('KR:069500', 200, '일반 계좌')], 'us'),
    ([('KR:360750', 500, '일반 계좌'), ('KR:069500', 300, '일반 계좌'), ('KR:148070', 200, '일반 계좌')], 'balanced'),
])
def test_type_card_follows_the_draft_rules_in_order(holdings, key):
    # 시안 v8-SH-types: 위에서부터 먼저 맞는 유형 하나. 금액은 카드에 넣지 않는다
    card = type_card(holder(holdings), CATALOG, 300000)
    assert card['key'] == key
    assert card['stats']['monthly'] is True
    assert not any(name.endswith('_krw') for name in card['stats'])


def final(**fields):
    from report_fixtures import final_payload
    return FinalReport.model_validate({**final_payload(), **fields})


def test_result_fields_report_chip_results_and_question_titled_analysis():
    request = holder([('US:QLD', 6000000, '일반 계좌'), ('KR:360750', 4000000, '일반 계좌')],
                     constraints=[{'kind': 'no_leverage'}, {'kind': 'no_sell'}], monthly_krw=300000)
    section = {'paragraphs': ['문단'], 'evidence': [{'source_id': 's1', 'passage_id': 'p1', 'quote': '##### 총보수 0.95%'}], 'limitations': []}
    published = {'current_evaluation': {'overall': 2.4, 'reason': section},
                 'recommendation': {'allocations': [{'instrument_id': 'US:QLD', 'weight_pct': 60.0}, {'instrument_id': 'KR:360750', 'weight_pct': 40.0}],
                                    'evaluation': {'overall': 2.4, 'reason': section},
                                    'execution': {'rows': [row('US:QLD', 6000000, 6000000), row('KR:360750', 4000000, 4000000)], 'monthly_krw': '300000',
                                                  'monthly_plan': [{'account': '일반 계좌', 'instrument_id': 'KR:360750', 'krw': '300000'}]}},
                 'insights': [{'title': '왜 QLD를 팔지 않아요?', 'content': section}]}
    checks = [{'kind': 'no_leverage', 'status': 'substituted'}, {'kind': 'no_sell', 'status': 'pass'}]
    fields = result_fields(published, {'opinion': section, 'outlook': section}, final(headline='매달 넣는 돈만 바꾸세요', suggested_questions=['QLD를 팔면?', 'QLD를 절반만 팔면?', '세금은 얼마나 나와요?', '매달 50만 원이면?']),
                           request, checks, CATALOG, [{'id': 's1', 'url': 'https://official.example'}])
    assert fields['verdict'] == {'kind': 'keep', 'headline': '매달 넣는 돈만 바꾸세요'}  # 시안 R5
    assert [(item['kind'], item['status']) for item in fields['constraint_results']] == [('no_leverage', 'substituted'), ('no_sell', 'kept')]
    assert '팔지 않기를 먼저' in fields['constraint_results'][0]['note']
    assert [item['title'] for item in fields['analysis']] == ['왜 2.4점이에요?', '그래서 무엇을 하면 돼요?', '추천 구성은 왜 2.4점이에요?', '왜 QLD를 팔지 않아요?', '앞으로 1년은요?']
    assert fields['analysis'][0]['evidence'] == [{'quote': '총보수 0.95%', 'url': 'https://official.example'}]
    assert len(fields['suggested_questions']) == 4
    assert fields['type_card']['key'] == 'leverage'
    assert 'answer' not in fields  # 모델이 쓰지 않았으면 싣지 않는다(화면이 기존 답으로 대신 보인다)


def test_suggested_questions_are_four_or_none():
    with pytest.raises(ValueError, match='exactly four'):
        final(suggested_questions=['첫 질문이요', '둘째 질문이요'])


def test_monthly_money_fills_bonds_when_no_sell_blocks_the_bond_chip():
    # '남은 조건은 월 납입으로 맞추는 안': 팔지 않기 때문에 채권 10%를 못 채우면 매달 넣는 돈은 채권에만 간다
    from edge_portfolio.execution import build_execution
    request = holder([('KR:069500', 9500000, '일반 계좌'), ('KR:148070', 500000, '일반 계좌')],
                     constraints=[{'kind': 'some_bonds'}, {'kind': 'no_sell'}], monthly_krw=300000)
    plan = build_execution(request, [{'instrument_id': 'KR:069500', 'account': '일반 계좌', 'weight_pct': 95},
                                     {'instrument_id': 'KR:148070', 'account': '일반 계좌', 'weight_pct': 5}], monthly_krw=300000, no_sell=True)
    assert plan['monthly_plan'] == [{'account': '일반 계좌', 'instrument_id': 'KR:148070', 'krw': '300000'}]


async def test_wrong_answer_amounts_are_repaired_then_dropped_without_failing_the_analysis(monkeypatch):
    # 금액 검사는 분석 전체를 실패시키지 않는다. 두 번 고쳐 쓰게 한 뒤에도 틀리면 답만 빼고 결과를 낸다
    from edge_portfolio.agent import run_analysis
    from report_fixtures import final_payload
    from test_agent import Evidence

    class Optimizer:
        def __init__(self, *args):
            self.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
            self.batches = {'first'}
        def validate(self, final):
            return self.result['candidates'][0] if final.recommendation else None

    async def enrich(published, *args):
        return None
    monkeypatch.setattr('edge_portfolio.agent.Optimizer', Optimizer)
    monkeypatch.setattr('edge_portfolio.agent.enrich_report', enrich)
    stages, repairs = [], []
    async def model(payload):
        role = stages[-1]
        content = final_payload() if role == 'opinion' else final_payload()[role]
        if role == 'question':
            content = {**content, 'decision': {}}
        if role == 'opinion':
            if 'ANSWER_AMOUNT_MISMATCH' in payload['messages'][-1]['content']:
                repairs.append(1)
            content.update(answer='999만 원어치를 사세요.', headline='새로 시작해요',
                           answers=[{'question_index': 1, 'status': 'answered', 'answer': '추천 구성과 비중을 제시합니다.'}])
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상', question='무엇을 살까요?')
    result = await run_analysis(request, 'test', stages.append, model_call=model, evidence=Evidence())
    assert len(repairs) == 2
    assert 'answer' not in result
    assert result['recommendation']['allocations'] and result['analysis']
    assert any(event['event'] == 'answer_dropped' for event in result['diagnostics']['events'])


@pytest.mark.parametrize('followup', [False, True])
async def test_what_if_followup_can_lift_a_chip_only_for_that_calculation(followup):
    # 더 물어보기 '만약 ~면'(시안 R5의 'QLD를 팔면?')은 팔지 않기를 풀어 추천을 다시 계산해야 답할 수 있다.
    # 첫 분석은 고른 칩을 모두 지킨다
    from edge_portfolio.agent import run_analysis
    from edge_portfolio.followup import answer_question
    from report_fixtures import final_payload
    from test_agent import Evidence
    stages = []
    async def model(payload):
        if stages[-1] != 'question':
            rules = json.loads(payload['messages'][1]['content'])['active_decision']['constraints']
            assert [rule['kind'] for rule in rules] == (['max_positions'] if followup else ['max_positions', 'no_sell'])
            raise RuntimeError('RELAX_CHECKED')
        content = {**final_payload()['question'], 'decision': {'kind': 'rebalance', 'relaxed_constraints': ['no_sell']}}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]}
    saved = {'holdings': [{'name': 'QLD', 'instrument_id': 'US:QLD', 'amount_krw': 1000000}], 'horizon': '10년 이상',
             'constraints': [{'kind': 'max_positions', 'value': 3}, {'kind': 'no_sell'}]}
    with pytest.raises(RuntimeError, match='RELAX_CHECKED'):
        if followup:
            await answer_question('QLD를 팔면?', {'input': saved, 'sources': Evidence.sources}, 'test', model_call=model, evidence=Evidence(), on_stage=stages.append)
        else:
            await run_analysis(AnalysisInput.model_validate(saved), 'test', stages.append, model_call=model, evidence=Evidence())


def test_answer_amount_rounding_is_exact_to_the_written_unit():
    # '만 원으로 반올림' 허용은 그 자리까지만이다. 표기 단위 전체를 오차로 두면 틀린 금액이 지나간다
    request = holder([('KR:069500', 50000000, '일반 계좌')])
    published = {'recommendation': {'execution': {'rows': [row('KR:069500', 50000000, 50000000)]}}}
    for wrong in ('1억 원을 사세요.', '1.5억 원을 사세요.', '5,001만 원을 사세요.'):
        published['answer'] = wrong
        assert answer_amount_error(published, request), wrong
    published['answer'] = '5,000만 원은 그대로 두세요.'
    assert answer_amount_error(published, request) is None


def test_saved_target_execution_amounts_are_allowed_in_the_answer():
    # 저장된 추천을 실행하는 후속 답은 최상위 execution 의 금액을 쓴다
    request = holder([('KR:069500', 1000000, '일반 계좌')])
    published = {'execution': {'rows': [row('KR:069500', 1000000, 0, sell=1000000), row('KR:360750', 0, 1000000, buy=1000000)]},
                 'answer': 'KODEX 200 100만 원을 팔아 S&P500을 사세요.'}
    assert answer_amount_error(published, request) is None


def test_type_card_uses_unrounded_shares_and_skips_unknown_weights():
    assert type_card(holder([('US:QLD', 196, '일반 계좌'), ('KR:360750', 804, '일반 계좌')]), CATALOG, 0)['key'] == 'us'  # 19.6%는 20% 미만
    names = AnalysisInput(holdings=[{'name': 'KODEX 200', 'instrument_id': 'KR:069500'}], horizon='10년 이상', input_mode='names')
    assert type_card(names, CATALOG, 0) is None  # 비중을 모르는 보유자를 '첫 출발'로 부르지 않는다


def test_relaxed_chip_is_not_applied_by_checks_or_monthly_split():
    # 견주기에서 푼 칩이 판정과 매달 배분에 남으면 '풀면?'의 답이 풀지 않은 계산이 된다
    from edge_portfolio.decision import Decision, check_constraints, validate_decision
    from edge_portfolio.execution import plan_execution
    from edge_portfolio.followup import FollowupInput
    request = FollowupInput(holdings=[{'name': key, 'instrument_id': key, 'amount_krw': amount} for key, amount in [('KR:069500', 9500000), ('KR:148070', 500000)]],
                            horizon='10년 이상', monthly_krw=300000, constraints=[{'kind': 'some_bonds'}, {'kind': 'no_sell'}],
                            question='처음\n\n후속 질문: 채권 조건을 빼면?', funding_questions=['처음', '채권 조건을 빼면?'])
    decision = validate_decision(Decision(relaxed_constraints=['some_bonds']), request, CATALOG)
    assert [rule.kind for rule in decision.constraints] == ['no_sell']
    plan = plan_execution(request, [{'instrument_id': 'KR:069500', 'account': '일반 계좌', 'weight_pct': 95},
                                    {'instrument_id': 'KR:148070', 'account': '일반 계좌', 'weight_pct': 5}], decision)
    assert len(plan['monthly_plan']) == 2  # 채권에만 몰아주지 않는다


def test_answer_may_state_the_total_moved_across_instruments():
    request = holder([('KR:069500', 1000000, '일반 계좌'), ('KR:360750', 2000000, '일반 계좌')])
    published = {'recommendation': {'execution': {'rows': [row('KR:069500', 1000000, 0, sell=1000000), row('KR:360750', 2000000, 0, sell=2000000),
                                                           row('KR:148070', 0, 1500000, buy=1500000), row('US:QLD', 0, 1500000, buy=1500000)]}},
                 'answer': '총 300만 원을 옮기세요.'}
    assert answer_amount_error(published, request) is None


def test_multiline_quote_headers_are_cleaned():
    assert clean_quote('상품 설명\n##### 총보수 0.03%') == '상품 설명 총보수 0.03%'


def test_thousand_unit_amounts_are_checked_and_growth_needs_no_current_series():
    request = holder([('KR:069500', 10000000, '일반 계좌')])
    published = {'recommendation': {'execution': {'rows': [row('KR:069500', 10000000, 10000000)]}},
                 'past': {'rec_end_krw': '12345678', 'cur_end_krw': None, 'rec_max_loss_krw': '1000000', 'cur_max_loss_krw': None}}
    published['answer'] = '3천만 원을 사세요.'
    assert answer_amount_error(published, request)  # '천' 표기로 검사를 피하지 못한다
    published['answer'] = '1천만 원은 그대로 두세요. 지난 기간이었다면 원금보다 235만 원 늘었어요.'
    assert answer_amount_error(published, request) is None
    published['answer'] = '1,999만 원을 사세요.'  # 반올림만 허용한다(버림은 아니다)
    assert answer_amount_error(published, request)
