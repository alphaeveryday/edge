"""Alternate feeds must recover data without inventing dividends or total returns."""
from datetime import date

import httpx
import pandas as pd
import pytest

from edge_portfolio.data_providers import load_fallback_prices, fetch_official_source
from edge_portfolio.market_data import fetch_prices, normalized_prices, comparison


@pytest.mark.parametrize('symbol,feed,currency', [
    ('379800.KS', 'NAVER:379800', 'KRW'), ('KRW=X', 'FRED:DEXKOUS', 'KRW'), ('VOO', 'YAHOO:VOO', 'USD')])
def test_fallback_feed_identity_currency_and_no_fabricated_dividends(monkeypatch, symbol, feed, currency):
    def reader(name, start, end):
        assert name == feed
        return pd.DataFrame({('DEXKOUS' if symbol == 'KRW=X' else 'Close'): [100., 101., 102.]},
                            index=pd.to_datetime(['2026-10-12', '2026-10-13', '2026-10-14']))
    monkeypatch.setattr('FinanceDataReader.DataReader', reader)
    record = load_fallback_prices(symbol, date(2026, 10, 14))
    assert record['frame'].index[-1] == pd.Timestamp('2026-10-13')
    assert record['currency'] == currency
    assert 'Dividends' not in record['frame']
    assert record['return_basis'] == 'price'


@pytest.mark.parametrize('invalid', [0., float('inf')])
def test_alternate_feed_cannot_cache_an_entirely_invalid_price_column(monkeypatch, invalid):
    monkeypatch.setattr('FinanceDataReader.DataReader', lambda *args: pd.DataFrame(
        {'Close': [invalid]}, index=pd.to_datetime(['2026-10-13'])))
    with pytest.raises(ValueError, match='PRICE_INVALID'):
        load_fallback_prices('VOO', date(2026, 10, 14))


def test_unavailable_adjustments_use_explicit_price_basis_when_close_is_valid(monkeypatch):
    monkeypatch.setattr('FinanceDataReader.DataReader', lambda *args: pd.DataFrame(
        {'Close': [100.], 'Adj Close': [None]}, index=pd.to_datetime(['2026-10-13'])))
    record = load_fallback_prices('VOO', date(2026, 10, 14))
    assert record['return_basis'] == 'price'
    assert record['frame']['Adj Close'].equals(record['frame']['Close'])


async def test_price_failure_switches_feed_instead_of_repeating_the_same_provider(monkeypatch):
    calls = []
    async def fetch(*args, provider='yahoo', **kwargs):
        calls.append(provider)
        if provider == 'yahoo':
            raise ValueError('PRICE_STALE')
        return {'frame': 'recovered'}
    monkeypatch.setattr('edge_portfolio.market_data._fetch_prices', fetch)
    assert (await fetch_prices('379800.KS', date(2026, 10, 14)))['frame'] == 'recovered'
    assert calls == ['yahoo', 'fallback']


async def test_isolated_invalid_quote_does_not_erase_usable_primary_history(monkeypatch):
    original = {'frame': pd.DataFrame({'Close': [0., 100.], 'Adj Close': [0., 100.]},
                                      index=pd.to_datetime(['2026-10-12', '2026-10-13']))}
    calls = []
    async def fetch(*args, provider='yahoo', **kwargs):
        calls.append(provider)
        if provider == 'fallback':
            raise ValueError('PRICE_PROVIDER_UNAVAILABLE')
        return original
    monkeypatch.setattr('edge_portfolio.market_data._fetch_prices', fetch)
    result = await fetch_prices('VOO', date(2026, 10, 14))
    assert calls == ['yahoo']
    assert result['frame'].equals(original['frame'])


async def test_entirely_invalid_primary_column_uses_alternate_feed(monkeypatch):
    calls = []
    async def fetch(*args, provider='yahoo', **kwargs):
        calls.append(provider)
        if provider == 'yahoo':
            return {'frame': pd.DataFrame({'Close': [0., 0.], 'Adj Close': [0., 0.]})}
        return {'frame': 'recovered'}
    monkeypatch.setattr('edge_portfolio.market_data._fetch_prices', fetch)
    assert (await fetch_prices('VOO', date(2026, 10, 14)))['frame'] == 'recovered'
    assert calls == ['yahoo', 'fallback']


async def test_stale_adjusted_column_uses_alternate_even_when_raw_close_is_current(monkeypatch):
    calls = []
    async def fetch(*args, provider='yahoo', **kwargs):
        calls.append(provider)
        if provider == 'yahoo':
            return {'frame': pd.DataFrame({'Close': [100., 101.], 'Adj Close': [100., None]},
                                         index=pd.to_datetime(['2026-10-01', '2026-10-13']))}
        return {'frame': 'recovered'}
    monkeypatch.setattr('edge_portfolio.market_data._fetch_prices', fetch)
    assert (await fetch_prices('VOO', date(2026, 10, 14)))['frame'] == 'recovered'
    assert calls == ['yahoo', 'fallback']


