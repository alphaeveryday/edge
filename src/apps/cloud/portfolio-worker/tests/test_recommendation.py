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
        {'instrument_id': 'CASH:KRW', 'account': '일반 계좌', 'weight_pct': 20}]
    result = publish(raw)
    plan = result['recommendation']
    assert sum(int(h['amount_krw']) for h in plan['allocations']) == 1000001
    assert plan['allocations'][0]['amount_krw'] == '500001'
    assert plan['evaluation']['overall'] == 3.5
    assert [a['action'] for a in plan['actions']] == ['줄이기', '새로 담기', '새로 담기']
    assert result['insights'][0]['title'] == '지금 가장 먼저 판단할 것'
    assert plan['expected_return_1y'] is None and plan['historical_performance'] is None


@pytest.mark.parametrize('change,match', [
    ({'weight_pct': 99}, 'total'), ({'instrument_id': 'US:INVENTED'}, 'registered'),
    ({'account': 'ISA'}, 'not selected')])
def test_invalid_model_recommendations_are_not_published(change, match):
    raw = final_payload(); raw['recommendation']['allocations'][0].update(change)
    with pytest.raises(ValueError, match=match): publish(raw)


def test_account_restriction_and_unknown_budget_are_preserved():
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO'}], horizon='10년 이상', input_mode='names', preferences={'accounts': ['일반 계좌', 'ISA']})
    raw = final_payload(); raw['recommendation']['allocations'][0]['account'] = 'ISA'
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


def test_bullets_have_bounded_count_and_one_sentence_not_multiple_paragraphs():
    for paragraphs in [['설명'] * 2, ['설명'] * 6, ['첫 문장이에요. 두 번째 문장이에요.'] * 3]:
        with pytest.raises(ValueError): Section.model_validate({**section(), 'paragraphs': paragraphs})
    assert len(Section.model_validate(section()).paragraphs) == 3
    assert search_etfs('VOO')[0]['instrument_id'] == 'US:VOO'


def test_sentence_boundary_preserves_etf_initialisms_and_decimal_values():
    assert not multiple_sentences('iShares U.S. Treasury Bond ETF를 3.5% 담아요.')
    assert multiple_sentences('올라요.내려요.')
    assert multiple_sentences('올라요. 내려요.')
    assert multiple_sentences('“올라요.” 내려요.')
    assert not multiple_sentences('결론은 “유지해요.”')
