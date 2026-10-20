"""Shared instrument collection, independent of workbook reads and valuations."""
import asyncio
import json
import logging
from datetime import date, timedelta

from . import store

log = logging.getLogger(__name__)


def initialize(db):
    from . import market_preload
    market_preload.initialize(db)
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_quote_refresh (
        instrument text PRIMARY KEY, since date NOT NULL,
        refresh_after timestamptz NOT NULL DEFAULT now(), lease_until timestamptz)''')


def requirements(workbook, prior, day):
    from .dashboard_work import basis_dates, zero_exposure
    bases = (prior or {}).get('bases', {})
    since = {}
    for asset in workbook['assets']:
        key = asset.get('instrument_id')
        if key in (None, 'CASH:KRW') or zero_exposure(asset):
            continue
        since[key] = min(since.get(key, day), *(date.fromisoformat(stamp) for stamp in basis_dates(asset, bases.get(asset['id'], {}), str(day))))
    if any(key.startswith('US:') for key in since):
        since['FX'] = day
    return since


def enqueue(db, since):
    for key, start in sorted(since.items()):
        # Saving another holding never expires an existing instrument's schedule.
        db.execute('''INSERT INTO portfolio_quote_refresh(instrument,since) VALUES (%s,%s)
            ON CONFLICT(instrument) DO UPDATE SET
                refresh_after=CASE WHEN excluded.since<portfolio_quote_refresh.since THEN now() ELSE portfolio_quote_refresh.refresh_after END,
                since=least(portfolio_quote_refresh.since,excluded.since)''', (key, start))


def cached(db, since, day):
    from .dashboard_work import price_end, held_history_missing
    from .market_data import decode_prices
    records = {}
    if not since:
        return records
    for row in db.execute('SELECT * FROM portfolio_dashboard_prices WHERE instrument=ANY(%s)', (list(since),)).fetchall():
        key = row['instrument']
        end = price_end(key, day)
        if row['day'] != end or row['payload'].get('actions_checked_on') != str(day):
            continue
        record = decode_prices(json.dumps(row['payload']))
        if not held_history_missing(key, record, since[key], end - timedelta(days=1)):
            records[key] = record
    return records


def project(db, owner, version, workbook, *, record_day=False):
    from .dashboard_work import today, valuation
    from psycopg.types.json import Jsonb
    previous = db.execute('SELECT payload FROM portfolio_dashboard_market WHERE owner=%s', (owner,)).fetchone()
    prior = previous['payload'] if previous else None
    day = today()
    since = requirements(workbook, prior, day)
    enqueue(db, since)
    records = cached(db, since, day)
    result = valuation(workbook, records, prior, day)
    result['pending_prices'] = [a['id'] for a in workbook['assets'] if a.get('instrument_id') in since
        and (a['instrument_id'] not in records or a['currency'] == 'USD' and 'FX' not in records)]
    db.execute('''INSERT INTO portfolio_dashboard_market VALUES (%s,%s,%s)
        ON CONFLICT(owner) DO UPDATE SET version=excluded.version,payload=excluded.payload''', (owner, version, Jsonb(result)))
    if record_day and workbook['assets'] and not result['missing'] and all(a['value'] is not None for a in result['assets']):
        snapshot = {'value': sum(a['value'] for a in result['assets']), 'assets': result['assets'], 'fx': result['fx']}
        db.execute('''INSERT INTO portfolio_dashboard_days VALUES (%s,%s,%s,%s)
            ON CONFLICT(owner,day) DO UPDATE SET version=excluded.version,payload=excluded.payload''', (owner, day, version, Jsonb(snapshot)))
    return result


async def collect_once(stopping=None):
    if stopping and stopping.is_set():
        return False
    with store.connect() as db:
        job = db.execute('''UPDATE portfolio_quote_refresh SET lease_until=now()+interval '5 minutes'
            WHERE instrument=(SELECT instrument FROM portfolio_quote_refresh
                WHERE refresh_after<=now() AND (lease_until IS NULL OR lease_until<now())
                ORDER BY refresh_after-CASE WHEN EXISTS(
                    SELECT 1 FROM portfolio_dashboard_prices p WHERE p.instrument=portfolio_quote_refresh.instrument)
                    THEN interval '0 seconds' ELSE interval '1 minute' END,
                    (instrument='FX') DESC FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *''').fetchone()
        if not job:
            return False
        active = db.execute('''SELECT 1 FROM portfolio_workbooks w, jsonb_array_elements(w.payload->'assets') a
            WHERE (a->>'instrument_id'=%s OR (%s='FX' AND a->>'currency'='USD' AND a->>'instrument_id' LIKE 'US:%%'))
                AND coalesce((a->>'quantity')::numeric,(a->>'amount')::numeric) IS DISTINCT FROM 0 LIMIT 1''', (job['instrument'], job['instrument'])).fetchone()
        if not active:
            db.execute('DELETE FROM portfolio_quote_refresh WHERE instrument=%s AND lease_until=%s', (job['instrument'], job['lease_until']))
            return True
    from .dashboard_work import records_for, today
    key = job['instrument']
    try:
        if stopping and stopping.is_set():
            return True
        await records_for([key], today(), valuation_since={key: job['since']}, include_fx=False, collector_lease=job['lease_until'])
    except Exception as error:
        log.warning('shared quote failed instrument=%s code=%s', key, type(error).__name__)
    finally:
        with store.connect() as db:
            updated = db.execute('''UPDATE portfolio_quote_refresh SET lease_until=NULL,
                refresh_after=CASE WHEN since<%s THEN now() ELSE now()+interval '1 minute' END
                WHERE instrument=%s AND lease_until=%s RETURNING instrument''', (job['since'], key, job['lease_until'])).fetchone()
        if updated:
            # Release quote locks before touching workbooks: saves lock them in the reverse order.
            with store.connect() as db:
                db.execute('''UPDATE portfolio_workbooks w SET refresh_after=least(refresh_after,now())
                    WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(w.payload->'assets') a
                        WHERE a->>'instrument_id'=%s OR (%s='FX' AND a->>'currency'='USD'))''', (key, key))
    return True


async def collection_loop(stopping):
    while not stopping.is_set():
        try:
            worked = await collect_once(stopping)
        except Exception as error:
            log.warning('shared quote loop failed code=%s', type(error).__name__)
            worked = False
        await asyncio.sleep(0 if worked else 1)
