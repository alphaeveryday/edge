"""Agent data tables preserve facts without repeating subject metadata."""
from copy import deepcopy

import pytest

from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.tools.fixture_data.demo import build_demo_fixture


def test_flow_pivot_preserves_each_investor_amount_null_and_zero():
    fixture = build_demo_fixture()
    fixture['flow'][0]['net_amount_krw'] = 0
    fixture['flow'][1]['net_amount_krw'] = None
    missing = fixture['flow'].pop(2)
    original = deepcopy(fixture)
    result = FixtureTools(fixture).initial_input()['flow']
    for target, body in result.items():
        assert body['columns'] == ['date', 'foreign', 'institution', 'individual']
        assert body['unit'] == 'KRW'
        assert len(body['rows']) == 30
        lookup = {r['date']: r for r in [dict(zip(body['columns'], row)) for row in body['rows']]}
        for raw in fixture['flow']:
            if raw['instrument_id'] == target:
                assert lookup[raw['date']][raw['investor']] == raw['net_amount_krw']
    assert result[missing['instrument_id']]['rows'][0] == [missing['date'], 0, None, None]
    assert fixture == original


def test_duplicate_flow_cannot_be_silently_overwritten():
    fixture = build_demo_fixture()
    fixture['flow'].append(deepcopy(fixture['flow'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        FixtureTools(fixture).initial_input()


def test_price_tables_preserve_values_and_timestamp_precision():
    fixture = build_demo_fixture()
    payload = FixtureTools(fixture).initial_input()
    for target, body in payload['prices'].items():
        raw = [r for r in fixture['prices'] if r['instrument_id'] == target][-40:]
        assert body['rows'] == [[r[c] for c in body['columns']] for r in raw]
        assert 'instrument_id' not in body['columns']
    body = payload['price_snapshots']
    assert body['instrument_id'] == fixture['context']['etf_code']
    assert body['columns'] == ['at', 'price', 'high', 'low', 'available_at']
    assert body['rows'] == [[r['observed_at'], r['price'], r['high'], r['low'], r['available_at']] for r in fixture['price_snapshots']]


def test_macro_initial_input_and_tool_return_have_identical_table_contract():
    tools = FixtureTools(build_demo_fixture())
    initial = tools.initial_input()
    for series, body in initial['macro'].items():
        assert body == tools.call('get_macro_observations', {'series': series})['result']
        assert body['columns'] == ['at', 'value', 'available_at'] + (['reference_period'] if series == 'kr_cpi_yoy' else [])
    definition = next(d for d in tools.definitions if d['function_name'] == 'get_macro_observations')
    assert definition['version'] == 'v2'
