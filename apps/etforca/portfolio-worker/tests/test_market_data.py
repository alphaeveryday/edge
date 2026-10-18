from datetime import date
import asyncio

import pandas as pd
import pytest

from edge_portfolio.contracts import AnalysisInput, FinalReport, ReturnForecast
from edge_portfolio.market_data import MarketData, comparison, enrich_report, weights, fetch_prices, expected_sessions
from report_fixtures import final_payload


def test_provider_requests_maximum_history_with_the_analysis_cutoff(monkeypatch):
    from edge_portfolio.market_data import load_prices
    calls = []
    class Ticker:
        history_metadata = {'currency': 'KRW'}
        def history(self, **kwargs):
            calls.append(kwargs)
            return record()['frame']
    monkeypatch.setattr('edge_portfolio.market_data.yf.Ticker', lambda _: Ticker())
    load_prices('069500.KS', date(2026, 10, 12))
    assert calls[0].get('period') == 'max'
    assert calls[0]['end'] > date.today().isoformat()
    assert 'start' not in calls[0]


def test_old_price_defect_outside_common_window_does_not_hide_new_etf_graph(monkeypatch):
    from edge_portfolio.market_data import load_prices
    older = record()['frame']
    older.iloc[10, :] = float('nan')
    class Ticker:
        history_metadata = {'currency': 'KRW'}
        def history(self, **kwargs): return older
    monkeypatch.setattr('edge_portfolio.market_data.yf.Ticker', lambda _: Ticker())
    records = {'KR:069500': load_prices('069500.KS', date(2026, 10, 12)),
               'KR:360750': record(dates=pd.bdate_range('2026-07-07', '2026-10-09'))}
    history = comparison({'KR:069500': 1}, {'KR:360750': 1}, records, date(2026, 10, 12))
    assert history['start_date'] == '2026-07-07'
    records['KR:069500']['frame'].loc['2026-07-08', 'Adj Close'] = -1
    recovered = comparison({'KR:069500': 1}, {'KR:360750': 1}, records, date(2026, 10, 12))
    assert recovered['points'][1]['recommended'] == recovered['points'][0]['recommended']


def record(currency='KRW', start=100, finish=110, dates=None):
    dates = dates if dates is not None else pd.bdate_range('2025-10-09', '2026-10-09')
    values = [start + (finish - start) * i / (len(dates) - 1) for i in range(len(dates))]
    return {'frame': pd.DataFrame({'Adj Close': values, 'Close': values}, index=dates),
            'currency': currency, 'source_url': 'https://finance.yahoo.com/quote/test/history/'}


def test_daily_buy_and_hold_preserves_cash_and_fx_and_initial_money():
    records = {'US:VOO': record('USD'), 'FX': record(start=1000, finish=1200)}
    history = comparison({'US:VOO': .5, 'CASH:KRW': .5}, {'US:VOO': 1}, records, date(2026, 10, 12))
    assert len(history['points']) > 250
    assert history['interval'] == '1d'
    assert history['points'][0]['recommended'] == 1000000
    assert history['points'][-1]['recommended'] == pytest.approx(1160000)
    assert history['points'][-1]['current'] == pytest.approx(1320000)


def test_short_history_and_consecutive_gaps_keep_the_last_observed_price():
    short = pd.bdate_range('2026-03-01', '2026-10-09')
    history = comparison({'KR:069500': 1}, None, {'KR:069500': record(dates=short)}, date(2026, 10, 12))
    assert history['start_date'] == '2026-03-02'
    assert history['end_date'] == '2026-10-09'
    assert '최근 1년' not in history['basis']
    broken = record()
    broken['frame'].iloc[50:70] = float('nan')
    original = broken['frame'].copy(deep=True)
    history = comparison({'KR:069500': 1}, None, {'KR:069500': broken}, date(2026, 10, 12))
    points = {p['date']: p['recommended'] for p in history['points']}
    for day in expected_sessions('KR:069500', original.index[50], original.index[69]):
        assert points[str(day.date())] == points[str(original.index[49].date())]
    assert points[str(original.index[70].date())] > points[str(original.index[49].date())]
    pd.testing.assert_frame_equal(broken['frame'], original)
    assert history['sources'][0]['price_fill']['method'] == 'previous_observation'
    assert history['sources'][0]['price_fill']['count'] > 0


