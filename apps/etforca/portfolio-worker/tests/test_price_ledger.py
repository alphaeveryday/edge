import math
from datetime import date

import pandas as pd
import pytest

from edge_portfolio import dashboard_work as work, price_ledger as ledger, store
from test_store import isolated_db  # noqa: F401

DAYS = pd.bdate_range('2026-09-01', periods=12)
# What happened, in each day's own share units: a 2-for-1 split on day 6, a cash distribution on a day Yahoo has no
# price for (day 7), and a Korean-style stock dividend (1.05) on the same ex-date as cash (day 9).
RAW = pd.Series([100., 101, 102, 103, 104, 52, None, 53, 50, 51, 52, 53], index=DAYS)
CASH = pd.Series(0., index=DAYS)
CASH.iloc[3], CASH.iloc[6], CASH.iloc[8] = 2., 1., .5
SPLITS = pd.Series(0., index=DAYS)
SPLITS.iloc[5], SPLITS.iloc[8] = 2., 1.05


def yahoo(fetched, start=None):
    """Yahoo's frame as fetched on `fetched`: every value divided (volume multiplied) by the splits after its day up
    to the fetch, and Adj Close scaled back from the fetch by 1 - cash/previous close at each ex-date."""
    days = DAYS[DAYS <= fetched]
    ratio = SPLITS[days].where(SPLITS[days] > 0, 1.)
    later = ratio[::-1].cumprod()[::-1].shift(-1, fill_value=1.)
    close, cash = RAW[days] / later, CASH[days] / later
    multiplier = pd.Series(1., index=days)
    for i, day in enumerate(days):
        if cash[day] > 0:
            before = close[:day].iloc[:-1].dropna().iloc[-1]
            multiplier[days[:i]] *= 1 - cash[day] / before
    frame = pd.DataFrame({'Open': close, 'High': close, 'Low': close, 'Close': close, 'Adj Close': close * multiplier,
                          'Volume': 1000. * later, 'Dividends': cash, 'Stock Splits': SPLITS[days]}, index=days)
    return frame[frame.index >= start] if start is not None else frame


def test_a_short_daily_fetch_stores_the_same_rows_as_a_later_full_history_fetch():
    # Yahoo rescales earlier rows after every split; the ledger must still never need to rewrite a stored row.
    full_bars, full_actions = ledger.facts(yahoo(DAYS[-1]))
    for fetched in DAYS[3:]:
        bars, actions = ledger.facts(yahoo(fetched, start=fetched - pd.Timedelta(days=6)))
        pd.testing.assert_frame_equal(bars, full_bars.loc[bars.index], check_freq=False)
        pd.testing.assert_frame_equal(actions, full_actions.loc[actions.index], check_freq=False)
    assert list(full_bars['close']) == list(RAW.dropna()), 'Stored closes are in each day\'s own share units'
    assert full_bars['volume'].iloc[0] == 1000., 'Volume is in the day\'s own shares too'
    assert DAYS[6] in full_actions.index, 'A distribution on a day without a price is kept'


@pytest.mark.parametrize('fetched', [DAYS[4], DAYS[8], DAYS[-1]])
def test_adjusted_closes_from_the_ledger_match_yahoo_adjusted_closes_as_of_each_fetch(fetched):
    # Adjusted prices are derived on read. The derivation must equal Yahoo's backward adjustment on the day it is
    # read, including the same-day split and cash, so returns do not change by where they were computed.
    frame = yahoo(fetched)
    bars, actions = ledger.facts(frame)
    result = ledger.adjust(bars, actions)
    expected = frame['Adj Close'][frame['Close'].notna()]
    assert [round(v, 9) for v in result['adjusted']] == [round(v, 9) for v in expected]


def test_a_new_distribution_changes_no_earlier_factor():
    early = ledger.adjust(*ledger.facts(yahoo(DAYS[2])))
    late = ledger.adjust(*ledger.facts(yahoo(DAYS[-1])))
    assert list(late['factor'][early.index]) == list(early['factor'])


def test_ledger_keeps_days_the_windowed_record_drops_and_takes_corrections(isolated_db):
    with store.connect() as db:
        assert ledger.record(db, 'KR:000001', {'frame': yahoo(DAYS[5]), 'currency': 'KRW'}) == 6
        early = ledger.adjusted(db, 'KR:000001')['adjusted']
        assert [round(v, 9) for v in early] == [round(v, 9) for v in yahoo(DAYS[5])['Adj Close']]
        corrected = yahoo(DAYS[-1], start=DAYS[5])
        corrected.loc[DAYS[8], 'Dividends'] = .6  # The provider revised the amount.
        ledger.record(db, 'KR:000001', {'frame': corrected, 'currency': 'KRW'})
        stored = db.execute('SELECT day,close FROM portfolio_price_bars ORDER BY day').fetchall()
        assert [r['day'] for r in stored] == [d.date() for d in RAW.dropna().index], 'Days before the second fetch stay'
        assert [r['close'] for r in stored] == list(RAW.dropna())
        cash = db.execute("SELECT dividend FROM portfolio_price_actions WHERE day=%s", (DAYS[8].date(),)).fetchone()
        assert math.isclose(cash['dividend'], .6)


