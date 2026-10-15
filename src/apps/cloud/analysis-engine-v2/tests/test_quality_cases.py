"""Counterfactual fixtures must change evidence, not leak the expected answer."""
import json
import pytest

from edge_analysis_v2.tools.fixture_data import FixtureTools
from edge_analysis_v2.quality.scenarios import CASES, make_quality_fixture
from edge_analysis_v2.dashboard.jobs import scenario_cutoff


def initial(name):
    return FixtureTools(make_quality_fixture(name, '2026-09-21T08:30:00+09:00')).initial_input()


def test_every_world_uses_same_cutoff_and_goals_are_not_sent_to_agent():
    for name in CASES:
        assert scenario_cutoff('outlook', name) == '2026-09-21T08:30:00+09:00'
        value = initial(name)
        assert 'reference_example' not in json.dumps(value)
        assert 'review_questions' not in json.dumps(value)
        assert value['financial_observation_catalog']['columns'] == ['instrument_id','metric','period']


def test_future_evidence_cannot_change_initial_input():
    assert initial('structural_base') == initial('structural_future')


def test_missing_forecasts_have_no_hidden_source_or_catalog_entry():
    value = initial('structural_missing')
    assert value['financial_observation_catalog']['rows'] == []
    assert not {'expectations','multiple','old-estimate'} & {r['news_id'] for r in value['news']}


def test_reordered_duplicate_changes_no_calculation():
    tools = [FixtureTools(make_quality_fixture(n,'2026-09-21T08:30:00+09:00')) for n in ('structural_base','structural_order')]
    args = {'eps_id':'eps-new','per_low':9,'per_high':11}
    assert tools[0].call('calculate_valuation_range',args)['result'] == tools[1].call('calculate_valuation_range',args)['result']
    result = tools[1].call('search_news_threads',{})['result']
    events = [e for t in result['threads'] for s in t['stages'] for e in s['events']]
    assert sum(e['duplicate_count'] for e in events) == 1


def test_priced_in_world_reduces_conditional_upside_without_changing_eps():
    tools = [FixtureTools(make_quality_fixture(n,'2026-09-21T08:30:00+09:00')) for n in ('structural_base','structural_priced')]
    base,priced = [t.call('calculate_valuation_range',{'eps_id':'eps-new','per_low':9,'per_high':11})['result'] for t in tools]
    assert base['eps_observation']['value'] == priced['eps_observation']['value']
    assert priced['return_high_pct'] == 0 < base['return_high_pct']


def test_priced_world_keeps_etf_quote_and_equity_turnover_coherent():
    original = make_quality_fixture('structural_base','2026-09-21T08:30:00+09:00')
    changed = make_quality_fixture('structural_priced','2026-09-21T08:30:00+09:00')
    def last(fixture, instrument):
        return max((r for r in fixture['prices'] if r['instrument_id']==instrument), key=lambda r:r['date'])
    stock = last(changed,'000660')
    assert stock['turnover'] == stock['close']*stock['volume']
    stock_change = stock['close']/last(original,'000660')['close']-1
    etf_change = last(changed,'091160')['close']/last(original,'091160')['close']-1
    assert etf_change == pytest.approx(.6*stock_change, abs=.0001)
    intraday = make_quality_fixture('structural_priced','2026-09-21T10:00:00+09:00')
    assert all(r['low'] <= r['price'] <= r['high'] for r in intraday['price_snapshots'])