async def test_price_gaps_do_not_eliminate_all_optimizer_candidates():
    from test_optimizer import batch
    from edge_portfolio.optimizer import Optimizer
    request, market, groups = batch()
    def loader(*_):
        data = record(finish=120)
        data['frame'] = data['frame'].drop(data['frame'].index[50:70])
        return data
    market.loader = loader
    result = await Optimizer(market, request).tool({'combinations': groups})
    assert result['status'] == 'available'
    assert len(result['candidates']) == 10
    assert all(source['price_fill']['count'] > 0 for source in result['sources'])
    candidate = result['candidates'][0]
    history = await market.get(candidate['allocations'])
    assert history['status'] == 'available'
    assert history['history']['sources'] == result['sources']


def test_price_fill_diagnostics_are_bounded_only_in_model_context():
    from edge_portfolio.market_data import model_price_payload
    dates = [str(day.date()) for day in pd.bdate_range('2026-01-01', periods=100)]
    saved = {'sources': [{'price_fill': {'method': 'previous_observation', 'count': 100, 'dates': dates}}]}
    response = model_price_payload(saved)
    assert response['sources'][0]['price_fill']['dates'] == dates[:20]
    assert response['sources'][0]['price_fill']['count'] == 100
    assert len(saved['sources'][0]['price_fill']['dates']) == 100


def test_common_end_and_known_weights_only():
    a, b = record(dates=pd.bdate_range('2025-10-08', '2026-10-09')), record(dates=pd.bdate_range('2025-10-08', '2026-10-08'))
    history = comparison({'KR:069500': 1}, {'KR:360750': 1}, {'KR:069500': a, 'KR:360750': b}, date(2026, 10, 12))
    assert history['points'][-1]['date'] == '2026-10-08'
    with pytest.raises(ValueError):
        weights([{'instrument_id': 'US:VOO', 'weight_pct': None}])


async def test_provider_failure_is_visible_and_can_be_retried_without_aborting_analysis():
    calls = []
    def unavailable(symbol, as_of):
        calls.append(symbol)
        raise RuntimeError('rate limited')
    market = MarketData(AnalysisInput(holdings=[], horizon='10년 이상', planning=True), date(2026, 10, 12), unavailable)
    rows = [{'instrument_id': 'KR:069500', 'weight_pct': 100}]
    first = await market.get(rows)
    assert first['status'] == 'unavailable'
    assert await market.get(rows) == first
    assert calls == ['069500.KS', '069500.KS']


async def test_forecast_retains_loss_and_dividends_require_complete_web_coverage():
    final = FinalReport.model_validate(final_payload())
    final.return_forecast = ReturnForecast(percent=-8, reason='가격 부담과 실적 둔화 가능성을 과거 성과에 더해 보수적으로 판단한 추정 수익률이에요.',
                             evidence=[{'source_id': 's1', 'passage_id': 'p1'}])
    class FakeMarket:
        as_of = date(2026, 10, 12)
        baseline_kind = 'current_holdings'
        queried = {(('US:VOO', 1.0),)}
        async def get(self, rows):
            return {'status': 'available', 'history': {}, 'return_1y_pct': -12}
        def dividends(self, instrument_id):
            return None
    published = {'recommendation': {'allocations': [{'instrument_id': 'US:VOO', 'weight_pct': 100}], 'expected_return_1y': None}}
    sources = [{'id': 's1', 'url': 'https://official.example', 'passages': [{'id': 'p1', 'text': 'Trailing 12 months dividend yield 1.25% as of 2026-10-10'}]}]
    await enrich_report(published, final, FakeMarket(), sources)
    assert published['recommendation']['expected_return_1y'] == -8
    assert published['recommendation']['target_return_1y'] == 7
    assert published['recommendation']['dividend_yield_1y'] is None


