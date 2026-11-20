"""Append-only daily bars, cash distributions and splits. Adjusted prices are computed on read: a new distribution
changes every earlier adjusted price, so storing them would force rewriting the history this ledger keeps."""
import logging
import math
import sys
import time

import pandas as pd

log = logging.getLogger(__name__)

PRICE_COLUMNS = ('Open', 'High', 'Low', 'Close')
# A stored close and a new fetch of the same day disagree by more than this only if one of them is on another split
# basis (Yahoo can scale its history before listing the split, or the reverse); provider corrections are smaller.
OVERLAP_TOLERANCE = .01


def initialize(db):
    # Prices are in the share units of their own day (before later splits), so a stored row never changes meaning.
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_price_bars (
        instrument text NOT NULL, day date NOT NULL, open double precision, high double precision,
        low double precision, close double precision NOT NULL, volume double precision,
        observed_on date NOT NULL, PRIMARY KEY(instrument,day))''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_price_actions (
        instrument text NOT NULL, day date NOT NULL, dividend double precision NOT NULL,
        split double precision NOT NULL, observed_on date NOT NULL, PRIMARY KEY(instrument,day))''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_price_backfill (
        instrument text PRIMARY KEY, finished_at timestamptz NOT NULL DEFAULT now(), result text NOT NULL)''')


def facts(frame):
    """Yahoo's daily frame (auto_adjust=False, actions=True) as unchanging facts. Yahoo scales prices, volumes and
    dividends by every split after each day up to the fetch; multiplying that back out gives the same values from
    any later fetch, so a short daily fetch and a full-history backfill agree row for row."""
    splits = frame['Stock Splits'].fillna(0.0)
    splits = splits.where(splits > 0, 1.0)
    later = splits[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    close = frame['Close'].where(frame['Close'].map(_positive))
    bars = pd.DataFrame({name.lower(): frame[name].where(frame[name].map(_positive)) * later
                         for name in PRICE_COLUMNS if name in frame}, index=frame.index)
    bars['volume'] = frame['Volume'] / later if 'Volume' in frame else float('nan')
    bars = bars[close.notna()]
    dividends = frame['Dividends'].fillna(0.0) * later
    actions = pd.DataFrame({'dividend': dividends, 'split': splits})
    # A distribution can sit on a row without a price (Yahoo omits some sessions); it is still an event.
    return bars, actions[(actions['dividend'] > 0) | (actions['split'] != 1)]


def record(db, key, record):
    """Add one fetch to the ledger and return the stored bar count. Overlapping days take the newer observation
    (provider corrections). Bars are never deleted; within the fetched days, actions the provider withdrew are."""
    frame = record['frame']
    if record.get('provider') or record.get('pending_splits') or not {'Dividends', 'Stock Splits'} <= set(frame.columns):
        # The fallback's closes may already include distributions, and a split announced for a coming session may
        # already scale Yahoo's history without appearing in the frame: neither yields the unchanging values.
        return 0
    bars, actions = facts(frame)
    if bars.empty:
        return 0
    observed = record.get('observed_on') or str(bars.index[-1].date())
    first, last = frame.index[0].date(), frame.index[-1].date()
    db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('portfolio-price-ledger:' + key,))
    stored = db.execute('SELECT day,close FROM portfolio_price_bars WHERE instrument=%s AND day BETWEEN %s AND %s',
                        (key, first, last)).fetchall()
    known = pd.Series({pd.Timestamp(r['day']): r['close'] for r in stored}, dtype=float)
    common = known.index.intersection(bars.index)
    if len(common) and ((bars.loc[common, 'close'] / known[common] - 1).abs() > OVERLAP_TOLERANCE).any():
        log.warning('price ledger instrument=%s rejected=SPLIT_BASIS_MISMATCH days=%s', key, len(common))
        return 0
    db.cursor().executemany('''INSERT INTO portfolio_price_bars VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(instrument,day) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,
            close=excluded.close,volume=excluded.volume,observed_on=excluded.observed_on
        WHERE portfolio_price_bars.observed_on<=excluded.observed_on''',
        [(key, day.date(), *(_value(row.get(name)) for name in ('open', 'high', 'low', 'close', 'volume')), observed)
         for day, row in bars.iterrows()])
    db.execute('''DELETE FROM portfolio_price_actions WHERE instrument=%s AND day BETWEEN %s AND %s
        AND observed_on<=%s AND NOT (day=ANY(%s))''', (key, first, last, observed, [d.date() for d in actions.index]))
    db.cursor().executemany('''INSERT INTO portfolio_price_actions VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT(instrument,day) DO UPDATE SET dividend=excluded.dividend,split=excluded.split,observed_on=excluded.observed_on
        WHERE portfolio_price_actions.observed_on<=excluded.observed_on''',
        [(key, day.date(), float(row['dividend']), float(row['split']), observed) for day, row in actions.iterrows()])
    return len(bars)


def adjust(bars, actions):
    """`bars`: closes by day; `actions`: dividend and split by day. Returns the close, the cumulative total-return
    factor and the close adjusted to the last day. The factor only steps up from each ex-date or split onward, so a
    new event never changes earlier factors; only the last-day divisor moves."""
    days = bars.index.union(actions.index).sort_values()
    close = bars['close'].reindex(days)
    dividend = actions['dividend'].reindex(days, fill_value=0.0)
    split = actions['split'].reindex(days, fill_value=1.0)
    # The last close before the ex-date, in that day's share units: every split since that close divides it,
    # including one on the ex-date itself (Korean stock dividends often share the ex-date with cash).
    splits_so_far = split.cumprod()
    before = (close * splits_so_far).ffill().shift(1) / splits_so_far
    step = pd.Series(1.0, index=days)
    # A distribution at or above the previous close has no meaningful ratio (a data error); it is left out.
    paid = (dividend > 0) & before.notna() & (before > dividend)
    step[paid] = before[paid] / (before[paid] - dividend[paid])
    factor = (step * split).cumprod()
    result = pd.DataFrame({'close': close, 'factor': factor})[close.notna()]
    result['adjusted'] = result['close'] * result['factor'] / result['factor'].iloc[-1] if len(result) else []
    return result


def adjusted(db, key, end=None):
    """The ledger's adjusted closes for `key` up to `end` (default: the last stored day)."""
    bound = 'AND day<=%s' if end else ''
    params = (key, end) if end else (key,)
    bars = pd.DataFrame(db.execute(f'SELECT day,close FROM portfolio_price_bars WHERE instrument=%s {bound} ORDER BY day',
                                   params).fetchall(), columns=['day', 'close'])
    actions = pd.DataFrame(db.execute(f'SELECT day,dividend,split FROM portfolio_price_actions WHERE instrument=%s {bound} ORDER BY day',
                                      params).fetchall(), columns=['day', 'dividend', 'split'])
    return adjust(bars.set_index(pd.to_datetime(bars['day']))[['close']],
                  actions.set_index(pd.to_datetime(actions['day']))[['dividend', 'split']])


def backfill(keys, *, today=None, pause=1.0, wait=900):
    """Fetch each instrument's full Yahoo history once into the ledger. Finished instruments are marked, so the run
    can stop and resume; a provider rate limit waits `wait` seconds (the daily collector shares the source)."""
    from . import store
    from .catalog import price_symbol
    from .dashboard_work import today as kst_today
    from .market_data import load_prices, price_failure
    today = today or kst_today()
    for key in keys:
        with store.connect() as db:
            if db.execute('SELECT 1 FROM portfolio_price_backfill WHERE instrument=%s', (key,)).fetchone():
                continue
        for attempt in range(3):
            try:
                fetched = load_prices(price_symbol(key), today)
                with store.connect() as db:
                    result = str(record(db, key, fetched))
                break
            except Exception as error:
                result = price_failure(error)
                if result != 'PRICE_PROVIDER_RATE_LIMIT' or attempt == 2:
                    break
                time.sleep(wait)
        if result != 'PRICE_PROVIDER_RATE_LIMIT':
            # Unavailable or delisted symbols are marked too, with the reason; delete the mark to retry one.
            with store.connect() as db:
                db.execute('''INSERT INTO portfolio_price_backfill(instrument,result) VALUES (%s,%s)
                    ON CONFLICT(instrument) DO UPDATE SET finished_at=now(),result=excluded.result''', (key, result))
        log.info('price ledger backfill instrument=%s result=%s', key, result)
        time.sleep(pause)


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _value(value):
    return float(value) if value is not None and math.isfinite(value) else None


if __name__ == '__main__':
    # Run in its own container so its memory is not counted against the worker's limit:
    # docker compose run --rm worker python -m edge_portfolio.price_ledger [instrument ...]  (default: catalogue and FX)
    from .catalog import instruments
    logging.basicConfig(level=logging.INFO)
    backfill(sys.argv[1:] or ['FX', *sorted(instruments())])
