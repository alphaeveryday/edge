import threading
import asyncio
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from edge_portfolio.optimizer import Optimizer, grid, search, rank
from edge_portfolio.market_data import MarketData, weights
from edge_portfolio.contracts import AnalysisInput
from test_market_data import record


def test_publication_requires_half_the_period_and_all_ten_recent_sessions():
    # 20 measured days, exactly half ahead, including the last ten: passes.
    frame = pd.DataFrame({'A': [1] + [.9] * 10 + [1.1] * 10, 'CASH:KRW': 1.0})
    assert len(search(['A'], frame, {'CASH:KRW': 1})) == 1
    frame.loc[11, 'A'] = .9
    assert search(['A'], frame, {'CASH:KRW': 1}) == []
    # Overall win rate is ample, but one tie within the last ten still disqualifies.
    frame['A'] = [1] + [1.1] * 20
    frame.loc[12, 'A'] = 1
    assert search(['A'], frame, {'CASH:KRW': 1}) == []


def test_every_grid_is_fully_invested_in_five_percent_units():
    for n in range(1, 6):
        values = grid(n)
        assert (values >= .05).all()
        assert np.allclose(values.sum(axis=1), 1)
        assert np.allclose(values * 20, np.round(values * 20))
    assert len(grid(5)) == 3876


def test_ranking_applies_global_40_40_20_and_excludes_failed_groups():
    groups = [[{'weights': [1], 'duration_pct': 100, 'excess_pp': 10, 'preservation_pct': 0}],
              [{'weights': [1], 'duration_pct': 50, 'excess_pp': 20, 'preservation_pct': 100}], []]
    result = rank(groups, [['A'], ['B'], ['C']])
    assert [r['candidate_id'] for r in result] == ['candidate-2', 'candidate-1']
    assert [r['score'] for r in result] == [80, 60]


async def test_batch_fetches_prices_once_and_runs_all_ten_searches_concurrently(monkeypatch):
    barrier = threading.Barrier(10, timeout=10)
    def simultaneous(*args):
        barrier.wait()  # A sequential implementation or fewer than ten workers fails here.
        return search(*args)
    monkeypatch.setattr('edge_portfolio.optimizer.search', simultaneous)
    calls = []
    def loader(symbol, as_of):
        calls.append(symbol)
        return record(finish=120 if symbol != 'E.KS' else 80)
    request = AnalysisInput(holdings=[{'name': 'cash', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 100}], horizon='10년 이상')
    market = MarketData(request, date(2026, 10, 12), loader)
    market.catalog = {f'KR:{key}': {'ticker': key, 'market': 'KR'} for key in 'ABCDE'}
    combinations = [['KR:A'], ['KR:B'], ['KR:C'], ['KR:D'], ['KR:E'],
                    ['KR:A', 'KR:B'], ['KR:A', 'KR:C'], ['KR:A', 'KR:D'], ['KR:B', 'KR:C'], ['KR:C', 'KR:D']]
    optimizer = Optimizer(market)
    result = await optimizer.tool({'combinations': combinations})
    assert result['status'] == 'available'
    assert len(result['candidates']) == 9
    assert 'candidate-5' not in {c['candidate_id'] for c in result['candidates']}
    assert sorted(calls) == [f'{key}.KS' for key in 'ABCDE']
    assert await optimizer.tool({'combinations': combinations}) == result
    selected = result['candidates'][-1]
    final = SimpleNamespace(selected_candidate_id=selected['candidate_id'], recommendation=SimpleNamespace(
        allocations=[SimpleNamespace(**a) for a in selected['allocations']]))
    assert optimizer.validate(final) == selected  # Agent may choose a lower-ranked valid candidate.
    final.recommendation.allocations[0].weight_pct -= 5
    with pytest.raises(ValueError, match='exactly match'):
        optimizer.validate(final)


def test_no_candidate_cannot_be_replaced_with_an_invented_recommendation():
    optimizer = Optimizer(None)
    optimizer.result = {'candidates': []}
    with pytest.raises(ValueError, match='No eligible candidate'):
        optimizer.validate(SimpleNamespace(recommendation=object(), selected_candidate_id=None, return_forecast=None))
    assert optimizer.validate(SimpleNamespace(recommendation=None, selected_candidate_id=None, return_forecast=None)) is None


