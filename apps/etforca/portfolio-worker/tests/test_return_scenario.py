import math
from statistics import variance
from datetime import date

import pandas as pd
import pytest

from edge_portfolio.market_data import upper_sigma_scenario, enrich_report


def history(rates):
    days = pd.bdate_range(end='2026-10-20', periods=len(rates) + 1)
    value = 1000000
    points = [{'date': days[0].date().isoformat(), 'recommended': value}]
    for day, rate in zip(days[1:], rates):
        value *= 1 + rate
        points.append({'date': day.date().isoformat(), 'recommended': value})
    return {'points': points}


def test_upper_sigma_uses_the_same_trailing_year_and_sample_variance():
    data = history([.4] * 20 + [.01, -.012] * 131)
    result = upper_sigma_scenario(data)
    window = [p for p in data['points'] if p['date'] >= result['start_date']]
    rates = [b['recommended'] / a['recommended'] - 1 for a, b in zip(window, window[1:])]
    base = (window[-1]['recommended'] / window[0]['recommended'] - 1) * 100
    sigma = math.sqrt(variance(rates) * len(rates)) * 100
    assert result['percent'] == round(base + sigma, 2)
    assert result['annualized_variance'] == pytest.approx(variance(rates) * len(rates))
    assert result['historical_return_pct'] < 0, 'Old high-return observations must not enter the trailing year'


def test_offsets_in_portfolio_value_reduce_sigma_instead_of_adding_asset_sigmas():
    data = history([0] * 262)
    for i, point in enumerate(data['points']):
        first = 100 + (i % 2) * 10
        second = 200 - first
        point['recommended'] = .5 * first + .5 * second
    result = upper_sigma_scenario(data)
    assert result['annualized_sigma_pct'] == 0
    assert result['percent'] == 0


def test_short_history_is_not_annualized_and_negative_scenarios_are_not_floored():
    assert upper_sigma_scenario(history([.001] * 100)) is None
    assert upper_sigma_scenario(history([-.001] * 262))['percent'] < 0


async def test_publication_uses_prices_even_without_any_model_forecast():
    class Market:
        baseline_kind = 'new_cash'
        async def get(self, rows):
            return {'status': 'available', 'history': history([.001, -.0005] * 131), 'return_1y_pct': 6}
        def dividends(self, instrument):
            return None
    report = {'recommendation': {'allocations': [{'instrument_id': 'KR:069500', 'weight_pct': 100}],
                                 'expected_return_1y': 99, 'return_forecast': {'reason': 'stale'}}}
    await enrich_report(report, None, Market(), [])
    result = report['recommendation']
    assert result['return_forecast']['kind'] == 'upper_sigma_1y'
    assert result['expected_return_1y'] == result['return_forecast']['percent'] != 99
