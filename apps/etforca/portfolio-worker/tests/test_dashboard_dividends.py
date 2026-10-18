from datetime import date
import pandas as pd
import pytest
from edge_portfolio import dashboard_work as work


def history(months):
    frame = pd.DataFrame({'Close': 100., 'Adj Close': 100., 'Dividends': 0.}, index=pd.bdate_range('2025-10-01', '2026-10-16'))
    for month in months:
        days = frame.loc[frame.index.strftime('%Y-%m') == month].index
        frame.loc[days[5], 'Dividends'] = 1.
    return {'frame': frame, 'currency': 'KRW', 'source_url': 'https://example.com'}


@pytest.mark.parametrize('months,cycle', [
    ([], '최근 1년 지급 없음'),
    (['2026-01'], '연 1회'),
    (['2026-01', '2026-07'], '반기'),
    (['2026-01', '2026-04', '2026-07', '2026-10'], '분기'),
    (['2026-01', '2026-02', '2026-03', '2026-04'], '비정기'),
    ([f'2026-{m:02}' for m in range(1, 11)] + ['2025-11', '2025-12'], '매월'),
])
def test_cycle_uses_observed_spacing_not_just_event_count(months, cycle):
    details = work.dividend_details(history(months), 'KR:069500')
    assert details['dividend_cycle'].startswith(cycle)
    assert details['dividend_yield_pct'] == len(months)


def test_short_history_stays_unknown_not_zero_or_annual():
    record = history(['2026-10'])
    record['frame'] = record['frame'].iloc[-20:]
    assert work.dividend_details(record, 'KR:069500') == {'dividend_yield_pct': None, 'annual_dividend_per_share': None, 'dividend_cycle': '미확인'}


def test_rolling_year_can_contain_thirteen_monthly_payment_months():
    record = history([f'2026-{m:02}' for m in range(1, 11)] + ['2025-11', '2025-12'])
    record['frame'].loc['2025-10-20', 'Dividends'] = 1.
    assert work.dividend_details(record, 'KR:069500')['dividend_cycle'].startswith('매월')


def test_weekly_distributions_are_not_presented_as_one_monthly_payment():
    record = history([])
    frame = record['frame']
    frame.loc[frame.index.weekday == 4, 'Dividends'] = 1.
    assert work.dividend_details(record, 'KR:069500')['dividend_cycle'].startswith('비정기')


def test_cash_distributions_never_buy_additional_shares():
    record = history(['2026-01', '2026-04', '2026-07', '2026-10'])
    forecast = work.forecast({'id': 'a', 'instrument_id': 'KR:069500'}, record, 10, 1400, date(2026, 10, 18))
    assert len(forecast) == 4
    assert all(row['amount'] == 14000 for row in forecast)
    assert sum(row['amount'] for row in forecast) == 56000


def test_annual_cash_keeps_original_distributions_instead_of_rounded_yield():
    record = history(['2026-01', '2026-07'])
    record['frame']['Close'] = 600.
    record['frame']['Dividends'] *= 4
    details = work.dividend_details(record, 'KR:069500')
    assert details['dividend_yield_pct'] == 1.3333
    assert details['annual_dividend_per_share'] == 8