def test_exact_decimal_account_splits_preserve_candidate_weights():
    optimizer = Optimizer(None)
    candidate = {'candidate_id': 'candidate-1', 'allocations': [{'instrument_id': 'A', 'weight_pct': 5}, {'instrument_id': 'B', 'weight_pct': 95}]}
    optimizer.result = {'candidates': [candidate]}
    final = SimpleNamespace(selected_candidate_id='candidate-1', recommendation=SimpleNamespace(allocations=[
        SimpleNamespace(instrument_id='A', weight_pct=weight) for weight in ['1.4', '1.4', '1.4', '0.8']
    ] + [SimpleNamespace(instrument_id='B', weight_pct='95')]))
    assert optimizer.validate(final) == candidate
    assert weights([{'instrument_id': row.instrument_id, 'weight_pct': row.weight_pct} for row in final.recommendation.allocations]) == {'A': .05, 'B': .95}


def batch():
    request = AnalysisInput(holdings=[{'name': 'cash', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 100}], horizon='10년 이상')
    market = MarketData(request, date(2026, 10, 12))
    market.catalog = {f'KR:{key}': {'ticker': key, 'market': 'KR', 'kind': 'etf'} for key in 'ABCDE'}
    groups = [['KR:A'], ['KR:B'], ['KR:C'], ['KR:D'], ['KR:E'],
              ['KR:A', 'KR:B'], ['KR:A', 'KR:C'], ['KR:A', 'KR:D'], ['KR:B', 'KR:C'], ['KR:C', 'KR:D']]
    return request, market, groups


@pytest.mark.parametrize('failure', ['timeout', 'fx', 'shared-window'])
async def test_partial_price_failures_preserve_nine_independent_combinations(monkeypatch, failure):
    request, market, groups = batch()
    if failure == 'timeout':
        monkeypatch.setattr('edge_portfolio.optimizer.PRICE_TIMEOUT_SECONDS', .05)
    if failure == 'fx':
        market.catalog['KR:E']['market'] = 'US'
    if failure == 'shared-window':
        market.as_of = date(2026, 10, 2)
    async def fetch(symbol, as_of):
        if failure == 'timeout' and symbol == 'E.KS':
            await asyncio.sleep(10)
        if symbol == 'KRW=X':
            raise ValueError('No FX available')
        dates = None
        if failure == 'shared-window':
            dates = pd.bdate_range('2025-10-01', '2026-10-01') if symbol == 'E.KS' else pd.bdate_range('2025-09-30', '2026-09-30')
        return record(currency='USD' if symbol == 'E' else 'KRW', dates=dates)
    monkeypatch.setattr('edge_portfolio.optimizer.fetch_prices', fetch)
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert len(result['candidates']) == 9
    assert all(c['candidate_id'] != 'candidate-5' for c in result['candidates'])


async def test_unbuyable_candidates_are_rejected_before_batch_is_frozen():
    request, market, groups = batch()
    request.holdings[0].account = 'ISA'
    request.preferences.accounts = ['ISA']
    market.catalog['KR:E']['market'] = 'US'
    optimizer = Optimizer(market, request)
    with pytest.raises(ValueError, match='eligible selected account'):
        await optimizer.tool({'combinations': groups})
    assert optimizer.result is None


async def test_planning_never_optimizes_single_etf_recommendations():
    request, market, groups = batch()
    request.planning = True
    market.loader = lambda symbol, as_of: record(currency='KRW')
    optimizer = Optimizer(market, request)
    with pytest.raises(ValueError, match='at least two'):
        await optimizer.tool({'combinations': groups})
    assert optimizer.result is None
    from itertools import combinations
    pairs = [list(pair) for pair in combinations(market.catalog, 2)]
    result = await optimizer.tool({'combinations': pairs})
    assert result['candidates']
    assert all(len(row['allocations']) >= 2 for row in result['candidates'])


def test_agent_can_withhold_backtest_winner_when_rating_evidence_is_insufficient():
    optimizer = Optimizer(None)
    optimizer.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
    final = SimpleNamespace(recommendation=None, selected_candidate_id=None, return_forecast=None)
    assert optimizer.validate(final) is None
    final.selected_candidate_id = 'candidate-1'
    with pytest.raises(ValueError):
        optimizer.validate(final)
