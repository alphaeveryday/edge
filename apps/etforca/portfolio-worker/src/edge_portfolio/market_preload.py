"""Catalogue-wide daily bars (a year per instrument, once a day after its close), using the existing worker."""
import asyncio
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import store

log = logging.getLogger(__name__)
KST = ZoneInfo('Asia/Seoul')


class PriceSession:
    """Keep one admitted child warm; timeouts/cancellation always release it."""

    def __init__(self, stopping=None):
        self.process = None
        self.admitted = False
        self.stopping = stopping

    async def fetch(self, symbol, end, start, *, repair_since=None, failure=None):
        """`failure`: the primary already failed this way, so the new process runs only the fallback (15 seconds)."""
        from . import market_data
        captured = None
        if market_data._SLOT_WAITERS:
            # A newsletter quote is waiting for the slot this warm process holds: hand it over, then queue again.
            await self.close()
            while market_data._SLOT_WAITERS:
                if self.stopping and self.stopping.is_set():
                    raise InterruptedError()
                await asyncio.sleep(.05)
        try:
            # Waiting for the slot is not part of the provider deadline: a busy worker is not a bad source.
            if not self.admitted:
                while not market_data._PRICE_SLOTS.acquire(blocking=False):
                    if self.stopping and self.stopping.is_set():
                        raise InterruptedError()
                    await asyncio.sleep(.05)
                self.admitted = True
            async with asyncio.timeout(15 if failure else 30) as deadline:
                if self.process is None:
                    if self.stopping and self.stopping.is_set():
                        raise InterruptedError()
                    self.process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'edge_portfolio.price_fetch',
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        limit=16 * 1024 * 1024,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                if self.stopping and self.stopping.is_set():
                    raise InterruptedError()
                self.process.stdin.write((json.dumps({'symbol': symbol, 'end': str(end), 'start': str(start) if start else None,
                                                      'repair_since': str(repair_since) if repair_since else None,
                                                      'failure': failure}) + '\n').encode())
                await self.process.stdin.drain()
                while True:
                    line = await self.process.stdout.readline()
                    if line.startswith(b'{"_fallback"'):
                        # The primary failed; the fallback gets its own 15 seconds however late that was.
                        failure = json.loads(line)['_fallback']
                        deadline.reschedule(asyncio.get_running_loop().time() + 15)
                        continue
                    record = market_data.decode_prices(line)
                    if record.pop('_checkpoint', False):
                        captured = record
                    else:
                        return record
        except (TimeoutError, ValueError) as error:
            await self.close()
            if captured is not None:
                captured['price_recovery'] = {'status': 'unavailable', 'reason': market_data.price_failure(error), 'dates': []}
                if failure:
                    captured['primary_failure'] = failure  # A rate limit still pauses the bulk collector.
                return captured
            if isinstance(error, TimeoutError) and failure is None:
                # The primary stalled before any usable closes: the fallback gets its own time.
                return await self.fetch(symbol, end, start, repair_since=repair_since, failure='PRICE_PROVIDER_TIMEOUT')
            if failure == 'PRICE_PROVIDER_RATE_LIMIT':
                raise ValueError(failure) from error  # However the fallback failed, the rate limit stays the reason.
            raise
        except BaseException:
            await self.close()
            raise

    async def close(self):
        from . import market_data
        try:
            if self.process is not None and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        finally:
            self.process = None
            if self.admitted:
                market_data._PRICE_SLOTS.release()
                self.admitted = False


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS portfolio_market_preload (
        instrument text PRIMARY KEY, quote_after timestamptz NOT NULL DEFAULT now(),
        history_after timestamptz DEFAULT now(), lease_until timestamptz,
        failures integer NOT NULL DEFAULT 0, last_error text, failed_at timestamptz)''')
    # An analysis waiting for an instrument the store lacks puts it ahead of every scheduled refresh.
    db.execute('ALTER TABLE portfolio_market_preload ADD COLUMN IF NOT EXISTS urgent_at timestamptz')
    # When the last finished whole-history fetch started: only requests made before that start are answered by it.
    db.execute('ALTER TABLE portfolio_market_preload ADD COLUMN IF NOT EXISTS served_at timestamptz')


def seed():
    from .catalog import directory
    keys = sorted({r['id'] for r in directory(include_stocks=True)['instruments']} | {'FX'})
    with store.connect() as db:
        db.execute('''INSERT INTO portfolio_market_preload(instrument)
            SELECT jsonb_array_elements_text(%s::jsonb) ON CONFLICT DO NOTHING''', (json.dumps(keys),))


def request_prices(keys):
    """Ask the collector for these instruments first, in this order. Returns the request time.
    urgent_at keeps the latest request, so one made while a fetch runs survives that fetch and is fetched again."""
    with store.connect() as db:
        requested = db.execute('SELECT now() AS at').fetchone()['at']
        for key in keys:
            db.execute('''INSERT INTO portfolio_market_preload(instrument,urgent_at) VALUES (%s,clock_timestamp())
                ON CONFLICT(instrument) DO UPDATE SET urgent_at=greatest(portfolio_market_preload.urgent_at,excluded.urgent_at)''', (key,))
    return requested


def finished_requests(requested):
    """{key: failure code or None} for instruments whose whole-history fetch started after their own request time
    ({key: time}) and has finished.
    Judged by served_at, not urgent_at, so another analysis requesting the same instrument cannot hide the answer.
    A provider cool-down fails all at once."""
    with store.connect() as db:
        if db.execute("""SELECT 1 FROM portfolio_market_preload WHERE last_error='PRICE_PROVIDER_RATE_LIMIT'
                AND failed_at>now()-interval '15 minutes' LIMIT 1""").fetchone():
            return {key: 'PRICE_PROVIDER_RATE_LIMIT' for key in requested}
        rows = db.execute('''SELECT p.instrument,CASE WHEN p.failed_at>=p.served_at THEN p.last_error END AS failure
            FROM portfolio_market_preload p JOIN unnest(%s::text[],%s::timestamptz[]) r(instrument,at) USING(instrument)
            WHERE p.served_at>=r.at''', (list(requested), list(requested.values()))).fetchall()
    return {row['instrument']: row['failure'] for row in rows}


async def stored_prices(keys, as_of, records, failures, *, poll=1.0):
    """Analysis prices come only from the collector's store. Missing instruments go to the front of the collector's
    queue and are awaited; the caller's deadline bounds the wait and leaves PRICE_QUEUE_TIMEOUT behind.
    Each instrument is read on its own so a deadline keeps every price already read."""
    from .dashboard_work import saved_prices
    from .market_data import PRICE_FAILURES
    rejected = {}
    async def read(key, fetched_since=None):
        found = await asyncio.to_thread(saved_prices, [key], as_of, rejected, fetched_since=fetched_since)
        if key in found:
            records[key] = found[key]
            failures.pop(key, None)
        return key in found
    requested = {}
    for key in keys:
        if key in records or await read(key):
            continue
        # Requested as soon as it is found missing, so later reads never delay the collector.
        failures[key] = 'PRICE_QUEUE_TIMEOUT'
        try:
            at = await asyncio.to_thread(request_prices, [key])
        except Exception as error:
            log.warning('price request unavailable code=%s', type(error).__name__)
            failures[key] = rejected.get(key, 'PRICE_READ_FAILED')
            continue
        requested[key] = at
    while requested:
        await asyncio.sleep(poll)
        try:
            done = await asyncio.to_thread(finished_requests, requested)
        except Exception as error:
            log.warning('price request status unavailable code=%s', type(error).__name__)
            failures.update({key: 'PRICE_READ_FAILED' for key in requested})
            return
        for key, failure in done.items():
            # A record fetched after this request is as current as the provider allows.
            if not await read(key, fetched_since=requested[key]):
                failures[key] = (failure if failure in PRICE_FAILURES else None) or rejected.get(key, 'PRICE_UNAVAILABLE')
            del requested[key]


def in_window(now):
    # News 00:10/08:10, dividends 03:00, outlook/newsletter 15:00/15:30,
    # disclosures 19:30. Stop admitting background work before those slots.
    minute = now.astimezone(KST).hour * 60 + now.astimezone(KST).minute
    return any(start <= minute < end for start, end in ((420, 470), (570, 870), (1020, 1140), (1260, 1380)))


def next_quote(key, now):
    local = now.astimezone(KST)
    target = local.replace(hour=17 if key.startswith('KR:') else 9 if key == 'FX' else 7,
                           minute=30 if key == 'FX' else 0, second=0, microsecond=0)
    return target if target > local else target + timedelta(days=1)


async def collect_one(stopping=None, *, session, now=None, urgent=False):
    """One scheduled instrument, or with `urgent` one an analysis waits for (any hour, even beside customer work).
    Every fetch is the instrument's whole stored window (dashboard_work.price_window), once a day after its close."""
    from . import dashboard_work as work, market_data
    now = now or datetime.now(KST)
    if not urgent and not in_window(now) or stopping and stopping.is_set():
        return False
    with store.connect() as db:
        if not urgent and (store.queued_count(db) or db.execute("SELECT 1 FROM portfolio_newsletter_content WHERE state='running' LIMIT 1").fetchone()):
            return False
        # The shared scheduler admits bounded preload turns even with customer work due.
        if not urgent and db.execute("SELECT 1 FROM portfolio_quote_refresh WHERE lease_until>now() LIMIT 1").fetchone():
            return False
        if db.execute("""SELECT 1 FROM portfolio_market_preload WHERE last_error='PRICE_PROVIDER_RATE_LIMIT'
                AND failed_at>now()-interval '15 minutes' LIMIT 1""").fetchone():
            return False
        job = db.execute(f'''SELECT q.*,r.since FROM portfolio_market_preload q LEFT JOIN portfolio_quote_refresh r USING(instrument)
            WHERE (q.lease_until IS NULL OR q.lease_until<now()) AND {"q.urgent_at IS NOT NULL" if urgent else "q.quote_after<=now()"}
            ORDER BY {"q.urgent_at" if urgent else "(q.instrument='FX') DESC,q.quote_after,q.instrument"}
            FOR UPDATE OF q SKIP LOCKED LIMIT 1''').fetchone()
        if not job:
            return False
        job['lease_until'] = db.execute("""UPDATE portfolio_market_preload SET lease_until=now()+interval '5 minutes'
            WHERE instrument=%s RETURNING lease_until""", (job['instrument'],)).fetchone()['lease_until']
    key = job['instrument']
    error = None
    started = datetime.now(ZoneInfo('UTC'))  # The fetch start: served_at, and the record's requested_at.
    try:
        end = work.price_end(key, now.date())
        from .catalog import price_symbol
        symbol = price_symbol(key)
        # No outer deadline: the session bounds the fetch itself (Yahoo 30s, then the fallback 15s), and an outer one
        # would also count the slot wait for newsletter quotes and replace a known rate limit with a timeout.
        record = await session.fetch(symbol, end, work.price_window(end, job['since']), repair_since=job['since'])
        if record['currency'] != ('USD' if key.startswith('US:') else 'KRW'):
            raise ValueError('UNSUPPORTED_CURRENCY')
        _, observed = work.last_close(record)
        if not 0 <= (end - datetime.fromisoformat(observed).date()).days <= 7:
            raise ValueError('PRICE_STALE')
        record.update(actions_checked_on=str(now.date()), requested_at=started.isoformat(),
                      fetched_at=datetime.now(ZoneInfo('UTC')).isoformat())
        limited = 'PRICE_PROVIDER_RATE_LIMIT' in (record.get('price_recovery', {}).get('reason'), record.get('primary_failure'))
        error = 'PRICE_PROVIDER_RATE_LIMIT' if limited else None
        with store.connect() as db:
            if not db.execute('SELECT 1 FROM portfolio_market_preload WHERE instrument=%s AND lease_until=%s FOR UPDATE',
                              (key, job['lease_until'])).fetchone():
                return True
            # A rejected write is fine only when a record for this session is stored (a later fetch won); otherwise
            # (e.g. a fallback that may not replace Yahoo's closes) the stored record is stale and the fetch is retried.
            if not work.publish_prices(db, key, end, record) and not db.execute(
                    'SELECT 1 FROM portfolio_dashboard_prices WHERE instrument=%s AND day=%s', (key, end)).fetchone():
                raise ValueError(error or 'PRICE_STALE')  # A rate limit stays the reason, pausing the bulk source.
            db.execute('''UPDATE portfolio_market_preload SET lease_until=NULL,urgent_at=CASE WHEN urgent_at>%s THEN urgent_at END,
                served_at=%s,failures=0,last_error=%s,failed_at=CASE WHEN %s THEN now() ELSE failed_at END,quote_after=%s
                WHERE instrument=%s''', (started, started, error, limited, next_quote(key, now), key))
        # Quotes are committed before workbook locks, matching the interactive collector.
        with store.connect() as db:
            db.execute('''UPDATE portfolio_workbooks w SET refresh_after=least(refresh_after,now())
                WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(w.payload->'assets') a
                    WHERE a->>'instrument_id'=%s OR (%s='FX' AND a->>'currency'='USD'))''', (key, key))
    except InterruptedError:
        with store.connect() as db:
            db.execute('UPDATE portfolio_market_preload SET lease_until=NULL WHERE instrument=%s AND lease_until=%s',
                       (key, job['lease_until']))
        return False
    except Exception as failure:
        error = market_data.price_failure(failure)
        with store.connect() as db:
            retry = now + timedelta(minutes=min(360, 15 * 2 ** min(job['failures'], 5)))
            db.execute('''UPDATE portfolio_market_preload SET lease_until=NULL,urgent_at=CASE WHEN urgent_at>%s THEN urgent_at END,
                served_at=%s,failures=failures+1,last_error=%s,failed_at=now(),quote_after=%s
                WHERE instrument=%s AND lease_until=%s''', (started, started, error, retry, key, job['lease_until']))
    log.info('market preload instrument=%s result=%s', key, error or 'ready')
    return True