async def test_two_unusable_feeds_leave_an_instrument_failure_instead_of_a_success_record(monkeypatch):
    async def fetch(*args, provider='yahoo', **kwargs):
        if provider == 'yahoo':
            return {'frame': pd.DataFrame({'Close': [0.], 'Adj Close': [0.]})}
        raise ValueError('PRICE_PROVIDER_UNAVAILABLE')
    monkeypatch.setattr('edge_portfolio.market_data._fetch_prices', fetch)
    with pytest.raises(ValueError, match='PRICE_PROVIDER_UNAVAILABLE'):
        await fetch_prices('VOO', date(2026, 10, 14))


def test_mixed_sources_compare_every_asset_on_the_same_price_basis():
    index = pd.bdate_range('2026-10-01', '2026-10-13')
    def record(adjusted, close, basis):
        return {'frame': pd.DataFrame({'Adj Close': adjusted, 'Close': close}, index=index),
                'currency': 'KRW', 'return_basis': basis, 'source_url': 'https://example.com/prices'}
    records = {'KR:A': record(100, 100, 'price'),
               'KR:B': record(list(range(100, 100 + len(index))), 100, 'total_return')}
    frame, _ = normalized_prices(set(records), records, date(2026, 10, 14))
    assert frame['KR:B'].eq(1).all()  # Cannot rank one asset with dividends and another without them.
    result = comparison({'KR:B': 1}, {'KR:A': 1}, records, date(2026, 10, 14))
    assert '분배금 제외' in result['method']
    assert '분배금 제외' in result['basis']
    standalone = comparison({'KR:B': 1}, None, records, date(2026, 10, 14))
    assert '분배금 제외' not in standalone['basis']
    assert standalone['points'][-1]['recommended'] > standalone['points'][0]['recommended']
    records['KR:B']['frame'].loc[index[:3], 'Adj Close'] = float('nan')
    result = comparison({'KR:B': 1}, {'KR:A': 1}, records, date(2026, 10, 14))
    assert result['start_date'] == '2026-10-01'
    # A group must use the shared batch basis even when it has no fallback asset itself.
    frame, _ = normalized_prices({'KR:B'}, records, date(2026, 10, 14),
                                 (index[0], index[-1]), column='Close')
    assert frame['KR:B'].eq(1).all()


def test_invalid_observation_uses_previous_price_without_inventing_pre_inception_history():
    from edge_portfolio.market_data import carry_missing_prices
    index = pd.bdate_range('2026-10-01', periods=3)
    prices = pd.DataFrame({'Close': [100., 0., 102.]}, index=index)
    filled, diagnostic = carry_missing_prices(prices, index, index[0], index[-1])
    assert list(filled['Close']) == [100., 100., 102.]
    assert diagnostic['dates'] == [str(index[1].date())]
    with pytest.raises(ValueError, match='PRICE_INVALID'):
        carry_missing_prices(prices.iloc[1:], index, index[0], index[-1])


def test_unused_close_gap_does_not_block_a_complete_adjusted_return_series():
    index = pd.bdate_range('2026-10-01', '2026-10-13')
    record = {'frame': pd.DataFrame({'Adj Close': 100., 'Close': 100.}, index=index),
              'currency': 'KRW', 'source_url': 'https://example.com/prices'}
    record['frame'].loc[index[0], 'Close'] = 0.
    frame, _ = normalized_prices({'KR:A'}, {'KR:A': record}, date(2026, 10, 14))
    assert frame.index[0] == index[0]
    assert frame['KR:A'].eq(1).all()


async def test_official_dynamic_page_exposes_published_holdings_and_distribution_rows(monkeypatch):
    calls = []
    async def get(self, url, **kwargs):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request('GET', url), json={
            'info': {'product': {'stkTicker': '379800', 'fNm': 'KODEX S&P500', 'gijunYMD': '20261013'},
                     'divideList': [{'BASIC_D': '20260731', 'DIVID_A': 132, 'PAY_D': '20260804'}]},
            'pdf': {'gijunYMD': '20261014', 'totalCnt': 1, 'list': [{'secNm': 'APPLE', 'itmNo': 'AAPL', 'ratio': 7}]}})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    body = await fetch_official_source('https://m.samsungfund.com/etf/product/view.do?id=2ETFE4')
    assert 'APPLE' in body and '132' in body and '2026-07-31' in body
    assert calls == ['https://www.samsungfund.com/api/v1/kodex/product/2ETFE4.do']
    assert await fetch_official_source('https://untrusted.example/etf/product/view.do?id=2ETFE4') is None
    assert len(calls) == 1


async def test_official_feed_enters_the_existing_citable_evidence_path(monkeypatch):
    from edge_portfolio.agent import ExaEvidence
    async def official(url):
        return 'Published product holdings and distributions as of 2026-10-13. ' * 20
    monkeypatch.setattr('edge_portfolio.agent.fetch_official_source', official)
    evidence = ExaEvidence(None)  # A successful official read must not require Exa extraction.
    url = 'https://m.samsungfund.com/etf/product/view.do?id=2ETFE4'
    evidence.discovered.add(url)
    await evidence.call('fetch_source', {'url': url})
    assert evidence.sources[0]['url'] == url
    assert evidence.sources[0]['passages'][0]['id'] == 'p1'
    assert evidence.attempts[0]['status'] == 'available'
