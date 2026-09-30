"""The same audited observations assemble ETF cards without changing their scope."""
from copy import deepcopy

import pytest

from edge_analysis_v2.storage.factors import project_factor_metrics, _matches_calculation, prepare_metrics
from edge_analysis_v2.tools.fixture_data import FixtureTools, make_fixture


def test_flat_factor_results_keep_card_values_times_and_run_reference():
    fixture = make_fixture()
    output = FixtureTools(fixture).call('get_instrument_factors', {'instrument_id': '091160'})
    metrics = project_factor_metrics(output, '091160')
    assert set(metrics) == {'차트', '매크로', '밸류', '수급'}
    rows = prepare_metrics(metrics)
    assert all(row['tool_run_ids'] == [output['tool_run_id']] for row in rows)
    card = next(r for r in metrics['차트'] if r['key'] == 'ma20_distance_pct')
    assert card['value'] == output['result']['chart']['ma20_distance_pct']
    assert card['observed_at'] == output['result']['chart']['observed_at']
    run = dict(function_name='get_instrument_factors', tool_run_id=output['tool_run_id'],
               arguments={'instrument_id': '091160'}, output=output)
    assert all(_matches_calculation(row, run, '091160') for row in rows)
    forged = deepcopy(run)
    forged['arguments']['instrument_id'] = '000660'
    assert not _matches_calculation(rows[0], forged, '091160')
    forged = deepcopy(run)
    forged['arguments']['factors'] = ['flow']
    chart_row = next(r for r in rows if r['factor_type'] == '차트')
    assert not _matches_calculation(chart_row, forged, '091160')


def test_individual_or_partial_query_cannot_impersonate_whole_etf_cards():
    tools = FixtureTools(make_fixture())
    individual = tools.call('get_instrument_factors', {'instrument_id': '000660'})
    with pytest.raises(ValueError, match='instrument'):
        project_factor_metrics(individual, '091160')
    partial = tools.call('get_instrument_factors', {'instrument_id': '091160', 'factors': ['flow']})
    assert set(project_factor_metrics(partial, '091160')) == {'수급'}


def test_development_mode_is_no_longer_an_execution_option(tmp_path):
    from edge_analysis_v2.dashboard.jobs import ExecutionDashboard
    manager = ExecutionDashboard(tmp_path, key='test', model='test', connection_factory=lambda: None)
    with pytest.raises(ValueError):
        manager.start({'kind': 'movement', 'scenario': 'baseline', 'tool_mode': 'cards'})


def test_approximate_weighted_per_is_withheld_from_cards_but_kept_in_the_screen():
    # Policy (ALPHA-1130): FY−9M Q4 EPS is an approximation. The screen response says so
    # (weighted_per_approximate); the projected card cannot, so the PER card is withheld while PBR stays.
    fixture = make_fixture()
    etf = fixture['context']['etf_code']
    rows = [r for r in fixture['financials'] if r['period'].endswith('-Q4')]
    assert rows, 'fixture must contain a Q4 quarter'
    for r in rows:
        r['eps_derivation'] = 'FY_MINUS_9M'
    output = FixtureTools(fixture).call('get_instrument_factors', {'instrument_id': etf, 'factors': ['valuation']})
    assert output['result']['valuation']['weighted_per_approximate'] is True
    keys = [c['key'] for c in project_factor_metrics(output, etf)['밸류']]
    assert 'weighted_per' not in keys and 'weighted_pbr' in keys