async def test_optimizer_cache_alone_cannot_authorize_a_forecast_without_reading_history():
    final = FinalReport.model_validate(final_payload())
    final.return_forecast = ReturnForecast(percent=8, reason='가격 부담과 실적 둔화 가능성을 과거 성과에 더해 보수적으로 판단한 추정 수익률이에요.',
        evidence=[{'source_id': 's1', 'passage_id': 'p1'}])
    market = MarketData(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'))
    rows = [{'instrument_id': 'US:VOO', 'weight_pct': 100}]
    market.results[(('US:VOO', 1.0),)] = {'status': 'available', 'return_1y_pct': 10,
        'history': {'start_date': '2025-10-10', 'end_date': '2026-10-09', 'method': 'test', 'sources': []}}
    published = {'recommendation': {'allocations': rows, 'expected_return_1y': None}}
    sources = [{'id': 's1', 'url': 'https://official.example', 'passages': [{'id': 'p1', 'text': 'Official outlook'}]}]
    with pytest.raises(ValueError, match='Call read_portfolio_history'):
        await enrich_report(published, final, market, sources)
    await market.tool({'allocations': rows})
    await enrich_report(published, final, market, sources)
    assert published['recommendation']['expected_return_1y'] == 8



def test_dividend_cash_history_uses_only_trailing_year_and_missing_is_not_zero():
    market = MarketData(AnalysisInput(holdings=[], horizon='10년 이상', planning=True))
    data = record(start=100, finish=100)
    market.records['US:VOO'] = data
    assert market.dividends('US:VOO') is None
    data['frame']['Dividends'] = 0.0
    data['frame'].iloc[30, data['frame'].columns.get_loc('Dividends')] = 1.0
    data['frame'].iloc[90, data['frame'].columns.get_loc('Dividends')] = 2.0
    observation = market.dividends('US:VOO')
    assert observation['trailing_yield_pct'] == 3
    assert len(observation['events']) == 2
    assert observation['as_of'] == '2026-10-09'


def test_missing_rows_are_restored_without_using_future_prices():
    broken = record()
    before = broken['frame'].index[49]
    missing = broken['frame'].index[50:65]
    broken['frame'] = broken['frame'].drop(broken['frame'].index[50:65])
    history = comparison({'US:VOO': 1}, None, {'US:VOO': broken}, date(2026, 10, 12))
    points = {p['date']: p['recommended'] for p in history['points']}
    for day in expected_sessions('US:VOO', missing[0], missing[-1]):
        assert points[str(day.date())] == points[str(before.date())]


def test_missing_fx_carries_only_fx_and_keeps_the_observed_stock_return():
    fx = record(start=1000, finish=1200)
    fx['frame'] = fx['frame'].drop(pd.Timestamp('2026-03-10'))
    stock = record('USD')
    history = comparison({'US:VOO': 1}, None, {'US:VOO': stock, 'FX': fx}, date(2026, 10, 12))
    point = next(p for p in history['points'] if p['date'] == '2026-03-10')
    assert point['recommended'] == pytest.approx(round(1000000 * stock['frame'].loc['2026-03-10', 'Adj Close'] / 100 * fx['frame'].loc['2026-03-09', 'Close'] / 1000, 2))
    assert history['sources'][-1]['price_fill']['dates'] == ['2026-03-10']


async def test_new_current_fund_sets_common_window_without_inventing_an_annual_return():
    request = AnalysisInput(holdings=[{'name': 'current', 'instrument_id': 'KR:360750', 'amount_krw': 100}], horizon='10년 이상')
    def loader(symbol, as_of):
        return record(dates=pd.bdate_range('2026-09-01', '2026-10-09')) if symbol == '360750.KS' else record()
    market = MarketData(request, date(2026, 10, 12), loader)
    result = await market.get([{'instrument_id': 'KR:069500', 'weight_pct': 100}])
    assert result['status'] == 'available'
    assert result['history']['points'][-1]['current'] is not None
    assert result['history']['start_date'] == '2026-09-01'
    assert 'current_unavailable_reason' not in result['history']
    assert result['return_1y_pct'] is None
    assert result['return_period_pct'] is not None
    market.calls = 3
    result = await market.get([{'instrument_id': 'CASH:KRW', 'weight_pct': 100}])
    assert result == {'status': 'unavailable', 'reason': 'MARKET_DATA_LIMIT'}


async def test_market_entry_point_ignores_defects_before_the_current_funds_inception():
    request = AnalysisInput(holdings=[{'name': 'current', 'instrument_id': 'KR:360750', 'amount_krw': 100}], horizon='10년 이상')
    def loader(symbol, as_of):
        if symbol == '360750.KS':
            return record(dates=pd.bdate_range('2026-09-01', '2026-10-09'))
        data = record()
        data['frame'].iloc[10, :] = float('nan')
        return data
    result = await MarketData(request, date(2026, 10, 12), loader).get([{'instrument_id': 'KR:069500', 'weight_pct': 100}])
    assert result['status'] == 'available'
    assert result['history']['start_date'] == '2026-09-01'
    assert result['history']['points'][-1]['current'] is not None


def test_holiday_carry_cannot_import_a_negative_price_from_before_common_start():
    kr = record(dates=pd.bdate_range('2026-07-16', '2026-07-24'))
    kr['frame'] = kr['frame'].drop(pd.Timestamp('2026-07-17'))
    kr['frame'].loc['2026-07-16', 'Adj Close'] = -100
    records = {'KR:069500': kr,
               'US:VOO': record('USD', dates=pd.bdate_range('2026-07-17', '2026-07-24')),
               'FX': record(dates=pd.bdate_range('2026-07-16', '2026-07-24'))}
    result = comparison({'KR:069500': .5, 'US:VOO': .5}, None, records, date(2026, 7, 27))
    assert result['start_date'] == '2026-07-20'  # First valid common price; no backward fill from the future.


def test_initial_holiday_can_carry_the_last_observed_price_across_a_gap():
    kr = record(dates=pd.DatetimeIndex(['2026-07-01', '2026-07-20', '2026-07-21', '2026-07-22', '2026-07-23', '2026-07-24']))
    records = {'KR:069500': kr,
               'US:VOO': record('USD', dates=pd.bdate_range('2026-07-17', '2026-07-24')),
               'FX': record(dates=pd.bdate_range('2026-07-16', '2026-07-24'))}
    history = comparison({'KR:069500': .5, 'US:VOO': .5}, None, records, date(2026, 7, 27))
    assert history['start_date'] == '2026-07-17'
    assert history['points'][0]['recommended'] == 1000000
    source = next(s for s in history['sources'] if s['instrument_id'] == 'KR:069500')
    assert '2026-07-16' in source['price_fill']['dates']


@pytest.mark.parametrize('fx_starts_on_common_day', [False, True])
def test_kr_start_on_us_holiday_uses_preceding_us_and_fx_quotes(fx_starts_on_common_day):
    us_dates = pd.DatetimeIndex(['2026-07-02', '2026-07-06', '2026-07-07', '2026-07-08', '2026-07-09', '2026-07-10'])
    records = {'KR:069500': record(dates=pd.bdate_range('2026-07-03', '2026-07-10')),
               'US:VOO': record('USD', dates=us_dates),
               'FX': record(dates=pd.bdate_range('2026-07-03', '2026-07-10') if fx_starts_on_common_day else us_dates)}
    history = comparison({'KR:069500': .5, 'US:VOO': .5}, None, records, date(2026, 7, 13))
    assert history['start_date'] == '2026-07-03'
    assert history['points'][0]['recommended'] == 1000000


def test_comparison_keeps_the_longest_common_history_instead_of_capping_at_one_year():
    records = {'KR:069500': record(dates=pd.bdate_range('2022-01-03', '2026-10-09')),
               'KR:360750': record(dates=pd.bdate_range('2023-05-02', '2026-10-08'))}
    history = comparison({'KR:069500': 1}, {'KR:360750': 1}, records, date(2026, 10, 12))
    assert history['start_date'] == '2023-05-02'
    assert history['end_date'] == '2026-10-08'
    assert history['points'][0]['recommended'] == history['points'][0]['current'] == 1000000


def test_short_fx_sets_common_start_without_backfilling_before_fx_exists():
    records = {'US:VOO': record('USD'), 'FX': record(dates=pd.bdate_range('2026-06-01', '2026-10-09'))}
    history = comparison({'US:VOO': 1}, None, records, date(2026, 10, 12))
    assert history['start_date'] == '2026-06-01'
    assert history['points'][0]['recommended'] == 1000000


async def test_multiyear_period_return_is_not_published_as_an_annual_return():
    market = MarketData(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), date(2026, 10, 12),
                        lambda *_: record(finish=140, dates=pd.bdate_range('2023-01-02', '2026-10-09')))
    result = await market.get([{'instrument_id': 'KR:069500', 'weight_pct': 100}])
    assert result['return_period_pct'] == 40
    assert 0 < result['return_1y_pct'] < result['return_period_pct']


