from datetime import date

import pandas as pd
import pytest

from edge_portfolio.data_providers import load_fallback_prices
from edge_portfolio.contracts import AnalysisInput
from edge_portfolio.market_data import MarketData, comparison


def test_naver_etf_adjustments_are_kept_without_adding_distributions_twice(monkeypatch):
    # Provider fixture: adjusted NAVER quotes around a cash distribution.
    index = pd.to_datetime(['2026-07-29', '2026-07-30', '2026-07-31'])
    monkeypatch.setattr('FinanceDataReader.DataReader', lambda *args: pd.DataFrame(
        {'Close': [90., 90., 99.]}, index=index))
    record = load_fallback_prices('379800.KS', date(2026, 8, 1))
    assert record['return_basis'] == 'total_return'
    assert record['frame']['Adj Close'].tolist() == [90., 90., 99.]
    history = comparison({'KR:A': 1}, None, {'KR:A': record}, date(2026, 8, 1))
    assert history['points'][-1]['recommended'] == pytest.approx(1100000)
    assert '분배금 재투자' in history['method']
    market = MarketData(AnalysisInput(holdings=[], planning=True, horizon='10년 이상'), date(2026, 8, 1))
    market.records['KR:A'] = record
    assert market.dividends('KR:A') is None


def test_naver_missing_sessions_keep_previous_price_and_fill_provenance(monkeypatch):
    index = pd.to_datetime(['2026-07-28', '2026-07-30', '2026-07-31'])
    monkeypatch.setattr('FinanceDataReader.DataReader', lambda *args: pd.DataFrame(
        {'Close': [90., 90., 99.]}, index=index))
    record = load_fallback_prices('379800.KS', date(2026, 8, 1))
    history = comparison({'KR:A': 1}, None, {'KR:A': record}, date(2026, 8, 1))
    assert history['sources'][0]['price_fill']['dates'] == ['2026-07-29']
    assert history['points'][-1]['recommended'] == pytest.approx(1100000)
