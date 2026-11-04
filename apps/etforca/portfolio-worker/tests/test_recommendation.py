from copy import deepcopy
import pytest
from edge_portfolio.contracts import AnalysisInput, FinalReport, Section, calculate, multiple_sentences
from edge_portfolio.recommendation import publish_recommendation, search_etfs
from report_fixtures import final_payload, section

SOURCES = [{'id': 's1', 'passages': [{'id': 'p1', 'text': 'Official evidence'}]}]


def publish(raw, request=None):
    request = request or AnalysisInput(holdings=[{'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 1000001}], horizon='10년 이상')
    return publish_recommendation(FinalReport.model_validate(raw), request, calculate(request), SOURCES)


def test_amounts_and_score_are_computed_and_current_input_is_not_the_recommendation():
    raw = final_payload()
    raw['recommendation']['allocations'] = [
        {'instrument_id': 'US:VOO', 'account': '일반 계좌', 'weight_pct': 50},
        {'instrument_id': 'US:QQQ', 'account': '일반 계좌', 'weight_pct': 30},
        {'instrument_id': 'US:VTI', 'account': '일반 계좌', 'weight_pct': 20}]
    result = publish(raw)
    plan = result['recommendation']
    assert sum(int(h['amount_krw']) for h in plan['allocations']) == 1000001
    assert plan['allocations'][0]['amount_krw'] == '500001'
    assert plan['evaluation']['overall'] == 4
    assert result['current_evaluation']['overall'] == 3.5
    assert [a['action'] for a in plan['actions']] == ['현금 투입', '새로 담기', '새로 담기', '새로 담기']
    assert result['insights'][0]['title'] == '지금 가장 먼저 판단할 것'
    assert plan['expected_return_1y'] is None and plan['historical_performance'] is None


def test_recommended_rating_is_required():
    raw = final_payload()
    raw['recommendation']['evaluation']['scores'] = None
    with pytest.raises(ValueError):
        FinalReport.model_validate(raw)


def test_recommendation_below_four_is_published_not_blocked():
    # 4~5점은 프롬프트 지침일 뿐이다. 모델이 4점 아래로 매겨도 서버가 막으면 고객은 추천을 못 받는다(8차 결정)
    raw = final_payload()
    raw['recommendation']['evaluation']['scores'] = {'leadership': 3, 'profitability': 3.5, 'stability': 4.5}
    assert publish(raw)['recommendation']['evaluation']['overall'] == 3.7
    for scores, overall in (({'leadership': 4, 'profitability': 4, 'stability': 4}, 4),
                            ({'leadership': 5, 'profitability': 4.5, 'stability': 3.5}, 4.3),
                            ({'leadership': 5, 'profitability': 5, 'stability': 5}, 5)):
        raw['recommendation']['evaluation']['scores'] = scores
        assert publish(raw)['recommendation']['evaluation']['overall'] == overall


def test_current_portfolio_keeps_scores_below_four():
    # 하한은 추천 구성에만 있다. 사용자가 지금 가진 구성은 근거대로 낮게 매길 수 있어야 비교가 된다.
    raw = final_payload()
    raw['current_evaluation']['scores'] = {'leadership': 1, 'profitability': 1.5, 'stability': 2}
    assert publish(raw)['current_evaluation']['overall'] == 1.5


def test_cash_current_rating_can_be_absent_without_hiding_recommended_stars():
    raw = final_payload()
    raw['current_evaluation']['scores'] = None
    raw['current_evaluation']['reason']['evidence'] = []
    result = publish(raw)
    assert result['current_evaluation']['overall'] is None
    assert result['recommendation']['evaluation']['overall'] == 4


def test_first_portfolio_has_no_external_evidence_for_nonexistent_current_holdings():
    raw = final_payload()
    raw['current_evaluation']['scores'] = None
    raw['current_evaluation']['reason']['evidence'] = []
    request = AnalysisInput(holdings=[], planning=True, horizon='10년 이상')
    result = publish(raw, request)
    assert result['current_evaluation']['overall'] is None
    assert result['recommendation']['evaluation']['overall'] == 4


def test_unrated_etf_current_evaluation_still_requires_external_evidence():
    raw = final_payload()
    raw['current_evaluation']['scores'] = None
    raw['current_evaluation']['reason']['evidence'] = []
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}], horizon='10년 이상')
    with pytest.raises(ValueError, match='require source evidence'):
        publish(raw, request)


@pytest.mark.parametrize('change,match', [
    ({'weight_pct': 99}, 'total'), ({'instrument_id': 'US:INVENTED'}, 'registered'),
    ({'account': 'ISA'}, 'not selected'), ({'instrument_id': 'CASH:KRW'}, 'cash weight')])
def test_invalid_model_recommendations_are_not_published(change, match):
    raw = final_payload(); raw['recommendation']['allocations'][0].update(change)
    with pytest.raises(ValueError, match=match): publish(raw)


