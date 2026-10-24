"""Daily dashboard valuation on the existing worker."""
import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
from psycopg.types.json import Jsonb

from . import store
from .catalog import directory, price_symbol

log = logging.getLogger(__name__)


def today():
    return datetime.now(ZoneInfo('Asia/Seoul')).date()


def identify(asset, *, strict=True, fx=None):
    asset = dict(asset)
    if asset['category'] == '현금' or asset.get('instrument_id') == 'CASH:KRW':
        if asset.get('quantity') is not None and asset.get('price') is not None:
            if asset['currency'] == 'USD' and fx is None:
                return asset
            asset.update(amount=asset['quantity'] * asset['price'] * (fx if asset['currency'] == 'USD' else 1), quantity=None, price=None)
        return {**asset, 'instrument_id': 'CASH:KRW', 'currency': 'KRW', 'category': '현금'}
    rows = directory(include_stocks=True)['instruments']
    key = asset.get('instrument_id')
    matches = [r for r in rows if r['id'] == key] if key else [r for r in rows
        if asset['name'].casefold() in (r['ticker'].casefold(), r['name'].casefold(), f"{r['name']} ({r['ticker']})".casefold())]
    if key and not matches:
        if strict:
            raise ValueError('SELECT_LISTED_INSTRUMENT')
        asset['instrument_id'] = None
    if len(matches) == 1:
        item = matches[0]
        if asset['currency'] != ('USD' if item['market'] == 'US' else 'KRW'):
            if strict:
                raise ValueError('SELECT_LISTED_INSTRUMENT')
            asset['instrument_id'] = None
            return asset
        asset.update(instrument_id=item['id'], currency='USD' if item['market'] == 'US' else 'KRW', name=f"{item['name']} ({item['ticker']})")
        if item['kind'] == 'stock':
            asset.update(category='주식', name=item['name'])
    return asset