def test_annual_return_uses_last_known_price_on_a_holiday_anniversary():
    from edge_portfolio.market_data import history_returns
    result = history_returns({'points': [
        {'date': '2025-10-10', 'recommended': 100},
        {'date': '2025-10-13', 'recommended': 120},
        {'date': '2026-10-12', 'recommended': 150},
    ]})
    assert result['return_1y_pct'] == 50  # October 12, 2025 was Sunday, before the jump.


async def test_current_timeout_keeps_completed_recommended_history(monkeypatch):
    request = AnalysisInput(holdings=[{'name': 'current', 'instrument_id': 'KR:360750', 'amount_krw': 100}], horizon='10년 이상')
    market = MarketData(request, date(2026, 10, 12))
    market.records['KR:069500'] = record()
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)
    original_timeout = asyncio.timeout
    monkeypatch.setattr('edge_portfolio.market_data.fetch_prices', slow)
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout(.01 if seconds == 65 else seconds))
    result = await market.get([{'instrument_id': 'KR:069500', 'weight_pct': 100}])
    assert result['status'] == 'available'
    assert result['history']['points'][-1]['current'] is None


async def test_cancelling_provider_kills_and_reaps_process(monkeypatch):
    class Process:
        returncode = None
        killed = False
        waited = False
        async def communicate(self):
            await asyncio.sleep(10)
        def kill(self):
            self.killed = True
        async def wait(self):
            self.waited = True
            self.returncode = -1
    process = Process()
    async def start(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', start)
    task = asyncio.create_task(fetch_prices('VOO', date(2026, 10, 12)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed and process.waited


def test_verified_krx_closures_are_not_treated_as_missing_prices():
    sessions = expected_sessions('KR:069500', pd.Timestamp('2026-06-01'), pd.Timestamp('2026-07-20'))
    assert pd.Timestamp('2026-06-03') not in sessions
    assert pd.Timestamp('2026-07-17') not in sessions
    assert pd.Timestamp('2026-07-16') in sessions


def test_current_session_split_is_retained_without_using_its_unfinished_price(monkeypatch):
    from edge_portfolio.market_data import load_prices
    class Ticker:
        history_metadata = {'currency': 'KRW'}
        def history(self, **kwargs):
            return pd.DataFrame({'Close': [100., 50.], 'Adj Close': [50., 50.], 'Stock Splits': [0., 2.]}, index=pd.to_datetime(['2026-10-15', '2026-10-16']))
    monkeypatch.setattr('edge_portfolio.market_data.yf.Ticker', lambda _: Ticker())
    result = load_prices('069500.KS', date(2026, 10, 16))
    assert result['pending_splits'] == ['2026-10-16']
    assert result['frame'].index[-1].date() == date(2026, 10, 15)


async def test_dashboard_primary_capture_does_not_change_analysis_raw_callback_semantics(monkeypatch):
    from edge_portfolio import market_data
    raw = record()
    raw['frame']['Adj Close'] = float('nan')
    async def provider(symbol, day, *, on_raw=None, provider='yahoo'):
        if provider == 'yahoo':
            return raw
        raise TimeoutError()
    monkeypatch.setattr(market_data, '_fetch_prices', provider)
    captured = []
    with pytest.raises(TimeoutError):
        await market_data.fetch_prices('069500.KS', date(2026, 10, 12), on_raw=captured.append)
    assert captured == []


def test_weekend_price_cutoff_still_checks_monday_actions(monkeypatch):
    from datetime import datetime
    from edge_portfolio import market_data
    class Monday(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 19, 10, tzinfo=tz)
    class Ticker:
        history_metadata = {'currency': 'KRW'}
        def history(self, **kwargs):
            assert kwargs['end'] == '2026-10-20'
            return pd.DataFrame({'Close': [100., 50.], 'Adj Close': [50., 50.], 'Stock Splits': [0., 2.]}, index=pd.to_datetime(['2026-10-16', '2026-10-19']))
    monkeypatch.setattr(market_data, 'datetime', Monday)
    monkeypatch.setattr(market_data.yf, 'Ticker', lambda _: Ticker())
    result = market_data.load_prices('069500.KS', date(2026, 10, 17))
    assert result['pending_splits'] == ['2026-10-19']
    assert result['frame'].index[-1].date() == date(2026, 10, 16)
