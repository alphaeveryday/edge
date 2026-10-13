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
    assert calls[0]['end'] == '2026-10-12'
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
    with pytest.raises(ValueError, match='PRICE_INVALID'):
        comparison({'KR:069500': 1}, {'KR:360750': 1}, records, date(2026, 10, 12))


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


def test_short_history_is_drawn_but_internal_long_gap_is_not_backfilled():
    short = pd.bdate_range('2026-03-01', '2026-10-09')
    history = comparison({'KR:069500': 1}, None, {'KR:069500': record(dates=short)}, date(2026, 10, 12))
    assert history['start_date'] == '2026-03-02'
    assert history['end_date'] == '2026-10-09'
    assert '최근 1년' not in history['basis']
    broken = record()
    broken['frame'].iloc[50:70] = float('nan')
    with pytest.raises(ValueError, match='MISSING_TRADING_SESSIONS'):
        comparison({'KR:069500': 1}, None, {'KR:069500': broken}, date(2026, 10, 12))


def test_common_end_and_known_weights_only():
    a, b = record(dates=pd.bdate_range('2025-10-08', '2026-10-09')), record(dates=pd.bdate_range('2025-10-08', '2026-10-08'))
    history = comparison({'KR:069500': 1}, {'KR:360750': 1}, {'KR:069500': a, 'KR:360750': b}, date(2026, 10, 12))
    assert history['points'][-1]['date'] == '2026-10-08'
    with pytest.raises(ValueError):
        weights([{'instrument_id': 'US:VOO', 'weight_pct': None}])


async def test_provider_failure_is_visible_and_cached_without_aborting_analysis():
    calls = []
    def unavailable(symbol, as_of):
        calls.append(symbol)
        raise RuntimeError('rate limited')
    market = MarketData(AnalysisInput(holdings=[], horizon='10년 이상', planning=True), date(2026, 10, 12), unavailable)
    rows = [{'instrument_id': 'KR:069500', 'weight_pct': 100}]
    first = await market.get(rows)
    assert first['status'] == 'unavailable'
    assert await market.get(rows) == first
    assert calls == ['069500.KS']


async def test_forecast_retains_loss_and_dividends_require_complete_web_coverage():
    final = FinalReport.model_validate(final_payload())
    final.return_forecast = ReturnForecast(percent=-8, reason='가격 부담과 실적 둔화 가능성을 과거 성과에 더해 보수적으로 판단한 추정 수익률이에요.',
                             evidence=[{'source_id': 's1', 'passage_id': 'p1'}])
    class FakeMarket:
        as_of = date(2026, 10, 12)
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


def test_missing_trading_dates_are_detected_even_without_nan_rows():
    broken = record()
    broken['frame'] = broken['frame'].drop(broken['frame'].index[50:65])
    with pytest.raises(ValueError, match='MISSING_TRADING_SESSIONS'):
        comparison({'US:VOO': 1}, None, {'US:VOO': broken}, date(2026, 10, 12))


def test_missing_fx_on_a_us_trading_day_is_not_carried_forward():
    fx = record(start=1000, finish=1200)
    fx['frame'] = fx['frame'].drop(pd.Timestamp('2026-03-10'))
    with pytest.raises(ValueError, match='MISSING_FX_SESSIONS'):
        comparison({'US:VOO': 1}, None, {'US:VOO': record('USD'), 'FX': fx}, date(2026, 10, 12))


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
    with pytest.raises(ValueError, match='PRICE_INVALID'):
        comparison({'KR:069500': .5, 'US:VOO': .5}, None, records, date(2026, 7, 27))


def test_initial_holiday_price_must_cover_the_preceding_trading_session():
    kr = record(dates=pd.DatetimeIndex(['2026-07-01', '2026-07-20', '2026-07-21', '2026-07-22', '2026-07-23', '2026-07-24']))
    records = {'KR:069500': kr,
               'US:VOO': record('USD', dates=pd.bdate_range('2026-07-17', '2026-07-24')),
               'FX': record(dates=pd.bdate_range('2026-07-16', '2026-07-24'))}
    with pytest.raises(ValueError, match='MISSING_TRADING_SESSIONS'):
        comparison({'KR:069500': .5, 'US:VOO': .5}, None, records, date(2026, 7, 27))


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
    async def slow(*args):
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
