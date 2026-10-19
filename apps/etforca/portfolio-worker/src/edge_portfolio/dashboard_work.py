"""Daily dashboard valuation and queued backtests on the existing worker."""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from psycopg.types.json import Jsonb

from . import store
from .catalog import directory
from .contracts import StrictModel

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
    rows = directory()['instruments']
    key = asset.get('instrument_id')
    matches = [r for r in rows if r['id'] == key] if key else [r for r in rows
        if asset['name'].casefold() in (r['ticker'].casefold(), r['name'].casefold(), f"{r['name']} ({r['ticker']})".casefold())]
    if key and not matches:
        if strict:
            raise ValueError('SELECT_LISTED_ETF')
        asset['instrument_id'] = None
    if len(matches) == 1:
        item = matches[0]
        if asset['currency'] != ('USD' if item['market'] == 'US' else 'KRW'):
            if strict:
                raise ValueError('SELECT_LISTED_ETF')
            asset['instrument_id'] = None
            return asset
        asset.update(instrument_id=item['id'], currency='USD' if item['market'] == 'US' else 'KRW', name=f"{item['name']} ({item['ticker']})")
    return asset


class Allocation(StrictModel):
    instrument_id: str = Field(max_length=40)
    weight_pct: float = Field(gt=0, le=100, allow_inf_nan=False, strict=True)


class Backtest(StrictModel):
    allocations: list[Allocation] = Field(min_length=1, max_length=20)
    start: date | None = None
    end: date | None = None

    @model_validator(mode='after')
    def valid(self):
        allowed = {r['id'] for r in directory()['instruments']} | {'CASH:KRW'}
        if any(a.instrument_id not in allowed for a in self.allocations):
            raise ValueError('SELECT_LISTED_ETF')
        if abs(sum(a.weight_pct for a in self.allocations) - 100) > .01:
            raise ValueError('INVALID_MARKET_WEIGHTS')
        if self.end and self.end >= today() or self.start and self.start >= today() or self.start and self.end and self.start >= self.end:
            raise ValueError('INVALID_PERIOD')
        return self