def initialize(db):
    from . import dashboard_quotes
    dashboard_quotes.initialize(db)
    db.execute('ALTER TABLE portfolio_workbooks ADD COLUMN IF NOT EXISTS refresh_after timestamptz NOT NULL DEFAULT now()')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_dashboard_market (
        owner text PRIMARY KEY, version bigint NOT NULL, payload jsonb NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_dashboard_days (
        owner text NOT NULL, day date NOT NULL, version bigint NOT NULL, payload jsonb NOT NULL,
        PRIMARY KEY(owner,day))''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_dashboard_prices (
        instrument text PRIMARY KEY, day date NOT NULL, payload jsonb NOT NULL)''')


def tracking(owner):
    with store.connect() as db:
        market = db.execute('SELECT version,payload FROM portfolio_dashboard_market WHERE owner=%s', (owner,)).fetchone()
        days = db.execute('SELECT day,payload FROM portfolio_dashboard_days WHERE owner=%s ORDER BY day', (owner,)).fetchall()
        archives = db.execute('SELECT version,payload FROM portfolio_workbook_archives WHERE owner=%s ORDER BY version DESC', (owner,)).fetchall()
    return {'market': market, 'days': [{'date': str(r['day']), **r['payload']} for r in days], 'archives': archives}


@lru_cache(maxsize=16)
def price_schedule(korean, day):
    from .market_data import calendars
    return calendars.get_calendar('XKRX' if korean else 'XNYS', start=day - timedelta(days=30), end=day + timedelta(days=1)).schedule


def price_end(key, day, now=None):
    now = now or datetime.now(ZoneInfo('UTC'))
    if key == 'FX':
        return min(day, now.astimezone(ZoneInfo('UTC')).date())
    korean = key.startswith('KR:')
    schedule = price_schedule(korean, day)
    completed = schedule[(schedule['close'] <= now) & (schedule.index.date <= day if korean else schedule.index.date < day)]
    return completed.index[-1].date() + timedelta(days=1)


def held_history_missing(key, record, since, end):
    if since is None or key == 'FX':
        return False
    from .market_data import expected_sessions, positive_price
    frame = record['frame']
    return bool(len(expected_sessions(key, min(since, end), end).difference(frame.index[frame['Close'].map(positive_price)])))


def publish_prices(db, key, end, record, *, collector_lease=None):
    from .market_data import decode_prices, positive_price
    from .price_fetch import serialize
    observed_on = last_close(record)[1]
    if collector_lease is not None and not db.execute('SELECT 1 FROM portfolio_quote_refresh WHERE instrument=%s AND lease_until=%s FOR UPDATE', (key, collector_lease)).fetchone():
        return False
    # Serialize read/merge/write; a partial daily update must retain the archive.
    db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('portfolio-price:' + key,))
    old = db.execute('SELECT payload FROM portfolio_dashboard_prices WHERE instrument=%s', (key,)).fetchone()
    saved = decode_prices(json.dumps(old['payload'])) if old else None
    if saved:
        record = {**record, 'pending_splits': sorted(set(record.get('pending_splits', [])) |
            {day for day in saved.get('pending_splits', []) if day >= str(end)})}
    if saved and record.get('history_complete') is False:
        overlap = saved['frame'].index.intersection(record['frame'].index)
        # Changed adjustment/split basis needs a fresh archive, never a mixed-basis chart.
        compatible = (saved['currency'] == record['currency'] and len(overlap) >= 2
            and saved.get('return_basis') != 'price' and not saved.get('provider', '').startswith('FinanceDataReader:NAVER:'))
        if compatible:
            pairs = [(a, b) for a, b in zip(saved['frame'].loc[overlap, 'Close'], record['frame'].loc[overlap, 'Close'])
                     if positive_price(a) and positive_price(b)]
            if not pairs:
                raise ValueError('PRICE_OVERLAP_UNAVAILABLE')
            compatible = all(math.isclose(a, b, rel_tol=1e-6) for a, b in pairs)
        if compatible:
            priced = overlap[saved['frame'].loc[overlap, 'Close'].map(positive_price) | record['frame'].loc[overlap, 'Close'].map(positive_price)]
            ratios = [b / a for a, b in zip(saved['frame'].loc[priced, 'Adj Close'], record['frame'].loc[priced, 'Adj Close'])
                      if positive_price(a) and positive_price(b)]
            anchored = len(ratios) == len(priced) and len(ratios) >= 2
            anchored = anchored and record.get('return_basis') != 'price' and all(math.isclose(ratio, ratios[0], rel_tol=1e-6) for ratio in ratios)
            action_dates = record['frame'].index[(record['frame'].index >= saved['frame'].index.min()) &
                                                (record['frame'].index <= saved['frame'].index.max())]
            for column in ('Dividends', 'Stock Splits'):
                if column in record['frame']:
                    previous = saved['frame'].get(column, pd.Series(dtype=float)).reindex(action_dates).fillna(0)
                    incoming = record['frame'].loc[action_dates, column].fillna(previous)
                    anchored = anchored and bool((incoming == previous).all())
            if anchored:
                # A new cash distribution rebases earlier adjusted closes by one factor.
                saved['frame']['Adj Close'] *= ratios[0]
            else:
                # Keep actual prices/actions usable, but never invent an adjustment basis.
                saved['frame']['Adj Close'] = float('nan')
                record = {**record, 'frame': record['frame'].copy()}
                record['frame']['Adj Close'] = float('nan')
                saved['history_complete'] = False
        if compatible:
            record = {**record, 'frame': record['frame'].copy()}
            if not record['frame'].loc[record['frame']['Close'].map(positive_price), 'Adj Close'].map(positive_price).all():
                saved['history_complete'] = False
            for column in ('Close', 'Adj Close'):
                missing = overlap[~record['frame'].loc[overlap, column].map(positive_price)]
                record['frame'].loc[missing, column] = saved['frame'].loc[missing, column]
            for column in ('Dividends', 'Stock Splits'):
                if column in saved['frame']:
                    if column not in record['frame']:
                        record['frame'][column] = saved['frame'][column].reindex(record['frame'].index)
                    else:
                        known = saved['frame'][column].reindex(record['frame'].index)
                        missing = ~record['frame'][column].map(positive_price) & known.map(positive_price)
                        record['frame'].loc[missing, column] = known.loc[missing]
            record = {**record, 'frame': pd.concat([saved['frame'].drop(record['frame'].index, errors='ignore'), record['frame']]).sort_index(),
                      'history_complete': saved.get('history_complete', True),
                      'repair_since': min(saved.get('repair_since', str(end)), record.get('repair_since', str(end)))}
            for metadata in ('return_basis', 'return_source_url', 'whole_history'):
                if metadata in saved and metadata not in record:
                    record[metadata] = saved[metadata]
            if saved.get('price_recovery', {}).get('status') in ('partial', 'unavailable', 'rejected'):
                record['price_recovery'] = saved['price_recovery']
    priced = record['frame']['Close'].map(positive_price)
    actions_complete = key == 'FX' or ({'Dividends', 'Stock Splits'} <= set(record['frame'].columns) and not any(
        record['frame'].loc[priced, column].isna().any()
        for column in ('Dividends', 'Stock Splits') if column in record['frame']))
    record = {**record, 'observed_on': observed_on, 'actions_complete': actions_complete}
    updated = db.execute('''INSERT INTO portfolio_dashboard_prices VALUES (%s,%s,%s)
        ON CONFLICT(instrument) DO UPDATE SET day=excluded.day,payload=excluded.payload
        WHERE (portfolio_dashboard_prices.day<excluded.day OR
            (portfolio_dashboard_prices.day=excluded.day AND coalesce(portfolio_dashboard_prices.payload->>'requested_at',portfolio_dashboard_prices.payload->>'fetched_at','')<=excluded.payload->>'requested_at'))
            AND excluded.payload->>'observed_on'>=coalesce(portfolio_dashboard_prices.payload->>'observed_on',
                (SELECT max(p->>'date') FROM jsonb_array_elements(portfolio_dashboard_prices.payload->'frame') p WHERE (p->>'Close')::numeric>0),'')
            AND (coalesce(excluded.payload->>'provider','') NOT LIKE 'FinanceDataReader:NAVER:%%'
                OR coalesce(portfolio_dashboard_prices.payload->>'provider','') LIKE 'FinanceDataReader:NAVER:%%')
        RETURNING instrument''', (key, end, Jsonb(json.loads(serialize(record))))).fetchone()
    return bool(updated)


async def records_for(ids, day, *, valuation_since=None, include_fx=True, collector_lease=None):
    from .market_data import decode_prices, fetch_prices, positive_price, year_before, with_actual_prices
    records, failures = {}, []
    keys = set(ids) - {'CASH:KRW', None}
    if include_fx and any(k.startswith('US:') for k in keys):
        keys.add('FX')
    # One subprocess at a time keeps the existing small worker within its memory limit.
    try:
        async with asyncio.timeout(210):
            for key in sorted(keys):
                since = (valuation_since or {}).get(key)
                end = price_end(key, day)
                with store.connect() as db:
                    cached = db.execute('SELECT payload FROM portfolio_dashboard_prices WHERE instrument=%s AND day=%s', (key, end)).fetchone()
                try:
                    if cached:
                        saved = decode_prices(json.dumps(cached['payload']))
                        needed_since = since or (end - timedelta(days=35) if valuation_since is not None else year_before(end, 2))
                        incomplete = date.fromisoformat(saved.get('repair_since', year_before(end, 2).isoformat())) > needed_since
                        if valuation_since is not None:
                            incomplete |= saved.get('actions_checked_on') != str(day)
                            expired = not saved.get('fetched_at') or datetime.fromisoformat(saved['fetched_at']) < datetime.now(ZoneInfo('UTC')) - timedelta(minutes=1)
                            incomplete |= expired
                        else:
                            incomplete |= saved.get('history_complete') is False
                            adjusted = saved['frame']['Adj Close'].map(positive_price)
                            incomplete |= saved.get('provider', '').startswith('FinanceDataReader:NAVER:') or saved.get('price_recovery', {}).get('status') in ('unavailable', 'partial')
                            incomplete |= key != 'FX' and (saved.get('return_basis') == 'price' or adjusted.sum() < 2)
                            incomplete |= last_close(saved)[1] != str(end - timedelta(days=1))
                            incomplete |= saved.get('actions_checked_on') != str(day)
                        if incomplete:
                            if valuation_since is None and saved.get('history_complete') is not False:
                                records[key] = saved
                            cached = None  # Retain usable observations while retrying missing data.
                    if cached:
                        record = decode_prices(json.dumps(cached['payload']))
                    else:
                        requested_at = datetime.now(ZoneInfo('UTC')).isoformat()
                        symbol = price_symbol(key)
                        expected = 'KRW' if key == 'FX' or key.startswith('KR:') else 'USD'
                        captured = None
                        def capture(record):
                            nonlocal captured
                            _, stamp = last_close(record)
                            if record['currency'] == expected and 0 <= (end - date.fromisoformat(stamp)).days <= 7:
                                captured = {**record, 'actions_checked_on': str(day), 'fetched_at': datetime.now(ZoneInfo('UTC')).isoformat(), 'requested_at': requested_at}
                                records[key] = captured
                        try:
                            budget = 200 / max(1, len(keys))
                            record = await fetch_prices(symbol, end, on_raw=capture, on_primary=capture, provider_timeout=budget * .45,
                                                        repair_since=since, **({'history_start': min(since or end, end - timedelta(days=35) if key == 'FX' else year_before(end) - timedelta(days=7))} if valuation_since is not None else {}))
                            record = {**record, 'actions_checked_on': str(day), 'fetched_at': datetime.now(ZoneInfo('UTC')).isoformat(), 'requested_at': requested_at}
                        except TimeoutError:
                            if key not in records:
                                raise
                            record = records[key]
                        if record.get('provider', '').startswith('FinanceDataReader:NAVER:') and captured and not captured.get('provider', '').startswith('FinanceDataReader:NAVER:'):
                            record = with_actual_prices(record, captured)
                        if record['currency'] != expected:
                            raise ValueError('UNSUPPORTED_CURRENCY')
                        record['observed_on'] = last_close(record)[1]
                        with store.connect() as db:
                            if not publish_prices(db, key, end, record, collector_lease=collector_lease) and collector_lease is not None:
                                continue
                    records[key] = record
                except Exception as error:
                    log.warning('dashboard price=%s failure=%s', key, type(error).__name__)
                    failures.append(key)
    except TimeoutError:
        log.warning('dashboard price collection deadline; retaining completed records')
        failures = sorted(set(failures) | (keys - records.keys()))
    for key, record in list(records.items()):
        if key == 'FX':
            continue
        # A missing held session may contain a split; a failed cache upgrade cannot assume none.
        if held_history_missing(key, record, (valuation_since or {}).get(key), price_end(key, day) - timedelta(days=1)):
            del records[key]
            if key not in failures:
                failures.append(key)
    return records, failures


def analysis_archive(key, payload, as_of, *, fetched_since=None):
    """The collector's stored record cut like a fetch for `as_of`. Analysis never fetches, so anything the collector
    would have published from a fetch is usable; only a recent-quotes-only or out-of-date record is not.
    `fetched_since`: a record whose own fetch started at or after this time is as current as a fetch can be,
    so it need not reach the last finished session (price_status still bounds its age)."""
    from .market_data import decode_prices, expected_sessions, positive_price, price_status
    record = decode_prices(json.dumps(payload))
    # A whole-history fetch stored as incomplete (Yahoo closes with fallback returns) is incomplete only so the
    # collector retries Yahoo; the fallback provider always returns its whole series (load_fallback_prices reads from 1970).
    if (record.get('history_complete') is not True and not record.get('whole_history')
            and not record.get('provider', '').startswith('FinanceDataReader:')):
        raise ValueError('PRICE_HISTORY_INCOMPLETE')
    frame = record['frame'].loc[record['frame'].index < pd.Timestamp(as_of)]
    closes = frame.index[frame['Close'].map(positive_price)]
    sessions = expected_sessions(key, as_of - timedelta(days=14), as_of - timedelta(days=1))
    fresh = fetched_since is not None and datetime.fromisoformat(record.get('requested_at') or '0001-01-01T00:00:00+00:00') >= fetched_since
    if not fresh and (closes.empty or not len(sessions) or closes[-1] < sessions[-1]):
        raise ValueError('PRICE_STALE')
    record = {**record, 'frame': frame}
    failure = price_status(record, as_of)
    if failure:
        raise ValueError(failure)
    return record


def saved_prices(keys, as_of, rejected=None, *, fetched_since=None):
    """The collector's stored prices usable for `as_of`. One row per query: a whole archive is 1-2MB and many
    in one statement can pass the 5-second statement limit. `rejected` receives why each other key is unusable."""
    rejected = {} if rejected is None else rejected
    records = {}
    try:
        with store.connect() as db:
            for key in keys:
                row = db.execute('SELECT payload FROM portfolio_dashboard_prices WHERE instrument=%s', (key,)).fetchone()
                if not row:
                    rejected[key] = 'PRICE_NOT_STORED'
                    continue
                try:
                    records[key] = analysis_archive(key, row['payload'], as_of, fetched_since=fetched_since)
                except Exception as error:
                    rejected[key] = str(error) if isinstance(error, ValueError) and str(error).startswith('PRICE_') else 'PRICE_RESPONSE_INVALID'
    except Exception as error:
        log.warning('saved prices unavailable code=%s', type(error).__name__)
        rejected.update({key: 'PRICE_READ_FAILED' for key in keys if key not in records and key not in rejected})
    return records


def last_close(record):
    from .market_data import positive_price
    prices = record['frame']['Close']
    prices = prices[prices.map(positive_price)]
    if prices.empty:
        raise ValueError('PRICE_UNAVAILABLE')
    return float(prices.iloc[-1]), str(prices.index[-1].date())


def forecast(asset, record, quantity, fx, day):
    from .market_data import dividend_observation
    observation = dividend_observation(record, asset['instrument_id'])
    if observation is None or quantity is None or fx is None:
        return None
    months = {}
    for event in observation['events']:
        stamp = date.fromisoformat(event['date'])
        # Provider dates are ex-dividend dates: forecast months, never fabricated payment days.
        try:
            next_date = stamp.replace(year=stamp.year + 1)
        except ValueError:
            next_date = stamp.replace(year=stamp.year + 1, day=28)
        if next_date < day:
            continue
        projected = next_date.year * 12 + next_date.month - 1
        months[projected] = months.get(projected, 0) + event['amount'] * quantity * fx
    start = day.year * 12 + day.month - 1
    return [{'asset_id': asset['id'], 'month': f'{m // 12:04d}-{m % 12 + 1:02d}', 'amount': round(amount, 2)}
            for m, amount in sorted(months.items()) if start <= m <= start + 12]


def zero_exposure(asset):
    return asset.get('quantity') == 0 or (asset.get('quantity') is None and asset.get('amount') == 0)


def dividend_details(record, instrument_id):
    from .market_data import dividend_observation
    observation = dividend_observation(record, instrument_id)
    if observation is None:
        return {'dividend_yield_pct': None, 'annual_dividend_per_share': None, 'dividend_cycle': '미확인'}
    months = sorted({event['date'][:7] for event in observation['events']})
    indices = [int(m[:4]) * 12 + int(m[5:]) for m in months]
    gaps = [b - a for a, b in zip(indices, indices[1:])]
    count = len(months)
    cycle = '최근 1년 지급 없음' if not count else '비정기'
    single_payment = len(observation['events']) == count
    if single_payment and count in (12, 13) and all(gap == 1 for gap in gaps):
        cycle = '매월'
    elif single_payment and count == 4 and all(gap == 3 for gap in gaps):
        cycle = '분기'
    elif single_payment and count == 2 and gaps == [6]:
        cycle = '반기'
    elif single_payment and count == 1:
        cycle = '연 1회'
    return {'dividend_yield_pct': observation['trailing_yield_pct'], 'annual_dividend_per_share': sum(event['amount'] for event in observation['events']), 'dividend_cycle': cycle + (' · 최근 1년' if count else '')}


def basis_dates(asset, previous, price_date):
    unchanged = previous.get('signature') == [asset.get('instrument_id'), asset.get('quantity'), asset.get('amount'), asset.get('quantity_revision')]
    baseline = previous.get('as_of', price_date) if unchanged else (asset.get('quantity_as_of') or asset.get('price_date') or price_date) if asset.get('quantity') is not None else price_date
    average_day = previous.get('average_day', baseline) if unchanged and previous.get('average') == asset.get('average') and previous.get('average_revision') == asset.get('average_revision') else asset.get('average_as_of') or asset.get('price_date') or price_date
    return baseline, average_day if asset.get('average') is not None else baseline


def valuation(workbook, records, prior, day):
    fx = last_close(records['FX'])[0] if 'FX' in records else None
    assets, forecasts, missing, dividend_missing = [], [], [], []
    pending_splits = []
    bases = (prior or {}).get('bases', {})
    next_bases = {a['id']: bases[a['id']] for a in workbook['assets'] if a['id'] in bases}
    for original in workbook['assets']:
        a = identify(original, strict=False, fx=workbook.get('fx'))
        key = a.get('instrument_id')
        if key == 'CASH:KRW':
            assets.append({'id': a['id'], 'instrument_id': key, 'value': a.get('amount'), 'profit': None, 'price': None, 'price_date': None, 'quantity': None})
            continue
        if zero_exposure(a):
            assets.append({'id': a['id'], 'instrument_id': key, 'value': 0, 'profit': -a.get('fee', 0), 'price': None, 'price_date': None,
                           'quantity': 0, 'average': a.get('average'), 'estimated_quantity': a.get('quantity') is None})
            next_bases.pop(a['id'], None)
            continue
        if key not in records or a['currency'] == 'USD' and fx is None or records[key].get('provider', '').startswith('FinanceDataReader:NAVER:'):
            missing.append(a['id'])
            dividend_missing.append(a['id'])
            continue
        record = records[key]
        if any(date.fromisoformat(stamp) <= day for stamp in record.get('pending_splits', [])):
            missing.append(a['id'])
            dividend_missing.append(a['id'])
            pending_splits.append(a['id'])
            continue
        price, price_date = last_close(record)
        rate = fx if a['currency'] == 'USD' else 1
        signature = [key, a.get('quantity'), a.get('amount'), a.get('quantity_revision')]
        previous = bases.get(a['id'], {})
        unchanged = previous.get('signature') == signature
        quantity = a.get('quantity')
        estimated = quantity is None
        if estimated:
            quantity = previous.get('quantity') if unchanged else a['amount'] / (price * rate) if a.get('amount') is not None else None
        baseline, average_day = basis_dates(a, previous, price_date)
        next_bases[a['id']] = {'signature': signature, 'quantity': quantity, 'as_of': baseline, 'average': a.get('average'), 'average_revision': a.get('average_revision'), 'average_day': average_day}
        splits = record['frame'].get('Stock Splits')
        factor = 1.
        average_factor = 1.
        if splits is not None:
            changes = splits.loc[(splits.index.date > date.fromisoformat(baseline)) & (splits.index.date <= date.fromisoformat(price_date))]
            factor = float(changes[changes > 0].prod())
            changes = splits.loc[(splits.index.date > date.fromisoformat(average_day)) & (splits.index.date <= date.fromisoformat(price_date))]
            average_factor = float(changes[changes > 0].prod())
        quantity = quantity * factor if quantity is not None else None
        average = a['average'] / average_factor if a.get('average') is not None else None
        amount = quantity * price * rate if quantity is not None else None
        profit = quantity * (price - average) * rate - a.get('fee', 0) if quantity is not None and average is not None else None
        assets.append({'id': a['id'], 'instrument_id': key, 'value': amount, 'profit': profit, 'price': price, 'price_date': price_date, 'quantity': quantity, 'average': average, 'estimated_quantity': estimated,
                       **dividend_details(record, key)})
        if amount is None:
            missing.append(a['id'])
        projected = forecast(a, record, quantity, rate, day)
        if projected is not None:
            forecasts.extend(projected)
        else:
            dividend_missing.append(a['id'])
    identities = {a['id']: a.get('instrument_id') for a in workbook['assets']}
    retained = {q['id']: q for q in (prior or {}).get('assets', []) if q['id'] in missing and q.get('value') is not None and identities.get(q['id'])
                and (q.get('instrument_id') or bases.get(q['id'], {}).get('signature', [None])[0]) == identities[q['id']]}
    assets = [q for q in assets if q['id'] not in retained] + list(retained.values())
    forecasts = [f for f in forecasts if f['asset_id'] not in retained] + [f for f in (prior or {}).get('forecasts', [])
        if f['asset_id'] in retained and (f['month'] > str(day)[:7] or prior.get('date') == str(day))]
    if fx is None and retained:
        fx = (prior or {}).get('fx')
    return {'date': str(day), 'assets': assets, 'fx': fx, 'forecasts': forecasts, 'missing': missing, 'bases': next_bases,
            'dividend_missing': dividend_missing, 'pending_splits': pending_splits}


async def work_once(stopping=None):
    if stopping and stopping.is_set():
        return False
    with store.connect() as db:
        book = db.execute("""SELECT * FROM portfolio_workbooks WHERE refresh_after<=now()
                ORDER BY refresh_after FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if book:
            book = db.execute("UPDATE portfolio_workbooks SET refresh_after=now()+interval '10 minutes' WHERE owner=%s RETURNING *", (book['owner'],)).fetchone()
    if not book:
        return False
    try:
        async with asyncio.timeout(240):
            assets = [identify(a, strict=False, fx=book['payload'].get('fx')) for a in book['payload']['assets']]
            from .dashboard_quotes import project
            with store.connect() as db:
                current = db.execute('SELECT version,refresh_after FROM portfolio_workbooks WHERE owner=%s FOR UPDATE', (book['owner'],)).fetchone()
                if current['version'] != book['version'] or current['refresh_after'] != book['refresh_after']:
                    return True
                if assets != book['payload']['assets']:
                    db.execute('UPDATE portfolio_workbooks SET payload=%s WHERE owner=%s', (Jsonb({**book['payload'], 'assets': assets}), book['owner']))
                project(db, book['owner'], book['version'], {**book['payload'], 'assets': assets}, record_day=True)
                db.execute("UPDATE portfolio_workbooks SET refresh_after=now()+interval '1 minute' WHERE owner=%s", (book['owner'],))
    except Exception as error:
        log.warning('dashboard task failed kind=%s code=%s', 'valuation', type(error).__name__)
        with store.connect() as db:
            db.execute("UPDATE portfolio_workbooks SET refresh_after=now()+interval '1 minute' WHERE owner=%s AND version=%s AND refresh_after=%s", (book['owner'], book['version'], book['refresh_after']))
    return True
