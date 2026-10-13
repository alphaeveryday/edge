from datetime import date, timedelta

import pytest

from edge_analysis_v2.tools.execution import ToolInputError
from edge_analysis_v2.tools.fixture_data.returns import daily, window


def fixture(days=30):
    dates = [(date(2026, 9, 1) + timedelta(days=n)).isoformat() for n in range(days)]
    at = '2026-08-01T00:00:00+09:00'
    market, level = [], 100.0
    for n, day in enumerate(dates):
        level *= 1.01 if n % 2 else 0.995
        market.append({'date': day, 'close': level, 'available_at': at})
    # A moves twice as far as the market every day until the window; B never moves.
    prices, a = [], 100.0
    for n, day in enumerate(dates):
        a *= 1 + 2 * (0.01 if n % 2 else -0.005)
        prices += [{'instrument_id': 'A', 'date': day, 'close': a, 'available_at': at},
                   {'instrument_id': 'B', 'date': day, 'close': 50, 'available_at': at},
                   {'instrument_id': 'ETF', 'date': day, 'close': a, 'available_at': at}]
    return {'context': {'etf_code': 'ETF', 'analysis_at': '2026-10-15T19:00:00+09:00'}, 'market_prices': market, 'prices': prices,
            'instruments': [{'instrument_id': 'A', 'name': '가'}, {'instrument_id': 'B', 'name': '나'}],
            'holdings': [{'instrument_id': 'A', 'weight': 0.6, 'as_of_date': '2026-09-01', 'available_at': at},
                         {'instrument_id': 'B', 'weight': 0.4, 'as_of_date': '2026-09-01', 'available_at': at}]}, dates


def test_the_market_share_is_beta_times_the_market_and_the_rest_is_the_excess():
    data, dates = fixture()
    result = window(data, dates[-3], dates[-1])
    rows = {r[0]: dict(zip(result['constituents']['columns'], r)) for r in result['constituents']['rows']}
    assert rows['A']['beta'] == pytest.approx(2, abs=0.01) and rows['A']['beta_observations'] == 27
    assert rows['A']['market_share_pct'] == pytest.approx(2 * result['market']['return_pct'], abs=0.01)
    assert rows['A']['excess_return_pct'] == pytest.approx(rows['A']['return_pct'] - rows['A']['market_share_pct'], abs=0.001)
    # A stock that ignored the market keeps its whole move as excess.
    assert rows['B']['beta'] == 0 and rows['B']['excess_return_pct'] == rows['B']['return_pct'] == 0
    assert result['etf']['return_pct'] == rows['A']['return_pct']


def test_too_short_a_history_gives_the_return_without_a_beta():
    data, dates = fixture(days=12)
    etf = window(data, dates[-3], dates[-1])['etf']
    assert etf['beta'] is None and etf['excess_return_pct'] is None and etf['return_pct'] is not None


def test_a_day_without_a_market_close_is_refused_with_the_usable_range():
    data, dates = fixture()
    with pytest.raises(ToolInputError, match=dates[-1]):
        window(data, dates[-1], '2026-10-15')


def test_a_day_in_the_daily_table_equals_the_one_day_window_and_contributions_use_the_weight():
    data, dates = fixture()
    one = window(data, dates[-2], dates[-1])
    table = daily(data, 5)
    last = dict(zip(table['columns'], table['rows'][-1]))
    assert len(table['rows']) == 5 and last['date'] == dates[-1]
    assert (last['return_pct'], last['excess_return_pct'], last['market_return_pct']) == (
        one['etf']['return_pct'], one['etf']['excess_return_pct'], one['market']['return_pct'])
    a = next(dict(zip(one['constituents']['columns'], r)) for r in one['constituents']['rows'] if r[0] == 'A')
    assert a['contribution_pct'] == pytest.approx(0.6 * a['return_pct'], abs=0.001)
    assert daily(data, 3, 'B')['rows'][0][1] == 0
    with pytest.raises(ToolInputError):
        daily(data, 5, 'UNKNOWN')
