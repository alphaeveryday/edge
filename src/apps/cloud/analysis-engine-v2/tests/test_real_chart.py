"""Close-only sources support RSI but never fabricated high/low or turnover."""
import pytest

from edge_analysis_v2.tools.fixture_data import make_fixture
from edge_analysis_v2.tools.fixture_data import chart, instrument_factors


def close_only():
    data = make_fixture('baseline')
    for row in data['prices']:
        row['high'] = row['low'] = row['turnover'] = None
    for row in data.get('price_snapshots', []):
        row['high'] = row['low'] = None
    if 'price_snapshot' in data:
        data['price_snapshot']['high'] = data['price_snapshot']['low'] = None
    return data


def test_rsi_survives_missing_ranges_but_bottom_does_not():
    original=make_fixture('baseline'); expected=chart.indicators(original)['momentum']
    result=chart.indicators(close_only())
    assert result['momentum']==expected
    assert result['bottom'] is None


def test_factor_cards_do_not_replace_missing_turnover_or_atr_with_zero():
    data=close_only()
    result=instrument_factors.read(data,data['context']['etf_code'],['chart'])['chart']
    assert result['ma20_distance_pct'] is not None
    assert result['momentum_index'] is not None
    assert result['bottom_index'] is None
    assert result['atr14_pct'] is None
    assert result['turnover_ratio_previous_day'] is None


def test_missing_bottom_never_proves_a_zone_transition():
    result=chart.transition(close_only(),'bottom')
    assert result['transitions'] is None
    assert result['held_zone'] is None


def test_invalid_present_range_still_fails():
    data=close_only()
    next(r for r in data['prices'] if r['instrument_id']==data['context']['etf_code'])['low']=-1
    with pytest.raises(ValueError,match='price'):
        chart.indicators(data)
