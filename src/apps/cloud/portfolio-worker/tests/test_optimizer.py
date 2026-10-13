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


def test_less_than_ten_measured_sessions_cannot_claim_ten_days_of_outperformance():
    frame = pd.DataFrame({'A': [1] + [1.1] * 9, 'CASH:KRW': 1.0})
    assert search(['A'], frame, {'CASH:KRW': 1}) == []


def test_every_grid_is_fully_invested_in_ten_percent_units():
    for n in range(1, 6):
        values = grid(n)
        assert (values >= .1).all()
        assert np.allclose(values.sum(axis=1), 1)
        assert np.allclose(values * 10, np.round(values * 10))
    assert len(grid(5)) == 126


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
    candidate = {'candidate_id': 'candidate-1', 'allocations': [{'instrument_id': 'A', 'weight_pct': 10}, {'instrument_id': 'B', 'weight_pct': 90}]}
    optimizer.result = {'candidates': [candidate]}
    final = SimpleNamespace(selected_candidate_id='candidate-1', recommendation=SimpleNamespace(allocations=[
        SimpleNamespace(instrument_id='A', weight_pct=weight) for weight in ['2.8', '2.8', '2.8', '1.6']
    ] + [SimpleNamespace(instrument_id='B', weight_pct='90')]))
    assert optimizer.validate(final) == candidate
    assert weights([{'instrument_id': row.instrument_id, 'weight_pct': row.weight_pct} for row in final.recommendation.allocations]) == {'A': .1, 'B': .9}


def batch():
    request = AnalysisInput(holdings=[{'name': 'cash', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 100}], horizon='10년 이상')
    market = MarketData(request, date(2026, 10, 12))
    market.catalog = {f'KR:{key}': {'ticker': key, 'market': 'KR', 'kind': 'etf'} for key in 'ABCDE'}
    groups = [['KR:A'], ['KR:B'], ['KR:C'], ['KR:D'], ['KR:E'],
              ['KR:A', 'KR:B'], ['KR:A', 'KR:C'], ['KR:A', 'KR:D'], ['KR:B', 'KR:C'], ['KR:C', 'KR:D']]
    return request, market, groups


async def test_customer_cap_filters_vectors_before_the_group_winner_is_chosen():
    from edge_portfolio.decision import Decision
    request, market, groups = batch()
    market.loader = lambda symbol, _: record(finish=200 if symbol == 'A.KS' else 120)
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(objectives=['growth'], constraints=[{'kind': 'max_weight', 'instrument_ids': ['KR:A'], 'value': 30, 'source': 'A 30% 이하'}])
    result = await optimizer.tool({'combinations': groups})
    mixed = next(row for row in result['candidates'] if {a['instrument_id'] for a in row['allocations']} == {'KR:A', 'KR:B'})
    assert next(a['weight_pct'] for a in mixed['allocations'] if a['instrument_id'] == 'KR:A') == 30
    assert result['customer_rejected_vectors'] > 0
    assert all(a['weight_pct'] <= 30 for row in result['candidates'] for a in row['allocations'] if a['instrument_id'] == 'KR:A')


def test_customer_objective_order_precedes_service_score_and_unknown_income_is_not_zero():
    groups = [[{'weights': [1], 'duration_pct': 100, 'excess_pp': 10, 'preservation_pct': 0, 'max_drawdown_pct': -20, 'income_yield_pct': None}],
              [{'weights': [1], 'duration_pct': 50, 'excess_pp': 5, 'preservation_pct': 100, 'max_drawdown_pct': -2, 'income_yield_pct': 0}]]
    assert rank(groups, [['A'], ['B']], ['growth', 'stability'])[0]['allocations'][0]['instrument_id'] == 'A'
    assert rank(groups, [['A'], ['B']], ['stability', 'growth'])[0]['allocations'][0]['instrument_id'] == 'B'
    assert rank(groups, [['A'], ['B']], ['income'])[0]['allocations'][0]['instrument_id'] == 'B'


async def test_new_batch_reopens_search_and_recomputes_previous_candidates_on_one_window():
    request, market, groups = batch()
    market.catalog['KR:F'] = {'ticker': 'F', 'market': 'KR', 'kind': 'etf'}
    market.loader = lambda symbol, _: record(finish=140, dates=pd.bdate_range('2026-07-07', '2026-10-09')) if symbol == 'F.KS' else record(finish=120)
    optimizer = Optimizer(market, request)
    first = await optimizer.tool({'combinations': groups})
    old = first['candidates'][0]
    old_weights = {row['instrument_id']: row['weight_pct'] / 100 for row in old['allocations']}
    market.queried.add(tuple(sorted(old_weights.items())))
    second_groups = [['KR:F'] if group == ['KR:E'] else group for group in groups]
    second = await optimizer.tool({'combinations': second_groups})
    assert second['submitted_combinations'] == 11
    assert second['start_date'] == '2026-07-07'
    assert second['revision'] == 2
    assert not market.queried
    assert all(row['candidate_id'].startswith('r2-') for row in second['candidates'])
    for row in second['candidates']:
        key = tuple(sorted((a['instrument_id'], a['weight_pct'] / 100) for a in row['allocations']))
        assert market.results[key]['history']['start_date'] == second['start_date']
    with pytest.raises(ValueError, match='eligible candidate'):
        optimizer.validate(SimpleNamespace(selected_candidate_id=old['candidate_id'], recommendation=SimpleNamespace(allocations=[])))


