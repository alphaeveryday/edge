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


def test_past_underperformance_does_not_disqualify_customer_objectives():
    # Diversification may sacrifice recent returns; comparison is information, not eligibility.
    frame = pd.DataFrame({'A': [1] + [.9] * 10 + [1.1] * 10, 'CASH:KRW': 1.0})
    assert len(search(['A'], frame, {'CASH:KRW': 1})) == 1
    frame.loc[11, 'A'] = .9
    result = search(['A'], frame, {'CASH:KRW': 1})
    assert result[0]['recent_outperform_days'] == 9
    frame['A'] = [1] + [.9] * 20
    result = search(['A'], frame, {'CASH:KRW': 1})
    assert result[0]['excess_pp'] == pytest.approx(-10)
    assert result[0]['duration_pct'] == 0
    assert result[0]['recent_outperform_days'] == 0


@pytest.mark.parametrize('excess', [[0, 0], [-20, -10], [-10, 10]])
def test_ranking_is_finite_and_ordered_with_zero_or_negative_excess(excess):
    groups = [[{'weights': [1], 'duration_pct': 0, 'excess_pp': value,
                'preservation_pct': 100, 'recent_outperform_days': 0}] for value in excess]
    result = rank(groups, [['A'], ['B']])
    assert all(np.isfinite(row['score']) and 0 <= row['score'] <= 100 for row in result)
    assert [row['excess_pp'] for row in result] == sorted(excess, reverse=True)
    assert all(row['recent_outperform_days'] == 0 for row in result)


def test_one_measured_session_is_compared_without_claiming_more_days_than_exist():
    # 8차: 하루치 기록이어도 비교한다. 대신 '최근 앞선 날'은 실제로 잰 날 수를 넘지 않는다
    frame = pd.DataFrame({'A': [1, 1.1], 'CASH:KRW': 1.0})
    rows = search(['A'], frame, {'CASH:KRW': 1})
    assert rows and all(row['recent_outperform_days'] <= 1 for row in rows)
    assert search(['A'], frame.iloc[:1], {'CASH:KRW': 1}) == []  # 종가 하나는 수익률이 없다


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
    assert len(result['candidates']) == 10
    assert any(row['excess_pp'] < 0 for row in result['candidates'])
    assert 'candidate-5' in {c['candidate_id'] for c in result['candidates']}
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


def collector(monkeypatch, prices, slow=()):
    """The collector's store as an analysis sees it: `prices` maps an instrument to its stored record or the
    collector's failure code; `slow` instruments never arrive. Returns each request's keys in order."""
    requested = []
    async def stored_prices(keys, as_of, records, failures, **_):
        requested.append(list(keys))
        for key in keys:
            value = prices.get(key)
            if key in slow:
                failures[key] = 'PRICE_QUEUE_TIMEOUT'
            elif isinstance(value, dict):
                records[key] = value
                failures.pop(key, None)
            else:
                failures[key] = value or 'PRICE_NOT_STORED'
        if set(keys) & set(slow):
            await asyncio.Event().wait()
    monkeypatch.setattr('edge_portfolio.market_preload.stored_prices', stored_prices)
    return requested


async def test_saved_holdings_removed_from_catalog_are_reported_without_losing_recovery():
    request, market, groups = batch()
    market.current = {'KR:REMOVED': 1}
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'unavailable'
    assert result['reason'] == 'CURRENT_INSTRUMENT_UNAVAILABLE'
    assert result['missing_instrument_ids'] == ['KR:REMOVED']
    assert not result['candidates'] and not market.records
    assert market.current == {'KR:REMOVED': 1}  # Never fabricate a cash baseline.


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


async def test_model_reviews_leverage_without_requiring_metadata_and_explicit_caps_still_apply():
    from edge_portfolio.decision import Decision
    request, market, groups = batch()
    market.loader = lambda symbol, _: record(finish=200 if symbol == 'A.KS' else 120)
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(constraints=[
        {'kind': 'unverified', 'source': '레버리지 제외'},
        {'kind': 'max_weight', 'instrument_ids': ['KR:A'], 'value': 30, 'source': 'A 30% 이하'}])
    result = await optimizer.tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['candidates']
    assert all(a['weight_pct'] <= 30 for row in result['candidates'] for a in row['allocations'] if a['instrument_id'] == 'KR:A')
    assert any(c['status'] == 'model_review' for c in optimizer.vector_checks(result['candidates'][0]['allocations']))


