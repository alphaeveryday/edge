"""Current-basket evidence must not mix ETF trades or hide correctable data gaps."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from edge_analysis_v2.sources import database
from edge_analysis_v2.tools.execution import AuditedExecution, ToolExecutionError, ToolInputError
from edge_analysis_v2.tools.fixture_data import FixtureTools, make_fixture
from test_fixture_flow import flow_fixture


def call(data, **arguments):
    return FixtureTools(data).call('sum_weighted_net_flow',
        {'investor': 'foreign', 'lookback_days': 5} | arguments)['result']


@pytest.mark.parametrize('days', [5, 20])
def test_one_latest_snapshot_supports_tools_and_factor_history(days):
    data = make_fixture()
    latest = data['context']['flow_as_of_date']
    data['holdings'] = [dict(r, as_of_date=latest, available_at=latest+'T23:59:00+09:00')
                        for r in data['holdings'] if r['as_of_date'] == max(h['as_of_date'] for h in data['holdings'])]
    tools = FixtureTools(data)
    result = call(data, lookback_days=days)
    factor = tools.call('get_instrument_factors', {'instrument_id': data['context']['etf_code'],
                                                  'factors': ['flow']})['result']['flow']
    assert len(factor['history']['rows']) == 30
    assert result['amount_krw'] == pytest.approx(sum(row[1] for row in factor['history']['rows'][-days:]))
    assert result['holdings_as_of'] == factor['holdings_as_of'] == latest
    assert result['weight_basis'] == factor['weight_basis'] == 'latest_snapshot_at_analysis'
    assert result['observed_weight_ratio'] == factor['observed_weight_ratio']


def test_etf_own_flow_is_neither_initial_input_nor_part_of_weighted_evidence():
    data = make_fixture()
    before = call(data)
    own = [dict(r, instrument_id=data['context']['etf_code'], net_amount_krw=999999999999)
           for r in data['flow'] if r['instrument_id'] == '000660']
    data['flow'] += own
    tools = FixtureTools(data)
    assert data['context']['etf_code'] not in tools.initial_input()['flow']
    assert '000660' in tools.initial_input()['flow']
    assert call(data) == before
    for name in ('sum_investor_net_flow', 'calculate_investor_flow'):
        args = dict(instrument_id=data['context']['etf_code'], investor='foreign', lookback_days=5)
        if name.startswith('calculate'):
            args.update(operation='sum', direction='none')
        with pytest.raises(ToolInputError, match='UNSUPPORTED_ETF_FLOW.*sum_weighted_net_flow'):
            tools.call(name, args)


@pytest.mark.parametrize('change,code', [('missing', 'MISSING_FLOW'), ('duplicate', 'DUPLICATE_FLOW'),
    ('unfinalized', 'UNFINALIZED_FLOW'), ('numeric', 'INVALID_FLOW_AMOUNT')])
def test_domain_failure_reaches_model_with_target_date_and_no_blind_retry(change, code):
    data = flow_fixture()
    if change == 'missing':
        data['flow'].pop(0)
    elif change == 'duplicate':
        data['flow'].append(deepcopy(data['flow'][0]))
    elif change == 'unfinalized':
        data['flow'][0]['finalized'] = False
    else:
        data['flow'][0]['net_amount_krw'] = 'password=secret'
    tools, store = FixtureTools(data), Mock()
    runtime = AuditedExecution(tools.call, store, definitions=tools.definitions,
        analysis_kind='movement', analysis_id='test', context=data['context'])
    with pytest.raises(ToolExecutionError) as raised:
        runtime.call('sum_weighted_net_flow', dict(investor='foreign', lookback_days=5))
    message = str(raised.value)
    assert code in message and 'A' in message and '2026-09-14' in message and 'foreign' in message
    assert '5' in message and 'Do not retry' in message
    assert 'secret' not in message and 'Tool execution failed' not in message
    assert raised.value.tool_run_id in message
    assert code in store.save_run.call_args.kwargs['error_message']


def test_missing_holdings_and_calendar_are_actionable_not_generic_failures():
    data = flow_fixture()
    data['holdings'] = []
    with pytest.raises(ToolInputError, match='NO_HOLDINGS.*analysis'):
        call(data)
    data = flow_fixture()
    data['trading_dates'].pop(0)
    with pytest.raises(ToolInputError, match='INSUFFICIENT_CALENDAR.*5'):
        call(data)


def test_flow_source_query_excludes_etf_and_former_constituents(monkeypatch):
    data = flow_fixture()
    data['source_instrument_ids'] = {'ETF': 'etf-id', 'A': 'a-id', 'B': 'b-id', 'OLD': 'old-id'}
    queries = []
    monkeypatch.setattr(database, '_rows', lambda c, sql, args: queries.append((sql, args)) or [])
    database.load_flow(object(), data)
    assert set(queries[0][1][0]) == {'a-id', 'b-id'}


@pytest.mark.parametrize('arguments', [{'investor': 'foreign'},
    {'investor': 'foreign', 'lookback_days': 5, 'password': 'secret'}, None])
def test_argument_shape_errors_are_safe_and_correctable(arguments):
    tools, store = FixtureTools(flow_fixture()), Mock()
    runtime = AuditedExecution(tools.call, store, definitions=tools.definitions,
        analysis_kind='outlook', analysis_id='test', context=tools.fixture['context'])
    with pytest.raises(ToolExecutionError, match='INVALID_FLOW_ARGUMENTS') as raised:
        runtime.call('sum_weighted_net_flow', arguments)
    assert 'schema' in str(raised.value) and 'secret' not in str(raised.value)


@pytest.mark.parametrize('target', [None, []])
def test_non_string_target_is_a_safe_input_error(target):
    with pytest.raises(ToolInputError, match='UNSUPPORTED_FLOW_TARGET'):
        FixtureTools(flow_fixture()).call('sum_investor_net_flow',
            dict(instrument_id=target, investor='foreign', lookback_days=5))


def test_etf_own_dates_cannot_shorten_constituent_preview():
    data = make_fixture()
    before = FixtureTools(data).initial_input()['flow']
    data['flow'].append(dict(instrument_id=data['context']['etf_code'], date='2026-09-19',
        investor='foreign', net_amount_krw=10, available_at='2026-09-19T18:00:00+09:00', finalized=True))
    data['context']['flow_as_of_date'] = '2026-09-20'
    assert FixtureTools(data).initial_input()['flow'] == before


def test_factor_history_preserves_first_gap_and_still_exposes_other_factors():
    data = make_fixture()
    day = data['context']['flow_as_of_date']
    for row in data['flow']:
        if row['date'] == day and row['investor'] == 'foreign':
            row['net_amount_krw'] = 'secret'
    data['flow'] = [r for r in data['flow'] if not (r['date'] == day and r['investor'] == 'individual')]
    result = FixtureTools(data).call('get_instrument_factors',
        dict(instrument_id=data['context']['etf_code'], factors=['flow', 'macro']))['result']
    assert 'INVALID_FLOW_AMOUNT' in result['unavailable']['flow']
    assert 'MISSING_FLOW' not in result['unavailable']['flow']
    assert 'secret' not in result['unavailable']['flow']
    assert result['macro'] is not None
