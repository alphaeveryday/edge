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


def test_unchanged_percentage_does_not_hide_a_required_top_up():
    from edge_portfolio.decision import Decision
    request = AnalysisInput(holdings=[{'name': key, 'instrument_id': key, 'amount_krw': 500000} for key in ['US:VOO', 'US:QQQ']],
                            horizon='10년 이상', question='추가 100만원')
    raw = final_payload()
    raw['recommendation']['allocations'] = [{'instrument_id': key, 'account': '일반 계좌', 'weight_pct': weight}
        for key, weight in [('US:VOO', 50), ('US:QQQ', 30), ('US:VTI', 20)]]
    decision = Decision(kind='rebalance', funding_text='추가 100만원')
    plan = publish_recommendation(FinalReport.model_validate(raw), request, calculate(request), SOURCES, decision)['recommendation']
    voo = next(action for action in plan['actions'] if action['instrument_id'] == 'US:VOO')
    assert voo['before_pct'] == voo['after_pct'] == 50
    assert voo['buy_krw'] == '500000'
    assert voo['action'] == '추가 매수'
    assert plan['total_krw'] == '2000000'


def test_recommended_rating_is_required():
    raw = final_payload()
    raw['recommendation']['evaluation']['scores'] = None
    with pytest.raises(ValueError):
        FinalReport.model_validate(raw)


def test_suitable_recommendation_keeps_honest_scores_below_four():
    raw = final_payload()
    scores = {'leadership': 3, 'profitability': 3.5, 'stability': 4.5}
    raw['recommendation']['evaluation']['scores'] = scores
    result = publish(raw)
    assert result['recommendation']['evaluation']['scores'] == scores
    assert result['recommendation']['evaluation']['overall'] == 3.7


def test_cash_current_rating_can_be_absent_without_hiding_recommended_stars():
    raw = final_payload()
    raw['current_evaluation']['scores'] = None
    raw['current_evaluation']['reason']['evidence'] = []
    result = publish(raw)
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