def initialize(db):
    db.execute('ALTER TABLE portfolio_workbooks ADD COLUMN IF NOT EXISTS refresh_after timestamptz NOT NULL DEFAULT now()')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_dashboard_market (
        owner text PRIMARY KEY, version bigint NOT NULL, payload jsonb NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_dashboard_days (
        owner text NOT NULL, day date NOT NULL, version bigint NOT NULL, payload jsonb NOT NULL,
        PRIMARY KEY(owner,day))''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_dashboard_prices (
        instrument text PRIMARY KEY, day date NOT NULL, payload jsonb NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_backtests (
        id text PRIMARY KEY, owner text NOT NULL, input jsonb NOT NULL, state text NOT NULL DEFAULT 'queued',
        created_at timestamptz NOT NULL DEFAULT now(), lease_until timestamptz, result jsonb)''')
    db.execute('CREATE INDEX IF NOT EXISTS portfolio_backtest_owner ON portfolio_backtests(owner,created_at)')


def tracking(owner):
    with store.connect() as db:
        market = db.execute('SELECT version,payload FROM portfolio_dashboard_market WHERE owner=%s', (owner,)).fetchone()
        days = db.execute('SELECT day,payload FROM portfolio_dashboard_days WHERE owner=%s ORDER BY day', (owner,)).fetchall()
        archives = db.execute('SELECT version,payload FROM portfolio_workbook_archives WHERE owner=%s ORDER BY version DESC', (owner,)).fetchall()
    return {'market': market, 'days': [{'date': str(r['day']), **r['payload']} for r in days], 'archives': archives}


def submit(owner, body):
    payload = body.model_dump(mode='json')
    with store.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('backtest:' + owner,))
        active = db.execute("SELECT id,input FROM portfolio_backtests WHERE owner=%s AND state IN ('queued','running') ORDER BY created_at LIMIT 1", (owner,)).fetchone()
        if active:
            if active['input'] == payload:
                return {'id': active['id']}
            raise ValueError('BACKTEST_BUSY')
        count = db.execute("SELECT count(*) AS n FROM portfolio_backtests WHERE owner=%s AND created_at>now()-interval '1 day'", (owner,)).fetchone()['n']
        if count >= 50:
            raise ValueError('BACKTEST_LIMIT')
        db.execute('SELECT pg_advisory_xact_lock(1266)')
        if store.queued_count(db) >= 20:
            raise ValueError('QUEUE_FULL')
        key = str(uuid4())
        db.execute('INSERT INTO portfolio_backtests(id,owner,input) VALUES (%s,%s,%s)', (key, owner, Jsonb(payload)))
    return {'id': key}


def read_backtest(owner, key):
    with store.connect() as db:
        return db.execute('SELECT id,state,result FROM portfolio_backtests WHERE id=%s AND owner=%s', (key, owner)).fetchone()


def price_end(key, day, now=None):
    now = now or datetime.now(ZoneInfo('UTC'))
    if key == 'FX':
        return min(day, now.astimezone(ZoneInfo('UTC')).date())
    from .market_data import calendars
    korean = key.startswith('KR:')
    schedule = calendars.get_calendar('XKRX' if korean else 'XNYS', start=day - timedelta(days=30), end=day + timedelta(days=1)).schedule
    completed = schedule[(schedule['close'] <= now) & (schedule.index.date <= day if korean else schedule.index.date < day)]
    return completed.index[-1].date() + timedelta(days=1)


def held_history_missing(key, record, since, end):
    if since is None or key == 'FX':
        return False
    from .market_data import expected_sessions, positive_price
    frame = record['frame']
    return bool(len(expected_sessions(key, min(since, end), end).difference(frame.index[frame['Close'].map(positive_price)])))


async def records_for(ids, day, *, valuation_since=None):
    from .market_data import decode_prices, fetch_prices, positive_price, year_before
    from .price_fetch import serialize
    records, failures = {}, []
    keys = set(ids) - {'CASH:KRW', None}
    if any(k.startswith('US:') for k in keys):
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
                        needed_since = min(since, year_before(end - timedelta(days=1))) if since else year_before(end, 2)
                        incomplete = date.fromisoformat(saved.get('repair_since', year_before(end, 2).isoformat())) > needed_since
                        if valuation_since is not None:
                            incomplete |= not saved.get('fetched_at') or datetime.fromisoformat(saved['fetched_at']) < datetime.now(ZoneInfo('UTC')) - timedelta(minutes=1)
                        else:
                            adjusted = saved['frame']['Adj Close'].map(positive_price)
                            incomplete |= saved.get('provider', '').startswith('FinanceDataReader:NAVER:') or saved.get('price_recovery', {}).get('status') in ('unavailable', 'partial')
                            incomplete |= key != 'FX' and (saved.get('return_basis') == 'price' or adjusted.sum() < 2)
                            incomplete |= last_close(saved)[1] != str(end - timedelta(days=1))
                            incomplete |= saved.get('actions_checked_on') != str(day)
                        if incomplete:
                            if valuation_since is None:
                                records[key] = saved
                            cached = None  # Retain usable observations while retrying missing data.
                    if cached:
                        record = decode_prices(json.dumps(cached['payload']))
                    else:
                        symbol = 'KRW=X' if key == 'FX' else key[3:] + ('.KS' if key.startswith('KR:') else '')
                        expected = 'KRW' if key == 'FX' or key.startswith('KR:') else 'USD'
                        captured = None
                        def capture(record):
                            nonlocal captured
                            _, stamp = last_close(record)
                            if record['currency'] == expected and 0 <= (end - date.fromisoformat(stamp)).days <= 7:
                                captured = {**record, 'actions_checked_on': str(day), 'fetched_at': datetime.now(ZoneInfo('UTC')).isoformat()}
                                records[key] = captured
                        try:
                            budget = 200 / max(1, len(keys))
                            record = await fetch_prices(symbol, end, on_raw=capture, on_primary=capture, provider_timeout=budget * .45,
                                                        repair_since=since)
                            record = {**record, 'actions_checked_on': str(day), 'fetched_at': datetime.now(ZoneInfo('UTC')).isoformat()}
                        except TimeoutError:
                            if key not in records:
                                raise
                            record = records[key]
                        if record.get('provider', '').startswith('FinanceDataReader:NAVER:') and captured and not captured.get('provider', '').startswith('FinanceDataReader:NAVER:'):
                            frame = captured['frame'].copy()
                            frame['Adj Close'] = record['frame']['Adj Close'].reindex(frame.index)
                            record = {**captured, 'frame': frame, 'return_basis': record['return_basis'], 'return_source_url': record['source_url']}
                        if record['currency'] != expected:
                            raise ValueError('UNSUPPORTED_CURRENCY')
                        with store.connect() as db:
                            db.execute('''INSERT INTO portfolio_dashboard_prices VALUES (%s,%s,%s)
                                ON CONFLICT(instrument) DO UPDATE SET day=excluded.day,payload=excluded.payload''', (key, end, Jsonb(json.loads(serialize(record)))))
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


def run_backtest(body, records, day, failures):
    import pandas as pd
    from .market_data import comparison, comparison_bounds, weights, year_before
    day = min(day, date.fromisoformat(body['end']) + timedelta(days=1)) if body.get('end') else day
    allocation = weights(body['allocations'])
    excluded = [k for k in allocation if k != 'CASH:KRW' and (k not in records or k.startswith('US:') and 'FX' not in records or records[k].get('return_basis') == 'price')]
    for key in allocation.keys() - set(excluded) - {'CASH:KRW'}:
        try:
            last_close(records[key])
            comparison_bounds({key}, records, day, column='Adj Close')
        except (ValueError, KeyError):
            excluded.append(key)
    target = {k: v for k, v in allocation.items() if k not in excluded}
    if not target:
        raise ValueError('PRICE_UNAVAILABLE')
    total = sum(target.values())
    if set(target) == {'CASH:KRW'}:
        end = pd.Timestamp(body.get('end') or day - timedelta(days=1))
        start = pd.Timestamp(body.get('start') or year_before(end.date()))
    else:
        start, end = comparison_bounds(set(target), records, day)
        if body.get('start'):
            start = max(start, pd.Timestamp(body['start']))
        if body.get('end'):
            end = pd.Timestamp(body['end'])
    if start >= end:
        raise ValueError('NO_COMMON_PRICE_HISTORY')
    history = comparison({k: v / total for k, v in target.items()}, None, records, day, bounds=(start, end))
    points = history['points']
    if len(points) < 2:
        raise ValueError('NO_COMMON_PRICE_HISTORY')
    baseline = points[0]['recommended']
    history.update(points=[{**p, 'recommended': round(p['recommended'] / baseline * 1000000, 2)} for p in points],
                   basis=f"{points[0]['date']} ~ {points[-1]['date']} · 100만 원 · 분배금 전액 재투자")
    return {'history': history, 'excluded': excluded, 'return_pct': round((points[-1]['recommended'] / baseline - 1) * 100, 2)}


async def work_once(stopping=None):
    if stopping and stopping.is_set():
        return False
    with store.connect() as db:
        job = db.execute("""SELECT * FROM portfolio_backtests WHERE state='queued' OR state='running' AND lease_until<now()
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        book = db.execute("""SELECT * FROM portfolio_workbooks WHERE refresh_after<=now()
                ORDER BY refresh_after FOR UPDATE SKIP LOCKED LIMIT 1""").fetchone()
        if book and (not job or book['refresh_after'] <= job['created_at']):
            job = None
            book = db.execute("UPDATE portfolio_workbooks SET refresh_after=now()+interval '10 minutes' WHERE owner=%s RETURNING *", (book['owner'],)).fetchone()
        elif job:
            book = None
            job = db.execute("UPDATE portfolio_backtests SET state='running',lease_until=now()+interval '10 minutes' WHERE id=%s RETURNING *", (job['id'],)).fetchone()
    if not job and not book:
        return False
    day = today()
    try:
        async with asyncio.timeout(240):
            if job:
                collection_day = min(day, date.fromisoformat(job['input']['end']) + timedelta(days=1)) if job['input'].get('end') else day
                records, failures = await records_for([a['instrument_id'] for a in job['input']['allocations']], collection_day)
                result = run_backtest(job['input'], records, day, failures)
                with store.connect() as db:
                    db.execute("UPDATE portfolio_backtests SET state='succeeded',result=%s WHERE id=%s AND lease_until=%s", (Jsonb(result), job['id'], job['lease_until']))
            else:
                assets = [identify(a, strict=False, fx=book['payload'].get('fx')) for a in book['payload']['assets']]
                previous = tracking(book['owner'])['market']
                bases = previous['payload'].get('bases', {}) if previous else {}
                # Keep split history from every holding's basis as well as the trailing dividend year.
                since = {}
                for a in assets:
                    key = a.get('instrument_id')
                    if zero_exposure(a) or key in (None, 'CASH:KRW'):
                        continue
                    since[key] = min(since.get(key, day), *(date.fromisoformat(stamp) for stamp in basis_dates(a, bases.get(a['id'], {}), str(day))))
                records, failures = await records_for([a.get('instrument_id') for a in assets if not zero_exposure(a)], day, valuation_since=since)
                result = valuation({**book['payload'], 'assets': assets}, {key: record for key, record in records.items() if key not in failures}, previous['payload'] if previous else None, day)
                with store.connect() as db:
                    current = db.execute('SELECT version,refresh_after FROM portfolio_workbooks WHERE owner=%s FOR UPDATE', (book['owner'],)).fetchone()
                    if current['version'] != book['version'] or current['refresh_after'] != book['refresh_after']:
                        return True
                    db.execute('''INSERT INTO portfolio_dashboard_market VALUES (%s,%s,%s)
                        ON CONFLICT(owner) DO UPDATE SET version=excluded.version,payload=excluded.payload''', (book['owner'], book['version'], Jsonb(result)))
                    if assets and not result['missing'] and all(a['value'] is not None for a in result['assets']):
                        snapshot = {'value': sum(a['value'] for a in result['assets']), 'assets': result['assets'], 'fx': result['fx']}
                        db.execute('''INSERT INTO portfolio_dashboard_days VALUES (%s,%s,%s,%s)
                            ON CONFLICT(owner,day) DO UPDATE SET version=excluded.version,payload=excluded.payload''', (book['owner'], day, book['version'], Jsonb(snapshot)))
                    db.execute("UPDATE portfolio_workbooks SET refresh_after=now()+interval '1 minute' WHERE owner=%s", (book['owner'],))
    except Exception as error:
        log.warning('dashboard task failed kind=%s code=%s', 'backtest' if job else 'valuation', type(error).__name__)
        with store.connect() as db:
            if job:
                db.execute("UPDATE portfolio_backtests SET state='failed',result=%s WHERE id=%s AND lease_until=%s", (Jsonb({'message': '가격 자료를 가져오지 못했어요. 잠시 후 다시 조회해 주세요.'}), job['id'], job['lease_until']))
            else:
                db.execute("UPDATE portfolio_workbooks SET refresh_after=now()+interval '1 minute' WHERE owner=%s AND version=%s AND refresh_after=%s", (book['owner'], book['version'], book['refresh_after']))
    return True
