"""A plausible value is not evidence when its issuer, period or release is wrong."""
from copy import deepcopy

import pytest

from edge_analysis_v2.fixture_tools import FixtureTools, make_fixture


def data():
    fixture = make_fixture()
    common = dict(instrument_id='000660', metric='eps', unit='KRW_per_share',
                  period='2027', kind='estimate', author='가상 증권사',
                  news_id='n1', available_at='2026-09-18T18:00:00+09:00')
    fixture['financial_observations'] = [
        common | dict(observation_id='old', value=6500, published_at='2026-09-17T08:00:00+09:00'),
        common | dict(observation_id='new', value=7200, published_at='2026-09-18T08:00:00+09:00')]
    return fixture


def test_revision_returns_source_values_and_independent_arithmetic():
    result = FixtureTools(data()).call('compare_financial_observations',
                                     {'previous_id':'old','current_id':'new'})['result']
    assert result['difference'] == 700
    assert result['percent_change'] == pytest.approx(700 / 6500 * 100)
    assert result['previous']['kind'] == result['current']['kind'] == 'estimate'
    assert result['current']['period'] == '2027'


@pytest.mark.parametrize('change', [dict(instrument_id='005930'), dict(period='2028'),
    dict(unit='USD_per_share'), dict(metric='bps'),
    dict(published_at='2026-09-22T08:00:00+09:00'),
    dict(available_at='2026-09-22T08:00:00+09:00')])
def test_equal_numbers_from_wrong_domain_or_future_cannot_justify_comparison(change):
    fixture = data()
    fixture['financial_observations'][1].update(change)
    with pytest.raises(ValueError):
        FixtureTools(fixture).call('compare_financial_observations', {'previous_id':'old','current_id':'new'})


def test_reordering_source_rows_does_not_change_calculation():
    original = data()
    changed = deepcopy(original)
    changed['financial_observations'].reverse()
    args = {'previous_id':'old','current_id':'new'}
    assert FixtureTools(original).call('compare_financial_observations',args)['result'] == FixtureTools(changed).call('compare_financial_observations',args)['result']


def test_missing_forecast_remains_empty_and_cannot_be_invented():
    fixture = data()
    fixture['financial_observations'] = []
    result = FixtureTools(fixture).call('get_financial_observations', {'instrument_id':'000660'})['result']
    assert result['rows'] == []
    with pytest.raises(ValueError):
        FixtureTools(fixture).call('calculate_valuation_range', {'eps_id':'new','per_low':9,'per_high':11})


def test_conditional_price_range_preserves_assumptions_and_numeric_provenance():
    fixture = data()
    result = FixtureTools(fixture).call('calculate_valuation_range', {'eps_id':'new','per_low':9,'per_high':11})['result']
    assert result['price_low'] == 64800 and result['price_high'] == 79200
    assert result['eps_observation']['observation_id'] == 'new'
    assert result['per_assumptions'] == {'low':9,'high':11}
    assert result['return_high_pct'] == pytest.approx((79200 / result['current_price'] - 1) * 100)


@pytest.mark.parametrize('bounds', [(0,11), (12,9), (-1,9), (True,11)])
def test_invalid_multiple_is_not_a_price_target(bounds):
    with pytest.raises(ValueError):
        FixtureTools(data()).call('calculate_valuation_range', {'eps_id':'new','per_low':bounds[0],'per_high':bounds[1]})


def test_twenty_day_average_does_not_require_sixty_day_chart_card_history():
    fixture = data()
    cutoff = fixture['trading_dates'][-20]
    fixture['prices'] = [r for r in fixture['prices'] if r['date'] >= cutoff]
    result = FixtureTools(fixture).call('get_chart_metrics', {'metrics':['ma20_distance_pct']})['result']
    assert [r['key'] for r in result['metrics']] == ['ma20_distance_pct']
    with pytest.raises(ValueError):
        FixtureTools(fixture).call('get_chart_metrics', {'metrics':['ma60_direction']})


def test_sum_interface_needs_no_direction_and_preserves_negative_days():
    fixture = data()
    tools = FixtureTools(fixture)
    args = {'investor':'foreign','lookback_days':5}
    result = tools.call('sum_weighted_net_flow',args)['result']
    original = tools.call('calculate_weighted_flow',args | {'operation':'sum','direction':'none'})['result']
    assert result == original
    descriptor = next(s['function'] for s in tools.schemas if s['function']['name']=='sum_weighted_net_flow')
    assert 'direction' not in descriptor['parameters']['properties']


@pytest.mark.parametrize('previous', [0,-100])
def test_nonpositive_previous_value_is_not_a_percentage_growth_rate(previous):
    fixture = data()
    fixture['financial_observations'][0]['value'] = previous
    result = FixtureTools(fixture).call('compare_financial_observations',{'previous_id':'old','current_id':'new'})['result']
    assert result['percent_change'] is None
    assert result['difference'] == 7200-previous


def test_duplicate_source_id_is_not_resolved_by_incidental_row_order():
    fixture = data()
    fixture['financial_observations'].append(dict(fixture['financial_observations'][0],value=9000))
    with pytest.raises(ValueError,match='duplicate financial'):
        FixtureTools(fixture).call('get_financial_observations',{'instrument_id':'000660'})


def test_chart_result_exposes_price_for_an_auditable_price_sentence():
    fixture = data()
    result = FixtureTools(fixture).call('get_chart_metrics',{'metrics':['ma20_distance_pct']})['result']
    assert result['price'] == fixture['price_snapshots'][-1]['price']
    assert result['price_at'] == fixture['price_snapshots'][-1]['observed_at']