async def test_a_failed_price_batch_can_recover_without_a_frozen_unavailable_result():
    request, market, groups = batch()
    def unavailable(*_):
        raise TimeoutError()
    market.loader = unavailable
    optimizer = Optimizer(market, request)
    assert (await optimizer.tool({'combinations': groups}))['status'] == 'unavailable'
    market.loader = lambda *_: record(finish=120)
    assert (await optimizer.tool({'combinations': groups}))['status'] == 'available'


async def test_static_service_policy_conflict_does_not_blame_missing_price_data():
    from edge_portfolio.decision import Decision
    request, market, groups = batch()
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(constraints=[{'kind': 'min_weight', 'instrument_ids': ['CASH:KRW'], 'value': 20, 'source': '현금 최소 20%'}])
    result = await optimizer.tool({'combinations': groups})
    assert result['reason'] == 'SERVICE_CUSTOMER_POLICY_CONFLICT'
    assert result['policy_conflicts'] == ['CASH_REQUIRED_BUT_SERVICE_CASH_ZERO']
    assert not market.records


def test_vector_filter_uses_funded_amounts_before_rejecting_diluted_no_sale_weights():
    from edge_portfolio.decision import Decision
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 1000000}],
        horizon='10년 이상', question='추가 100만원. 매도 금지.')
    market = MarketData(request)
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(funding_text='추가 100만원', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    rows = [{'instrument_id': key, 'weight_pct': 50} for key in ['US:VOO', 'US:QQQ']]
    assert all(check['status'] == 'pass' for check in optimizer.vector_checks(rows))
    optimizer.decision.funding_text = ''
    assert any(check['status'] == 'fail' for check in optimizer.vector_checks(rows))


async def test_bad_dividends_do_not_destroy_price_eligible_candidates():
    from edge_portfolio.decision import Decision
    request, market, groups = batch()
    def loader(*_):
        data = record(finish=120)
        data['frame']['Dividends'] = 'bad provider value'
        return data
    market.loader = loader
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(objectives=['income'])
    result = await optimizer.tool({'combinations': groups})
    assert result['status'] == 'available'
    assert all(row['income_yield_pct'] is None for row in result['candidates'])
    assert result['income_failures']


def test_global_no_sale_filter_preserves_potentially_feasible_account_rounding():
    from edge_portfolio.decision import Decision
    from edge_portfolio.execution import plan_execution
    request = AnalysisInput(holdings=[{'name': 'A', 'instrument_id': 'KR:A', 'amount_krw': 700001, 'account': account} for account in ['ISA', '일반 계좌']]
        + [{'name': 'cash', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 5600005}], horizon='10년 이상')
    market = MarketData(request)
    market.catalog = {key: {'kind': 'etf', 'market': 'KR'} for key in ['KR:A', 'KR:B']}
    decision = Decision(constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    optimizer = Optimizer(market, request)
    optimizer.decision = decision
    assert all(check['status'] == 'pass' for check in optimizer.vector_checks([{'instrument_id': 'KR:A', 'weight_pct': 20}, {'instrument_id': 'KR:B', 'weight_pct': 80}]))
    rows = [{'instrument_id': 'KR:A', 'weight_pct': 10, 'account': account} for account in ['ISA', '일반 계좌']] + [{'instrument_id': 'KR:B', 'weight_pct': 80, 'account': '일반 계좌'}]
    assert plan_execution(request, rows, decision)['status'] == 'ready'


def test_percentage_input_can_verify_no_sale_weights_without_a_krw_principal():
    from edge_portfolio.decision import Decision
    request = AnalysisInput(holdings=[{'name': 'VOO', 'instrument_id': 'US:VOO', 'amount_krw': 50},
        {'name': 'cash', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': 50}], input_mode='percent', horizon='10년 이상')
    optimizer = Optimizer(MarketData(request), request)
    optimizer.decision = Decision(constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    rows = [{'instrument_id': key, 'weight_pct': 50} for key in ['US:VOO', 'US:QQQ']]
    assert all(check['status'] == 'pass' for check in optimizer.vector_checks(rows))


async def test_unexpected_optimizer_exception_never_publishes_internal_details(monkeypatch):
    request, market, groups = batch()
    market.loader = lambda *_: record(finish=120)
    def broken_search(*_):
        raise ValueError('private internal provider payload')
    monkeypatch.setattr('edge_portfolio.optimizer.search', broken_search)
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['reason'] == 'PRICE_PROVIDER_UNAVAILABLE'
    assert 'private' not in str(result)


@pytest.mark.parametrize('failure', ['timeout', 'fx', 'shared-window'])
async def test_partial_price_failures_preserve_nine_independent_combinations(monkeypatch, failure):
    request, market, groups = batch()
    if failure == 'timeout':
        monkeypatch.setattr('edge_portfolio.optimizer.PRICE_TIMEOUT_SECONDS', .05)
    if failure == 'fx':
        market.catalog['KR:E']['market'] = 'US'
    if failure == 'shared-window':
        market.as_of = date(2026, 10, 2)
    async def fetch(symbol, as_of, **kwargs):
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
    assert len(result['candidates']) == (10 if failure == 'shared-window' else 9)
    if failure != 'shared-window':
        assert all(c['candidate_id'] != 'candidate-5' for c in result['candidates'])


async def test_new_baseline_etf_uses_its_available_period_for_all_candidates():
    request, market, groups = batch()
    market.current = {'KR:E': 1}
    market.loader = lambda symbol, _: record(finish=95, dates=pd.bdate_range('2026-07-07', '2026-10-09')) if symbol == 'E.KS' else record(finish=120)
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['start_date'] == '2026-07-07'
    assert result['candidates']


async def test_candidate_too_new_for_ten_days_does_not_shorten_usable_peers():
    request, market, groups = batch()
    market.loader = lambda symbol, _: record(dates=pd.bdate_range('2026-10-01', '2026-10-09')) if symbol == 'E.KS' else record()
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 9
    assert len(result['candidates']) == 9
    assert result['start_date'] < '2026-10-01'


async def test_candidates_are_validated_within_the_shared_window_not_their_full_histories():
    request, market, groups = batch()
    def loader(symbol, _):
        if symbol == 'E.KS':
            return record(dates=pd.bdate_range('2026-07-07', '2026-10-09'))
        data = record(dates=pd.bdate_range('2023-01-02', '2026-10-09'))
        data['frame'].iloc[10, :] = float('nan')
        return data
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 10
    assert len(result['candidates']) == 10
    assert result['start_date'] == '2026-07-07'


async def test_shared_endpoint_does_not_let_a_short_candidate_disqualify_longer_peers():
    from edge_portfolio.market_data import expected_sessions
    request, market, groups = batch()
    market.as_of = date(2026, 10, 14)
    short = expected_sessions('KR:E', pd.Timestamp('2026-09-01'), pd.Timestamp('2026-10-13'))[-11:]
    market.loader = lambda symbol, _: record(dates=short if symbol == 'E.KS' else pd.bdate_range('2025-10-09', '2026-10-12'))
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 9
    assert len(result['candidates']) == 9


async def test_null_close_column_excludes_only_the_malformed_candidate():
    request, market, groups = batch()
    def loader(symbol, _):
        data = record()
        if symbol == 'E.KS':
            data['frame']['Close'] = None  # JSON nulls from the provider subprocess.
        return data
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 9
    assert len(result['candidates']) == 9


async def test_invalid_endpoint_does_not_permanently_discard_new_valid_candidates():
    from edge_portfolio.market_data import expected_sessions
    request, market, groups = batch()
    market.as_of = date(2026, 10, 14)
    short = expected_sessions('KR:A', pd.Timestamp('2026-09-01'), pd.Timestamp('2026-10-13'))[-11:]
    def loader(symbol, _):
        data = record(dates=pd.bdate_range('2025-10-09', '2026-10-12') if symbol == 'E.KS' else short)
        if symbol == 'E.KS':
            data['frame']['Close'] = None
        return data
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 9
    assert result['end_date'] == '2026-10-13'


async def test_optimizer_retains_the_failed_instrument_and_provider_cause():
    request, market, groups = batch()
    def loader(symbol, _):
        if symbol == 'E.KS':
            raise ValueError('PRICE_STALE')
        return record()
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['failures'] == {'KR:E': 'PRICE_STALE'}
    assert result['rejected_combinations'] == [{'combination': 5, 'reason': 'INSTRUMENT_PRICE_UNAVAILABLE'}]


async def test_collection_timeout_does_not_overwrite_an_already_known_price_failure(monkeypatch):
    request, market, groups = batch()
    async def fetch(symbol, _, **kwargs):
        if symbol == 'E.KS':
            raise ValueError('PRICE_STALE')
        if symbol == 'D.KS':
            await asyncio.sleep(10)
        return record()
    monkeypatch.setattr('edge_portfolio.optimizer.fetch_prices', fetch)
    monkeypatch.setattr('edge_portfolio.optimizer.PRICE_TIMEOUT_SECONDS', .05)
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['failures']['KR:E'] == 'PRICE_STALE'
    assert result['failures']['KR:D'] == 'PRICE_PROVIDER_TIMEOUT'


async def test_all_provider_failed_combinations_are_accounted_for_in_diagnostics():
    request, market, groups = batch()
    def loader(*args):
        raise ValueError('PRICE_STALE')
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'unavailable'
    assert len(result['rejected_combinations']) == 10
    assert {row['reason'] for row in result['rejected_combinations']} == {'INSTRUMENT_PRICE_UNAVAILABLE'}


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