def test_a_split_on_a_day_without_a_price_still_scales_a_later_distribution():
    # Day 1 splits 2-for-1 and day 2 pays 1 per new share; Yahoo has no price on either day. The previous close in
    # new shares is 50, so day 0 adjusts to 100 / 2 * (1 - 1/50) = 49.
    days = pd.bdate_range('2026-09-01', periods=4)
    frame = pd.DataFrame({'Close': [50., None, None, 50.], 'Dividends': [0., 0., 1., 0.],
                          'Stock Splits': [0., 2., 0., 0.]}, index=days)
    result = ledger.adjust(*ledger.facts(frame))
    assert list(result['close']) == [100., 50.]
    assert math.isclose(result['adjusted'].iloc[0], 49.)


def test_a_fetch_on_another_split_basis_is_not_stored(isolated_db):
    # Yahoo can scale its history before it lists the split (or the reverse). Such a fetch would store every
    # overlapping day off by the split ratio, so it is rejected and the stored days stay.
    with store.connect() as db:
        ledger.record(db, 'KR:000001', {'frame': yahoo(DAYS[5]), 'currency': 'KRW'})
        unlisted = yahoo(DAYS[5])
        unlisted[['Open', 'High', 'Low', 'Close']] /= 2
        assert ledger.record(db, 'KR:000001', {'frame': unlisted, 'currency': 'KRW', 'observed_on': str(DAYS[6].date())}) == 0
        assert [r['close'] for r in db.execute('SELECT close FROM portfolio_price_bars ORDER BY day')] == list(RAW[:6])


def test_a_distribution_the_provider_withdrew_is_removed_within_the_fetched_days(isolated_db):
    # A wrong distribution kept forever would lower every earlier adjusted close; outside the fetch nothing changes.
    with store.connect() as db:
        ledger.record(db, 'KR:000001', {'frame': yahoo(DAYS[-1]), 'currency': 'KRW'})
        withdrawn = yahoo(DAYS[-1], start=DAYS[2])
        withdrawn.loc[DAYS[3], 'Dividends'] = 0.
        ledger.record(db, 'KR:000001', {'frame': withdrawn, 'currency': 'KRW', 'observed_on': '2026-10-01'})
        days = [r['day'] for r in db.execute('SELECT day FROM portfolio_price_actions ORDER BY day')]
        assert days == [DAYS[5].date(), DAYS[6].date(), DAYS[8].date()]


@pytest.mark.parametrize('extra', [{'provider': 'FinanceDataReader:NAVER:ETF'}, {'pending_splits': ['2026-09-17']}])
def test_closes_that_may_already_be_adjusted_are_not_stored(isolated_db, extra):
    # NAVER's ETF closes include distributions; a split announced for a coming session may already scale Yahoo's
    # history without being in the frame. Either would store a value that is not the day's own close.
    with store.connect() as db:
        assert ledger.record(db, 'KR:000001', {'frame': yahoo(DAYS[-1]), 'currency': 'KRW', **extra}) == 0
        assert db.execute('SELECT count(*) AS n FROM portfolio_price_bars').fetchone()['n'] == 0


def test_published_prices_are_also_added_to_the_ledger(isolated_db):
    frame = yahoo(DAYS[-1])
    with store.connect() as db:
        assert work.publish_prices(db, 'KR:000001', date(2026, 9, 17), {'frame': frame, 'currency': 'KRW',
            'source_url': 'https://example.com/prices', 'requested_at': '2026-09-17T00:00:00+00:00'})
        assert db.execute('SELECT count(*) AS n FROM portfolio_price_bars').fetchone()['n'] == len(RAW.dropna())


def test_a_ledger_failure_does_not_withhold_current_prices(isolated_db, monkeypatch):
    def broken(db, key, record):
        db.execute('SELECT 1/0')
    monkeypatch.setattr(ledger, 'record', broken)
    with store.connect() as db:
        assert work.publish_prices(db, 'KR:000001', date(2026, 9, 17), {'frame': yahoo(DAYS[-1]), 'currency': 'KRW',
            'source_url': 'https://example.com/prices', 'requested_at': '2026-09-17T00:00:00+00:00'})
    with store.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM portfolio_dashboard_prices').fetchone()['n'] == 1


def test_backfill_fetches_each_instrument_once_and_resumes_after_a_rate_limit(isolated_db, monkeypatch):
    from edge_portfolio import market_data
    calls = []
    def load(symbol, as_of):
        calls.append(symbol)
        if symbol == 'LIMITED.KS':
            raise ValueError('PRICE_PROVIDER_RATE_LIMIT')
        return {'frame': yahoo(DAYS[-1]), 'currency': 'KRW'}
    monkeypatch.setattr(market_data, 'load_prices', load)
    monkeypatch.setattr('edge_portfolio.catalog.price_symbol', lambda key: key[3:] + '.KS')
    ledger.backfill(['KR:000001', 'KR:LIMITED'], today=date(2026, 9, 17), pause=0, wait=0)
    ledger.backfill(['KR:000001', 'KR:LIMITED'], today=date(2026, 9, 17), pause=0, wait=0)
    assert calls == ['000001.KS'] + ['LIMITED.KS'] * 6, 'A finished instrument is not fetched again; a rate limit is'
    with store.connect() as db:
        assert db.execute('SELECT instrument,result FROM portfolio_price_backfill').fetchall() == [
            {'instrument': 'KR:000001', 'result': str(len(RAW.dropna()))}]
