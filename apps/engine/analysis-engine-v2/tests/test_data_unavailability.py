"""A fixed snapshot gap is not an argument retry; known bad source is not absent data."""
import pytest

from edge_analysis_v2.tools.execution import ToolInputError
from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.tools.fixture_data import returns
from test_fixture_flow import flow_fixture
from test_fixture_valuation import valuation_fixture
from test_fixture_chart import chart_fixture
from test_window_returns import fixture as return_fixture


@pytest.mark.parametrize('case', ['chart', 'flow', 'unfinalized', 'calendar', 'holdings', 'coverage',
                                  'valuation_price', 'valuation_quarters', 'valuation_coverage', 'returns'])
def test_known_data_gap_has_nonretryable_type(case):
    if case == 'chart':
        data = chart_fixture(); data['prices'] = []
        name, args = 'calculate_chart_indicators', {}
    elif case in ('valuation_price', 'valuation_quarters', 'valuation_coverage'):
        data = valuation_fixture()
        if case == 'valuation_price': data['prices'] = []
        else: data['financials'] = []
        name, args = ('calculate_weighted_valuation', {}) if case == 'valuation_coverage' else ('calculate_valuation', {'instrument_id': 'A'})
    elif case == 'returns':
        data, _ = return_fixture(); data['market_prices'] = []
        name, args = 'get_daily_returns', {'lookback_days': 5}
    else:
        data = flow_fixture()
        if case == 'flow': data['flow'] = []
        elif case == 'unfinalized': data['flow'][0]['finalized'] = False
        elif case == 'calendar': data['trading_dates'] = []
        elif case == 'holdings': data['holdings'] = []
        elif case == 'coverage':
            for row in data['holdings']: row['weight'] /= 2
        name, args = 'calculate_weighted_flow', {'investor': 'foreign', 'lookback_days': 5, 'operation': 'sum', 'direction': 'none'}
    with pytest.raises(ToolInputError) as caught:
        FixtureTools(data).call(name, args)
    assert type(caught.value).__name__ == 'ToolDataUnavailableError'
    assert caught.value.code == 'data_unavailable'
    assert caught.value.retryable is False


@pytest.mark.parametrize('case', ['flow_duplicate', 'flow_amount', 'holdings_duplicate', 'valuation_duplicate'])
def test_existing_source_refusals_are_not_labeled_missing_or_retryable(case):
    data = flow_fixture()
    name, args = 'calculate_weighted_flow', {'investor': 'foreign', 'lookback_days': 5, 'operation': 'sum', 'direction': 'none'}
    if case == 'flow_duplicate': data['flow'].append(data['flow'][0].copy())
    elif case == 'flow_amount': data['flow'][0]['net_amount_krw'] = '10'
    elif case == 'holdings_duplicate': data['holdings'].append(data['holdings'][0].copy())
    else:
        data = valuation_fixture(); data['prices'].append(data['prices'][0].copy())
        name, args = 'calculate_weighted_valuation', {}
    with pytest.raises(ToolInputError) as caught:
        FixtureTools(data).call(name, args)
    assert type(caught.value).__name__ == 'ToolSourceDataError'
    assert caught.value.code == 'invalid_source_data'
    assert caught.value.retryable is False


def test_unknown_return_target_is_still_correctable_even_if_no_prices_exist():
    data, _ = return_fixture(); data['prices'] = []
    with pytest.raises(ToolInputError) as caught:
        returns.daily(data, 5, 'UNKNOWN')
    assert type(caught.value) is ToolInputError