@pytest.mark.parametrize('account', ['ISA', 'IRP', '연금저축'])
def test_account_restriction_and_unknown_budget_are_preserved(account):
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO'}], horizon='10년 이상', input_mode='names', preferences={'accounts': ['일반 계좌', account]})
    raw = final_payload(); raw['recommendation']['allocations'][0]['account'] = account
    with pytest.raises(ValueError, match='US-listed'): publish(raw, request)
    raw['recommendation']['allocations'][0]['account'] = '일반 계좌'
    result = publish(raw, request)['recommendation']
    assert result['total_krw'] is None
    assert result['allocations'][0]['amount_krw'] is None
    assert result['actions'][0]['before_pct'] is None


def test_duplicate_allocation_and_fabricated_evidence_fail():
    raw = final_payload(); row = raw['recommendation']['allocations'][0]; row['weight_pct'] = 50
    raw['recommendation']['allocations'].append(deepcopy(row))
    with pytest.raises(ValueError, match='Duplicate'): publish(raw)
    raw = final_payload(); raw['recommendation']['evaluation']['reason']['evidence'][0]['passage_id'] = 'invented'
    with pytest.raises(ValueError, match='passage'): publish(raw)


def test_bullets_have_bounded_count_without_rejecting_valid_multi_sentence_explanations():
    for paragraphs in [['설명'] * 2, ['설명'] * 6, ['설명' * 100] * 3]:
        with pytest.raises(ValueError): Section.model_validate({**section(), 'paragraphs': paragraphs})
    assert len(Section.model_validate(section()).paragraphs) == 3
    assert search_etfs('VOO')[0]['instrument_id'] == 'US:VOO'
    paragraphs = ['현재 구성은 유지해도 됩니다. 추가 투자는 분산에 우선 배치하세요.'] * 3
    assert Section.model_validate({**section(), 'paragraphs': paragraphs}).paragraphs == paragraphs


def test_sentence_boundary_preserves_etf_initialisms_and_decimal_values():
    assert not multiple_sentences('iShares U.S. Treasury Bond ETF를 3.5% 담아요.')
    assert multiple_sentences('올라요.내려요.')
    assert multiple_sentences('올라요. 내려요.')
    assert multiple_sentences('“올라요.” 내려요.')
    assert not multiple_sentences('결론은 “유지해요.”')


def test_a_held_stock_can_be_kept_or_reduced_but_never_bought_and_no_new_stock_is_published():
    # 고객이 넣은 개별주는 분석·유지·축소할 수 있다. AI가 새 개별주를 담거나 보유 개별주를 더 사게 하면 안 된다.
    from edge_portfolio.catalog import canonicalize
    def plan(rows, funded=False):
        holdings = [('US:AAPL', 500000), ('US:VOO', 500000), *([('CASH:KRW', 1000000)] if funded else [])]
        request = canonicalize(AnalysisInput(holdings=[{'name': 'x', 'instrument_id': key, 'amount_krw': amount, 'kind': 'cash' if key == 'CASH:KRW' else 'etf'}
                                                       for key, amount in holdings], horizon='10년 이상'))
        raw = final_payload()
        raw['recommendation']['allocations'] = [{'instrument_id': key, 'account': '일반 계좌', 'weight_pct': weight} for key, weight in rows]
        return publish_recommendation(FinalReport.model_validate(raw), request, calculate(request), SOURCES, None)['recommendation']
    kept = plan([('US:AAPL', 25), ('US:VOO', 75)], funded=True)
    assert next(row for row in kept['allocations'] if row['instrument_id'] == 'US:AAPL')['kind'] == 'stock'
    assert next(r for r in kept['execution']['rows'] if r['instrument_id'] == 'US:AAPL')['buy_krw'] == '0', 'new cash goes to the ETF'
    with pytest.raises(ValueError, match='never bought more'):
        plan([('US:AAPL', 40), ('US:VOO', 60)], funded=True)
    with pytest.raises(ValueError, match='never bought more'):
        plan([('US:AAPL', 60), ('US:VOO', 40)])
    assert plan([('US:AAPL', 30), ('US:VOO', 70)])['allocations'][0]['weight_pct'] == 30
    with pytest.raises(ValueError, match='stock the customer already holds'):
        plan([('US:MSFT', 20), ('US:VOO', 80)])


def test_unknown_current_weights_do_not_force_selling_a_held_stock():
    # 이름만 입력해 현재 비중을 모르면 '늘렸다'고 판단할 기준이 없다. 보유 개별주를 유지하는 안을 막지 않는다.
    from edge_portfolio.catalog import canonicalize
    request = canonicalize(AnalysisInput(holdings=[{'name': 'x', 'instrument_id': key} for key in ['US:AAPL', 'US:VOO']],
                                         horizon='10년 이상', input_mode='names'))
    raw = final_payload()
    raw['recommendation']['allocations'] = [{'instrument_id': 'US:AAPL', 'account': '일반 계좌', 'weight_pct': 50},
                                            {'instrument_id': 'US:VOO', 'account': '일반 계좌', 'weight_pct': 50}]
    assert publish_recommendation(FinalReport.model_validate(raw), request, calculate(request), SOURCES)['recommendation']['allocations'][0]['kind'] == 'stock'
