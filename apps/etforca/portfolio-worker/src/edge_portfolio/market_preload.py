"""Catalogue-wide daily quotes first, then archives, using the existing worker."""
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

    async def fetch(self, symbol, end, start):
        from . import market_data
        captured = None
        try:
            async with asyncio.timeout(30):
                if not self.admitted:
                    while not market_data._PRICE_SLOTS.acquire(blocking=False):
                        if self.stopping and self.stopping.is_set():
                            raise InterruptedError()
                        await asyncio.sleep(.05)
                    self.admitted = True
                    if self.stopping and self.stopping.is_set():
                        raise InterruptedError()
                    self.process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'edge_portfolio.price_fetch', '--serve',
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                        limit=16 * 1024 * 1024,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                if self.stopping and self.stopping.is_set():
                    raise InterruptedError()
                self.process.stdin.write((json.dumps({'symbol': symbol, 'end': str(end), 'start': str(start) if start else None}) + '\n').encode())
                await self.process.stdin.drain()
                while True:
                    record = market_data.decode_prices(await self.process.stdout.readline())
                    if record.pop('_checkpoint', False):
                        captured = record
                    else:
                        return record
        except (TimeoutError, ValueError) as error:
            await self.close()
            if captured is not None:
                captured['price_recovery'] = {'status': 'unavailable', 'reason': market_data.price_failure(error), 'dates': []}
                return captured
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


def seed():
    from .catalog import directory
    keys = sorted({r['id'] for r in directory()['instruments']} | {'FX'})
    with store.connect() as db:
        db.execute('''INSERT INTO portfolio_market_preload(instrument)
            SELECT jsonb_array_elements_text(%s::jsonb) ON CONFLICT DO NOTHING''', (json.dumps(keys),))


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


async def collect_one(stopping=None, *, now=None, session=None):
    from . import dashboard_work as work, market_data
    now = now or datetime.now(KST)
    if not in_window(now) or stopping and stopping.is_set():
        return False
    with store.connect() as db:
        if store.queued_count(db) or db.execute("SELECT 1 FROM portfolio_newsletter_content WHERE state='running' LIMIT 1").fetchone():
            return False
        if db.execute("""SELECT 1 FROM portfolio_market_preload WHERE last_error='PRICE_PROVIDER_RATE_LIMIT'
                AND failed_at>now()-interval '15 minutes' LIMIT 1""").fetchone():
            return False
        job = db.execute('''SELECT q.*,q.quote_after<=now() AS quote FROM portfolio_market_preload q
            WHERE (q.lease_until IS NULL OR q.lease_until<now())
                AND (q.quote_after<=now() OR q.history_after<=now())
            ORDER BY (q.quote_after<=now()) DESC, (q.instrument='FX') DESC,
                least(q.quote_after,q.history_after),q.instrument
            FOR UPDATE SKIP LOCKED LIMIT 1''').fetchone()
        if not job:
            return False
        job['lease_until'] = db.execute("""UPDATE portfolio_market_preload SET lease_until=now()+interval '5 minutes'
            WHERE instrument=%s RETURNING lease_until""", (job['instrument'],)).fetchone()['lease_until']
    key = job['instrument']
    error = None
    try:
        end = work.price_end(key, now.date())
        symbol = 'KRW=X' if key == 'FX' else key[3:] + ('.KS' if key.startswith('KR:') else '')
        requested_at = datetime.now(ZoneInfo('UTC')).isoformat()
        # Recent quotes are available before an instrument's long archive is collected.
        async with asyncio.timeout(40):
            record = await (session.fetch(symbol, end, end - timedelta(days=35) if job['quote'] else None) if session else
                market_data.fetch_prices(symbol, end, provider_timeout=15, history_start=end - timedelta(days=35) if job['quote'] else None))
        if record['currency'] != ('USD' if key.startswith('US:') else 'KRW'):
            raise ValueError('UNSUPPORTED_CURRENCY')
        _, observed = work.last_close(record)
        if not 0 <= (end - datetime.fromisoformat(observed).date()).days <= 7:
            raise ValueError('PRICE_STALE')
        record.update(actions_checked_on=str(now.date()), requested_at=requested_at,
                      fetched_at=datetime.now(ZoneInfo('UTC')).isoformat())
        with store.connect() as db:
            if not db.execute('SELECT 1 FROM portfolio_market_preload WHERE instrument=%s AND lease_until=%s FOR UPDATE',
                              (key, job['lease_until'])).fetchone():
                return True
            published = work.publish_prices(db, key, end, record)
            saved = db.execute("SELECT day,payload->>'actions_checked_on' AS checked,payload->>'history_complete' AS complete,payload->>'actions_complete' AS actions_complete,payload->'price_recovery'->>'status' AS recovery FROM portfolio_dashboard_prices WHERE instrument=%s", (key,)).fetchone()
            limited = 'PRICE_PROVIDER_RATE_LIMIT' in (record.get('price_recovery', {}).get('reason'), record.get('primary_failure'))
            if not published and (not saved or saved['complete'] != 'true' or saved['day'] != end or saved['checked'] != str(now.date())):
                db.execute("""UPDATE portfolio_market_preload SET lease_until=NULL,history_after=now()+interval '1 minute',
                    quote_after=greatest(quote_after,now()+interval '1 minute'),
                    last_error=%s,failed_at=CASE WHEN %s THEN now() ELSE failed_at END WHERE instrument=%s""",
                    ('PRICE_PROVIDER_RATE_LIMIT' if limited else 'PRICE_PUBLICATION_SUPERSEDED', limited, key))
                log.info('market preload instrument=%s result=superseded; archive retry scheduled', key)
                return True
            if limited:
                error = 'PRICE_PROVIDER_RATE_LIMIT'
            db.execute('''UPDATE portfolio_market_preload SET lease_until=NULL,failures=0,last_error=%s,
                failed_at=CASE WHEN %s THEN now() ELSE failed_at END,
                quote_after=%s,history_after=CASE WHEN %s THEN NULL WHEN %s THEN coalesce(history_after,now()) ELSE now()+interval '1 day' END
                WHERE instrument=%s''', (error, limited, next_quote(key, now) if job['quote'] else job['quote_after'],
                                       saved and saved['complete'] == 'true' and saved['actions_complete'] == 'true' and saved['recovery'] not in ('partial','unavailable','rejected'), job['quote'], key))
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
            db.execute('''UPDATE portfolio_market_preload SET lease_until=NULL,failures=failures+1,last_error=%s,failed_at=now(),
                quote_after=CASE WHEN %s THEN %s ELSE quote_after END,
                history_after=CASE WHEN history_after IS NOT NULL THEN greatest(history_after,%s) ELSE NULL END
                WHERE instrument=%s AND lease_until=%s''',
                (error, job['quote'], retry, retry, key, job['lease_until']))
    log.info('market preload instrument=%s stage=%s result=%s', key, 'quote' if job['quote'] else 'history', error or 'ready')
    return True


async def loop(stopping):
    seeded = None
    session = PriceSession(stopping)
    try:
        while not stopping.is_set():
            try:
                day = datetime.now(KST).date()
                if seeded != day:
                    await asyncio.to_thread(seed)
                    seeded = day
                worked = await collect_one(stopping, session=session)
            except Exception as error:
                log.warning('market preload loop failed code=%s', type(error).__name__)
                worked = False
            if not worked:
                await session.close()
            await asyncio.sleep(1 if worked else 10)
    finally:
        await session.close()
