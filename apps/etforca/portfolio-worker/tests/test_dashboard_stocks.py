"""Stocks must reuse the shared quote cache without changing ETF analysis scope."""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from edge_portfolio import api, catalog, dashboard, dashboard_work as work, market_data, store
from test_dashboard_work import asset, record
from test_store import isolated_db  # noqa: F401


CASES = [('KR:005930', '005930.KS', 'KRW'), ('KR:247540', '247540.KQ', 'KRW'),
         ('US:AAPL', 'AAPL', 'USD'), ('US:BRK.B', 'BRK-B', 'USD')]


def test_dashboard_catalog_is_complete_unique_and_separate_from_analysis(monkeypatch):
    monkeypatch.delenv('PORTFOLIO_ORIGIN_SECRET', raising=False)
    client = TestClient(api.app)
    response = client.get('/api/portfolio/workbook/catalog')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'public, max-age=3600'
    rows = response.json()['instruments']
    assert len({row['id'] for row in rows}) == len(rows)
    assert sum(row['kind'] == 'stock' for row in rows) > 8000
    assert all(row['kind'] != 'stock' for row in client.get('/api/portfolio/catalog').json()['instruments'])


@pytest.mark.parametrize('key,symbol,currency', CASES)
async def test_stock_save_quotes_profit_and_dividends_share_existing_storage(isolated_db, monkeypatch, key, symbol, currency):
    day = date(2026, 10, 18)
    monkeypatch.setattr(work, 'today', lambda: day)
    holding = {**asset(), 'instrument_id': key, 'currency': currency, 'account': '일반 계좌'}
    saved = dashboard.save('stock-test', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[holding])))
    assert saved['workbook']['assets'][0]['category'] == '주식'
    assert dashboard.read('stock-test')['workbook']['assets'][0]['instrument_id'] == key
    assert catalog.price_symbol(key) == symbol
    calls = []

    async def fetch(actual, end, **kwargs):
        calls.append(actual)
        result = record(currency='KRW' if actual == 'KRW=X' else currency)
        result['frame'] = pd.DataFrame({'Close': 1400. if actual == 'KRW=X' else 110.,
                                       'Adj Close': 1400. if actual == 'KRW=X' else 110., 'Dividends': 0.},
                                      index=pd.bdate_range('2025-10-01', '2026-10-16'))
        if actual != 'KRW=X':
            result['frame'].loc['2026-10-15', 'Dividends'] = 2.
        return result

    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    keys = [key] + (['FX'] if currency == 'USD' else [])
    records, failures = await work.records_for(keys, day, valuation_since={key: day})
    assert failures == [] and set(calls) == {catalog.price_symbol(item) for item in keys}
    again, failures = await work.records_for(keys, day, valuation_since={key: day})
    assert len(calls) == len(keys), 'Repeated reads must not refetch stock quotes'
    value = work.valuation(saved['workbook'], again, None, day)
    fx = 1400 if currency == 'USD' else 1
    assert value['assets'][0]['value'] == 1100 * fx
    assert value['assets'][0]['profit'] == 300 * fx
    assert value['dividend_missing'] == []
    assert sum(row['amount'] for row in value['forecasts']) == 20 * fx
    with store.connect() as db:
        assert db.execute('SELECT instrument FROM portfolio_dashboard_prices WHERE instrument=%s', (key,)).fetchone()
        assert db.execute('SELECT instrument FROM portfolio_quote_refresh WHERE instrument=%s', (key,)).fetchone()


@pytest.mark.parametrize('key,symbol,currency', CASES)
def test_stock_identity_does_not_accept_the_wrong_currency(key, symbol, currency):
    with pytest.raises(ValueError, match='SELECT_LISTED_INSTRUMENT'):
        work.identify({**asset(), 'instrument_id': key, 'currency': 'USD' if currency == 'KRW' else 'KRW'})


def test_long_official_stock_name_can_be_saved_again_without_blocking_other_edits(isolated_db):
    stock = catalog.instruments()['US:WTFCN']
    holding = {**asset(), 'instrument_id': stock['id'], 'name': stock['name'], 'currency': 'USD'}
    first = dashboard.save('long-stock', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[holding])))
    returned = dashboard.Save(version=first['version'], workbook=first['workbook'])
    returned.workbook.assets[0].quantity = 20.
    second = dashboard.save('long-stock', returned)
    assert second['workbook']['assets'][0]['name'] == stock['name']
    assert second['workbook']['assets'][0]['quantity'] == 20.