def test_new_money_kind_does_not_add_an_unrequested_no_sell_constraint():
    from edge_portfolio.decision import Decision
    request, market, _ = batch()
    request.holdings = AnalysisInput(holdings=[{'name': 'A', 'instrument_id': 'KR:A', 'amount_krw': 100}], horizon='10년 이상').holdings
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(kind='new_money')
    assert optimizer.vector_checks([{'instrument_id': 'KR:B', 'weight_pct': 100}]) == []
    optimizer.decision = Decision(kind='new_money', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
    assert optimizer.vector_checks([{'instrument_id': 'KR:B', 'weight_pct': 100}])[0]['status'] == 'fail'


def test_excess_return_precedes_drawdown_income_and_service_score():
    groups = [[{'weights': [1], 'duration_pct': 100, 'excess_pp': 10, 'preservation_pct': 0, 'max_drawdown_pct': -20, 'income_yield_pct': None}],
              [{'weights': [1], 'duration_pct': 50, 'excess_pp': 5, 'preservation_pct': 100, 'max_drawdown_pct': -2, 'income_yield_pct': 0}]]
    assert rank(groups, [['A'], ['B']], ['growth', 'stability'])[0]['allocations'][0]['instrument_id'] == 'A'
    assert rank(groups, [['A'], ['B']], ['stability', 'growth'])[0]['allocations'][0]['instrument_id'] == 'A'
    assert rank(groups, [['A'], ['B']], ['income'])[0]['allocations'][0]['instrument_id'] == 'A'


def test_outperform_duration_breaks_equal_excess_before_mdd_or_preservation():
    groups = [[{'weights': [1], 'duration_pct': 80, 'excess_pp': 10, 'preservation_pct': 0, 'max_drawdown_pct': -50}],
              [{'weights': [1], 'duration_pct': 60, 'excess_pp': 10, 'preservation_pct': 100, 'max_drawdown_pct': -1}]]
    assert rank(groups, [['A'], ['B']], ['stability', 'preserve'])[0]['allocations'][0]['instrument_id'] == 'A'


def test_unknown_income_remains_unknown_when_performance_is_tied():
    groups = [[{'weights': [1], 'duration_pct': 80, 'excess_pp': 10, 'preservation_pct': 0, 'income_yield_pct': None}],
              [{'weights': [1], 'duration_pct': 80, 'excess_pp': 10, 'preservation_pct': 0, 'income_yield_pct': 0}]]
    result = rank(groups, [['A'], ['B']], ['income'])
    assert result[0]['allocations'][0]['instrument_id'] == 'B'
    assert result[1]['income_yield_pct'] is None


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


async def test_shared_baseline_and_fx_are_requested_before_optional_candidates(monkeypatch):
    # The collector serves requests in order: the baseline and FX lead, so a slow candidate cannot hold them back.
    request, market, groups = batch()
    market.current = {'US:VOO': 1}
    market.catalog['US:VOO'] = {'ticker': 'VOO', 'market': 'US', 'kind': 'etf'}
    prices = {'FX': record(start=1000, finish=1000), 'US:VOO': record('USD', finish=105), 'KR:A': record(finish=120)}
    requested = collector(monkeypatch, prices, slow={'KR:B', 'KR:C', 'KR:D', 'KR:E'})
    monkeypatch.setattr('edge_portfolio.optimizer.PRICE_COLLECTION_BUDGET_SECONDS', .1)
    result = await Optimizer(market).tool({'combinations': groups})
    assert requested[0][:2] == ['FX', 'US:VOO']
    assert result['status'] == 'available'
    assert result['candidates'][0]['allocations'] == [{'instrument_id': 'KR:A', 'weight_pct': 100}]


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
    # 팔지 않기는 금액으로 거른다. 8차에서 새 돈은 입력한 현금이고 글로 적은 추가금은 세지 않는다
    from edge_portfolio.decision import Decision
    rows = [{'instrument_id': key, 'weight_pct': 50} for key in ['US:VOO', 'US:QQQ']]
    def checks(holdings):
        request = AnalysisInput(holdings=[{'name': key, 'instrument_id': key, 'amount_krw': amount, 'kind': 'cash' if key == 'CASH:KRW' else 'etf'}
                                          for key, amount in holdings], horizon='10년 이상', question='추가 100만원. 매도 금지.')
        optimizer = Optimizer(MarketData(request), request)
        optimizer.decision = Decision(funding_text='추가 100만원', constraints=[{'kind': 'no_sell', 'source': '매도 금지'}])
        return optimizer.vector_checks(rows)
    assert all(check['status'] == 'pass' for check in checks([('US:VOO', 1000000), ('CASH:KRW', 1000000)]))
    assert any(check['status'] == 'fail' for check in checks([('US:VOO', 1000000)]))


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
        monkeypatch.setattr('edge_portfolio.optimizer.PRICE_COLLECTION_BUDGET_SECONDS', .05)
    if failure == 'fx':
        market.catalog['KR:E']['market'] = 'US'
    if failure == 'shared-window':
        market.as_of = date(2026, 10, 2)
    prices = {'FX': 'PRICE_STALE'}
    for key in market.catalog:
        dates = None
        if failure == 'shared-window':
            dates = pd.bdate_range('2025-10-01', '2026-10-01') if key == 'KR:E' else pd.bdate_range('2025-09-30', '2026-09-30')
        prices[key] = record(dates=dates)
    collector(monkeypatch, prices, slow={'KR:E'} if failure == 'timeout' else ())
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


async def test_newest_candidate_sets_the_shared_window_instead_of_being_dropped():
    # 8차 결정: 모든 비교를 가장 짧은 종목의 기록에 맞춘다. 7거래일짜리 새 ETF도 후보에 남고 모두 그 기간으로 견준다
    request, market, groups = batch()
    market.loader = lambda symbol, _: record(dates=pd.bdate_range('2026-10-01', '2026-10-09')) if symbol == 'E.KS' else record()
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 10
    assert len(result['candidates']) == 10
    assert result['start_date'] == '2026-10-01'


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
    # 짧은 후보도 남고(8차: 기록이 짧다고 빼지 않는다), 공통 끝날로 잘려도 긴 후보들은 그대로 견준다
    assert result['tested_combinations'] == 10
    assert len(result['candidates']) == 10
    assert result['end_date'] == '2026-10-12'


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


async def test_short_fallback_group_does_not_choose_the_basis_for_surviving_groups():
    request, market, groups = batch()
    def loader(symbol, _):
        data = record()
        if symbol == 'E.KS':
            data['frame'] = data['frame'].iloc[-5:]
            data['return_basis'] = 'price'
        else:
            data['frame'].iloc[-20:, data['frame'].columns.get_loc('Close')] = float('nan')
        return data
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 9
    assert len(result['candidates']) == 9
    for value in market.results.values():
        history = value['history']
        assert '분배금 제외' not in history['basis']
        assert history['points'][-1]['recommended'] > history['points'][-2]['recommended']


async def test_full_length_price_only_candidate_is_excluded_without_erasing_dividends():
    request, market, groups = batch()
    def loader(symbol, _):
        data = record()
        if symbol == 'E.KS':
            data['return_basis'] = 'price'
        return data
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert result['tested_combinations'] == 9
    assert result['rejected_combinations'] == [{'combination': 5, 'reason': 'TOTAL_RETURN_UNAVAILABLE'}]
    assert all('분배금 재투자' in value['history']['method'] for value in market.results.values())


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


async def test_collection_deadline_does_not_overwrite_a_failure_the_collector_reported(monkeypatch):
    request, market, groups = batch()
    collector(monkeypatch, {**{key: record() for key in market.catalog}, 'KR:E': 'PRICE_STALE'}, slow={'KR:D'})
    monkeypatch.setattr('edge_portfolio.optimizer.PRICE_COLLECTION_BUDGET_SECONDS', .05)
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['failures']['KR:E'] == 'PRICE_STALE'
    assert result['failures']['KR:D'] == 'PRICE_QUEUE_TIMEOUT'


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


async def test_planning_can_select_one_diversified_etf_without_an_invented_position_minimum():
    request, market, groups = batch()
    request.planning = True
    market.loader = lambda symbol, as_of: record(currency='KRW')
    optimizer = Optimizer(market, request)
    result = await optimizer.tool({'combinations': [['KR:A']]})
    assert result['candidates'][0]['allocations'] == [{'instrument_id': 'KR:A', 'weight_pct': 100}]
    from itertools import combinations
    pairs = [list(pair) for pair in combinations(market.catalog, 2)]
    result = await optimizer.tool({'combinations': pairs})
    assert result['candidates']
    assert any(len(row['allocations']) >= 2 for row in result['candidates'])


def test_agent_can_withhold_backtest_winner_when_rating_evidence_is_insufficient():
    optimizer = Optimizer(None)
    optimizer.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
    final = SimpleNamespace(recommendation=None, selected_candidate_id=None, return_forecast=None)
    assert optimizer.validate(final) is None
    final.selected_candidate_id = 'candidate-1'
    with pytest.raises(ValueError):
        optimizer.validate(final)


@pytest.mark.parametrize('completed', [False, True])
async def test_batch_deadline_keeps_arrived_prices_and_names_the_rest_as_still_queued(monkeypatch, completed):
    request, market, groups = batch()
    arrived = {'KR:A'} if completed else set()
    collector(monkeypatch, {key: record() for key in arrived}, slow=set(market.catalog) - arrived)
    monkeypatch.setattr('edge_portfolio.optimizer.PRICE_COLLECTION_BUDGET_SECONDS', .05)
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert market.failures == {key: 'PRICE_QUEUE_TIMEOUT' for key in set(market.catalog) - arrived}
    assert bool(result['candidates']) == completed
    if completed:
        assert result['tested_combinations'] == 1


def test_rating_alone_cannot_withhold_an_available_candidate():
    from edge_portfolio.contracts import FinalReport
    from report_fixtures import final_payload
    optimizer = Optimizer(None)
    optimizer.result = {'status': 'available', 'candidates': [{'candidate_id': 'candidate-1'}]}
    final = FinalReport.model_validate({**final_payload(), 'recommendation': None, 'withheld_reason': 'quality_failed'})
    with pytest.raises(ValueError, match='Rating is not a recommendation threshold'):
        optimizer.validate(final)
    optimizer.result = {'status': 'no_match', 'candidates': []}
    assert optimizer.validate(final) is None


async def test_price_retries_share_collection_time_and_keep_completed_candidates(monkeypatch):
    from itertools import combinations
    request, market, groups = batch()
    requested = collector(monkeypatch, {'KR:A': record()}, slow=set(market.catalog) - {'KR:A'})
    monkeypatch.setattr('edge_portfolio.optimizer.PRICE_COLLECTION_BUDGET_SECONDS', .03)
    optimizer = Optimizer(market, request)
    first = await optimizer.tool({'combinations': groups})
    attempts = len(requested)
    second = await optimizer.tool({'combinations': groups})
    assert len(requested) == attempts  # Retrying cannot consume the report-writing time again.
    assert first['status'] == second['status'] == 'available'
    assert [row['allocations'] for row in first['candidates']] == [row['allocations'] for row in second['candidates']]
    assert first['failures'] == second['failures']
    third = await optimizer.tool({'combinations': [list(group) for group in combinations(market.catalog, 3)]})
    assert len(requested) == attempts  # A different candidate batch shares the same collection budget.
    assert [row['allocations'] for row in third['candidates']] == [row['allocations'] for row in first['candidates']]


def test_model_chosen_balanced_weights_remain_selectable_below_historical_winner():
    groups = [[{'weights': [.9, .1], 'duration_pct': 100, 'excess_pp': 40, 'preservation_pct': 60},
               {'weights': [.5, .5], 'duration_pct': 50, 'excess_pp': 10, 'preservation_pct': 100}]]
    result = rank(groups, [['KR:A', 'KR:B']], ['growth'], preferred=[(('KR:A', 50), ('KR:B', 50))])
    assert len(result) == 2
    assert any(row['allocations'] == [{'instrument_id': 'KR:A', 'weight_pct': 50}, {'instrument_id': 'KR:B', 'weight_pct': 50}] for row in result)


async def test_single_researched_combination_is_enough_and_model_can_change_its_weights(monkeypatch):
    from test_market_data import record
    request, market, _ = batch()
    market.loader = lambda symbol, as_of: record(finish=120 if symbol == 'A.KS' else 110)
    optimizer = Optimizer(market, request)
    first = await optimizer.tool({'combinations': [['KR:A', 'KR:B']]})
    preferred = [{'instrument_id': 'KR:A', 'weight_pct': 50}, {'instrument_id': 'KR:B', 'weight_pct': 50}]
    result = await optimizer.tool({'combinations': [['KR:A', 'KR:B']], 'preferred_allocations': preferred})
    assert result['revision'] > first['revision']
    assert any(row['allocations'] == preferred for row in result['candidates'])


async def test_model_preferred_weights_still_obey_explicit_customer_limits():
    from edge_portfolio.decision import Decision
    request, market, _ = batch()
    market.loader = lambda symbol, as_of: record(finish=120 if symbol == 'A.KS' else 110)
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(constraints=[{'kind': 'max_weight', 'source': 'A는 30% 이하', 'instrument_ids': ['KR:A'], 'value': 30}])
    result = await optimizer.tool({'combinations': [['KR:A', 'KR:B']], 'preferred_allocations': [
        {'instrument_id': 'KR:A', 'weight_pct': 50}, {'instrument_id': 'KR:B', 'weight_pct': 50}]})
    assert result['candidates']
    assert all(next(a['weight_pct'] for a in row['allocations'] if a['instrument_id'] == 'KR:A') <= 30 for row in result['candidates'])


@pytest.mark.parametrize('missing', ['baseline', 'prices'])
async def test_missing_comparison_keeps_a_model_proposal_that_passes_customer_constraints(missing):
    request, market, _ = batch()
    if missing == 'baseline':
        market.current = {}
    def unavailable(*_):
        raise ValueError('PRICE_UNAVAILABLE')
    market.loader = unavailable
    optimizer = Optimizer(market, request)
    allocations = [{'instrument_id': 'KR:A', 'weight_pct': 50}, {'instrument_id': 'KR:B', 'weight_pct': 50}]
    result = await optimizer.tool({'combinations': [['KR:A', 'KR:B']], 'preferred_allocations': allocations})
    assert result['status'] == 'available'
    assert result['candidates'][0]['allocations'] == allocations
    assert result['candidates'][0]['score'] is None
    assert result['comparison_status'] == 'unavailable'


async def test_missing_prices_never_override_the_customers_weight_limit():
    from edge_portfolio.decision import Decision
    request, market, _ = batch()
    market.current = {}
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(constraints=[{'kind': 'max_weight', 'source': 'A 30%', 'instrument_ids': ['KR:A'], 'value': 30}])
    result = await optimizer.tool({'combinations': [['KR:A', 'KR:B']], 'preferred_allocations': [
        {'instrument_id': 'KR:A', 'weight_pct': 50}, {'instrument_id': 'KR:B', 'weight_pct': 50}]})
    assert not result['candidates']


async def test_search_grid_does_not_reject_model_weights_that_satisfy_a_five_percent_limit():
    from edge_portfolio.decision import Decision
    request, market, _ = batch()
    market.current = {}
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(constraints=[
        {'kind': 'require', 'source': 'A required', 'instrument_ids': ['KR:A']},
        {'kind': 'max_weight', 'source': 'A 5%', 'instrument_ids': ['KR:A'], 'value': 5}])
    allocations = [{'instrument_id': 'KR:A', 'weight_pct': 5}, {'instrument_id': 'KR:B', 'weight_pct': 95}]
    result = await optimizer.tool({'combinations': [['KR:A', 'KR:B']], 'preferred_allocations': allocations})
    assert result['candidates'][0]['allocations'] == allocations


@pytest.mark.parametrize('first_weight', [31, 23.52941176])
async def test_model_weights_outside_grid_receive_comparable_metrics_and_history(first_weight):
    from edge_portfolio.contracts import FinalReport
    from report_fixtures import final_payload
    request, market, _ = batch()
    market.loader = lambda symbol, as_of: record(finish=120 if symbol == 'A.KS' else 110)
    optimizer = Optimizer(market, request)
    allocation = [{'instrument_id': 'KR:A', 'weight_pct': first_weight}, {'instrument_id': 'KR:B', 'weight_pct': 100 - first_weight}]
    result = await optimizer.tool({'combinations': [['KR:A', 'KR:B']], 'preferred_allocations': allocation})
    selected = next(c for c in result['candidates'] if c['allocations'] == allocation)
    final = final_payload()
    final['recommendation']['allocations'] = [{**row, 'account': '일반 계좌'} for row in allocation]
    final['selected_candidate_id'] = selected['candidate_id']
    assert optimizer.validate(FinalReport.model_validate(final)) == selected
    assert selected['excess_pp'] is not None
    assert selected['max_drawdown_pct'] is not None
    assert selected.get('comparison_status') != 'unavailable'
    history = market.results[tuple(sorted(weights(allocation).items()))]['history']
    assert history['start_date'] == result['start_date']
    assert history['end_date'] == result['end_date']
    last = history['points'][-1]
    assert selected['excess_pp'] == pytest.approx((last['recommended'] - last['current']) / 10000, abs=1e-5)


def test_preferred_weights_use_the_same_arithmetic_without_duplicating_grid_vectors():
    frame = pd.DataFrame({'A': [1.] + [1.2] * 10, 'B': [1.] + [.9] * 10, 'CASH:KRW': 1.})
    rows = search(['A', 'B'], frame, {'CASH:KRW': 1}, [(('A', 31), ('B', 69)), (('A', 50), ('B', 50))])
    assert len(rows) == 10  # Nine grid vectors plus one new model vector.
    chosen = next(r for r in rows if r['weights'] == [.31, .69])
    assert chosen['excess_pp'] == pytest.approx(-.7)


def test_numerical_noise_cannot_override_outperform_duration_with_equal_final_returns():
    frame = pd.DataFrame({'A': [1.] + [1.3, .9] * 5 + [1.2],
                          'B': [1.] + [.9, 1.3] * 5 + [1.2], 'CASH:KRW': 1.})
    rows = search(['A', 'B'], frame, {'CASH:KRW': 1})
    result = rank([rows], [['A', 'B']])
    assert result[0]['excess_pp'] == 20
    assert result[0]['duration_pct'] == 100


def test_funded_candidates_that_would_buy_more_of_a_held_stock_are_dropped_before_ranking():
    # 비중이 현재보다 낮아도 새 돈(현금)이 들어오면 개별주를 더 사야 할 수 있다. 그런 후보가 1위로 남으면 게시에서 거절돼 분석이 실패한다.
    from edge_portfolio.catalog import canonicalize
    from edge_portfolio.decision import Decision
    request = canonicalize(AnalysisInput(holdings=[{'name': 'x', 'instrument_id': key, 'kind': kind, 'amount_krw': amount}
                                                   for key, kind, amount in [('KR:005930', 'stock', 500000), ('KR:069500', 'etf', 500000), ('CASH:KRW', 'cash', 1000000)]],
                                         horizon='10년 이상'))
    optimizer = Optimizer(MarketData(request), request)
    optimizer.decision = Decision(kind='rebalance')
    def held(weight):
        rows = [{'instrument_id': 'KR:005930', 'weight_pct': weight}, {'instrument_id': 'KR:069500', 'weight_pct': 100 - weight}]
        return next((c['status'] for c in optimizer.vector_checks(rows) if c['kind'] == 'held_stock'), 'pass')
    assert held(25) == 'pass', '₩500,000 of ₩2,000,000 keeps the stock as it is'
    assert held(30) == 'fail', 'below the current 25%+cash weight but still a ₩100,000 purchase'
    assert held(60) == 'fail'




async def test_dividend_chip_shows_trailing_yields_so_the_model_can_favor_higher_payers():
    # '배당 받기'를 고른 고객에게 모델이 분배율 높은 ETF에 무게를 두려면, 목적(income)을 따로 적지 않아도
    # ETF별 분배율과 후보별 비중 가중 분배율이 보여야 한다. 칩이 없으면 붙이지 않는다
    from edge_portfolio.decision import Decision
    request, market, _ = batch()
    market.catalog = {'KR:A': {'ticker': 'A', 'market': 'KR', 'kind': 'etf', 'name': '고배당 A'},
                      'KR:B': {'ticker': 'B', 'market': 'KR', 'kind': 'etf', 'name': '배당성장 B'}}
    def loader(symbol, _):
        data = record(start=100, finish=100)
        data['frame']['Dividends'] = 0.0
        data['frame'].iloc[60, data['frame'].columns.get_loc('Dividends')] = 6.0 if symbol == 'A.KS' else 2.0
        return data
    market.loader = loader
    groups = [['KR:A'], ['KR:B'], ['KR:A', 'KR:B']]
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision(constraints=[{'kind': 'dividend', 'source': '배당 받기'}])
    result = await optimizer.tool({'combinations': groups})
    assert result['income_yields_pct'] == {'KR:A': 6, 'KR:B': 2}  # 최근 12개월 분배금 ÷ 최근 종가 100
    assert result['candidates']
    for row in result['candidates']:
        expected = sum(a['weight_pct'] / 100 * result['income_yields_pct'][a['instrument_id']] for a in row['allocations'])
        assert row['income_yield_pct'] == pytest.approx(expected)
    optimizer = Optimizer(market, request)
    optimizer.decision = Decision()
    result = await optimizer.tool({'combinations': groups})
    assert result['income_yields_pct'] == {}
    assert all(row['income_yield_pct'] is None for row in result['candidates'])


class SavedPrices:
    """portfolio_dashboard_prices as the collector leaves it, without a database."""
    def __init__(self, payloads):
        self.payloads = payloads
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def execute(self, _, params):
        payload = self.payloads.get(params[0])
        return SimpleNamespace(fetchone=lambda: {'payload': payload} if payload else None)


async def test_twelve_holdings_answer_from_the_store_and_only_the_missing_one_waits(monkeypatch):
    # ALPHA-1437: one provider slot served twelve holdings one by one until the shared
    # 180-second budget ran out, so every holding failed at once. The analysis now reads the
    # collector's store: stored holdings answer at once and are never requested again;
    # only the instrument the store lacks is requested, and it alone fails when the collector is slow.
    import json
    from edge_portfolio import store
    from edge_portfolio.price_fetch import serialize
    held = [f'KR:H{number:02d}' for number in range(12)]
    request, market, _ = batch()
    market.catalog = {key: {'ticker': key[3:], 'market': 'KR', 'kind': 'etf'} for key in [*held, 'KR:NEW']}
    market.current = {key: 1 / 12 for key in held}
    payloads = {key: json.loads(serialize({**record(finish=110 + n), 'history_complete': True})) for n, key in enumerate(held)}
    monkeypatch.setattr(store, 'connect', lambda: SavedPrices(payloads))
    asked = []
    monkeypatch.setattr('edge_portfolio.market_preload.request_prices', lambda keys: asked.extend(keys) or 'requested')
    monkeypatch.setattr('edge_portfolio.market_preload.finished_requests', lambda requested: {})
    monkeypatch.setattr('edge_portfolio.optimizer.PRICE_COLLECTION_BUDGET_SECONDS', 1.5)
    result = await Optimizer(market, request).tool({'combinations': [[held[0], held[1]], ['KR:NEW']]})
    assert asked == ['KR:NEW'], 'Stored archives must not be requested from the collector again'
    assert market.failures == {'KR:NEW': 'PRICE_QUEUE_TIMEOUT'}
    assert result['status'] != 'unavailable' and result['candidates']


