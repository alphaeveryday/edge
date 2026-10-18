"""Missing observations permit another research path; corrupt observations stop execution."""
import pytest

from edge_analysis_v2.tools.execution import ToolInputError
from edge_analysis_v2.tools.fixture_data import FixtureTools
from test_fixture_chart import chart_fixture
from test_financial_observations import data as financial_data
from test_fixture_macro import source as macro_source


@pytest.mark.parametrize('name,args', [
    ('get_instrument_factors', {'instrument_id': 'unknown'}),
    ('get_instrument_factors', {'instrument_id': '000660', 'factors': ['unknown']}),
    ('get_instrument_factor_table', {'factors': [{}]}),
    ('get_instrument_factor_table', {'top_n': 0}),
    ('get_financial_observations', {'instrument_id': 'unknown'}),
    ('compare_financial_observations', {'previous_id': 'missing', 'current_id': 'new'}),
    ('compare_financial_observations', {'previous_id': 'new', 'current_id': 'old'}),
    ('calculate_valuation_range', {'eps_id': 'new', 'per_low': 'bad', 'per_high': 10}),
    ('calculate_valuation_range', {'eps_id': 'new', 'per_low': 20, 'per_high': 10}),
])
def test_invalid_selection_is_recoverable_with_actionable_guidance(name, args):
    with pytest.raises(ToolInputError) as caught:
        FixtureTools(financial_data()).call(name, args)
    assert any(word in str(caught.value).lower() for word in ('select', 'use', 'choose', 'must', 'required'))


@pytest.mark.parametrize('prices', [0, 8])
def test_absent_or_short_price_history_does_not_abort_other_research(prices):
    fixture = chart_fixture()
    fixture['prices'] = fixture['prices'][-prices:] if prices else []
    with pytest.raises(ToolInputError, match='Do not retry'):
        FixtureTools(fixture).call('calculate_chart_indicators', {})


@pytest.mark.parametrize('series,previous', [('bad', '2026-09-18T09:00:00+09:00'),
                                            ('kr_10y_yield', 'bad'),
                                            ('kr_10y_yield', '2026-09-17T09:00:00+09:00')])
def test_macro_selection_errors_offer_valid_observation_discovery(series, previous):
    with pytest.raises(ToolInputError, match='get_macro_observations|series'):
        FixtureTools(macro_source()).call('compare_macro_observations',
            {'series': series, 'previous_at': previous, 'current_at': '2026-09-21T09:00:00+09:00', 'operation': 'difference'})


def test_missing_valuation_price_is_not_a_corrupt_price():
    fixture = financial_data(); fixture['prices'] = []
    with pytest.raises(ToolInputError, match='Do not retry'):
        FixtureTools(fixture).call('calculate_valuation_range', {'eps_id': 'new', 'per_low': 8, 'per_high': 10})


@pytest.mark.parametrize('problem', ['duplicate_price', 'negative_price', 'macro_unit', 'duplicate_financial'])
def test_internal_source_corruption_remains_fatal(problem):
    if problem in ('duplicate_price', 'negative_price'):
        fixture = chart_fixture()
        if problem == 'duplicate_price': fixture['prices'].append(fixture['prices'][-1].copy())
        else: fixture['prices'][-1]['close'] = -1
        name, args = 'calculate_chart_indicators', {}
    elif problem == 'macro_unit':
        fixture = macro_source(); fixture['macro'][0]['unit'] = 'wrong'
        name, args = 'get_macro_observations', {'series': 'kr_10y_yield'}
    else:
        fixture = financial_data(); fixture['financial_observations'].append(fixture['financial_observations'][0].copy())
        name, args = 'get_financial_observations', {'instrument_id': '000660'}
    with pytest.raises(ValueError) as caught:
        FixtureTools(fixture).call(name, args)
    assert not isinstance(caught.value, ToolInputError)
