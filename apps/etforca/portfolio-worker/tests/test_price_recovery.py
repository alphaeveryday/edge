from datetime import date

import pandas as pd

from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.market_data import MarketData, comparison, load_prices
from test_market_data import record


def test_missing_quote_repair_preserves_dividend_and_other_observed_prices(monkeypatch):
    raw = record()['frame']
    raw['Dividends'] = 0.0
    raw.loc['2026-07-08', ['Close', 'Adj Close']] = float('nan')
    raw.loc['2026-07-08', 'Dividends'] = 210.0
    repaired = raw.copy()
    repaired.loc['2026-07-08', ['Close', 'Adj Close']] = [12710.0, 10216.0]
    calls = []
    class Ticker:
        history_metadata = {'currency': 'KRW'}
        def history(self, **kwargs):
            calls.append(kwargs)
            return (repaired if kwargs.get('repair') else raw).copy()
    monkeypatch.setattr('edge_portfolio.market_data.yf.Ticker', lambda _: Ticker())
    result = load_prices('0094M0.KS', date(2026, 10, 12))
    assert result['frame'].loc['2026-07-08', 'Close'] == 12710.0
    assert result['frame'].loc['2026-07-08', 'Dividends'] == 210.0
    pd.testing.assert_frame_equal(result['frame'].drop(pd.Timestamp('2026-07-08')), raw.drop(pd.Timestamp('2026-07-08')), check_index_type=False)
    assert result['price_recovery']['dates'] == ['2026-07-08']
    assert len(calls) == 2


def test_repair_cannot_silently_change_the_adjustment_basis(monkeypatch):
    raw = record()['frame']
    raw.loc['2026-07-08', ['Close', 'Adj Close']] = float('nan')
    repaired = raw.ffill()
    repaired['Adj Close'] *= .8
    class Ticker:
        history_metadata = {'currency': 'KRW'}
        def history(self, **kwargs):
            return (repaired if kwargs.get('repair') else raw).copy()
    monkeypatch.setattr('edge_portfolio.market_data.yf.Ticker', lambda _: Ticker())
    result = load_prices('0094M0.KS', date(2026, 10, 12))
    assert pd.isna(result['frame'].loc['2026-07-08', 'Adj Close'])
    assert result['price_recovery']['reason'] == 'REPAIR_BASIS_MISMATCH'


def test_nontrading_dividend_event_is_kept_without_becoming_a_missing_quote():
    data = record(dates=pd.bdate_range('2026-07-01', '2026-07-24'))
    data['frame']['Dividends'] = 0.0
    data['frame'].loc['2026-07-17', ['Close', 'Adj Close']] = float('nan')
    data['frame'].loc['2026-07-17', 'Dividends'] = 210.0
    history = comparison({'KR:069500': 1}, None, {'KR:069500': data}, date(2026, 7, 27))
    assert history['points'][-1]['recommended'] > 1000000
    assert data['frame']['Dividends'].sum() == 210


def test_fx_recovery_provenance_stays_attached_to_the_krw_comparison():
    fx = record()
    fx['price_recovery'] = {'status': 'repaired', 'dates': ['2026-07-08'], 'method': 'intraday reconstruction'}
    history = comparison({'US:VOO': 1}, None, {'US:VOO': record('USD'), 'FX': fx}, date(2026, 10, 12))
    source = next(source for source in history['sources'] if source['instrument_id'] == 'USD/KRW')
    assert source['price_recovery'] == fx['price_recovery']


async def test_transient_unavailability_does_not_freeze_future_success():
    attempts = []
    def loader(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError()
        return record()
    market = MarketData(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), date(2026, 10, 12), loader)
    rows = [{'instrument_id': 'KR:069500', 'weight_pct': 100}]
    await market.get(rows)
    assert (await market.get(rows))['status'] == 'available'
    assert len(attempts) == 2


async def test_instrument_failure_code_survives_to_the_market_result():
    def loader(*args):
        raise ValueError('PRICE_STALE')
    market = MarketData(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), date(2026, 10, 12), loader)
    result = await market.get([{'instrument_id': 'KR:069500', 'weight_pct': 100}])
    assert result['failures'] == {'KR:069500': 'PRICE_STALE'}
    assert result['reason'] == 'PRICE_STALE'


def test_repair_preserves_valid_close_when_only_adjusted_close_is_missing(monkeypatch):
    raw = record()['frame']
    raw.loc['2026-07-08', 'Adj Close'] = float('nan')
    repaired = raw.ffill()
    repaired.loc['2026-07-08', 'Close'] += 10
    class Ticker:
        history_metadata = {'currency': 'KRW'}
        def history(self, **kwargs):
            return (repaired if kwargs.get('repair') else raw).copy()
    monkeypatch.setattr('edge_portfolio.market_data.yf.Ticker', lambda _: Ticker())
    result = load_prices('0094M0.KS', date(2026, 10, 12))
    assert result['frame'].loc['2026-07-08', 'Close'] == raw.loc['2026-07-08', 'Close']
    assert pd.isna(result['frame'].loc['2026-07-08', 'Adj Close'])
    assert result['price_recovery']['reason'] == 'REPAIR_BASIS_MISMATCH'
