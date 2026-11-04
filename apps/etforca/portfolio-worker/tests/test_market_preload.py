import asyncio
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from edge_portfolio.market_preload import PriceSession
from edge_portfolio import dashboard, dashboard_work as work, market_data, market_preload as preload, store
from test_dashboard_work import asset, record, serve
from test_store import isolated_db  # noqa: F401

NOW = datetime.fromisoformat('2026-10-20T17:00:00+09:00')


@pytest.mark.parametrize('hour,minute,expected', [(0, 10, False), (3, 0, False), (8, 10, False),
    (15, 0, False), (15, 30, False), (19, 30, False), (7, 0, True), (9, 30, True), (17, 0, True), (21, 0, True)])
def test_bulk_collection_does_not_start_in_existing_batch_slots(hour, minute, expected):
    assert preload.in_window(NOW.replace(hour=hour, minute=minute)) is expected


def test_catalog_seeding_is_shared_and_does_not_reset_schedules(isolated_db, monkeypatch):
    from edge_portfolio import catalog
    monkeypatch.setattr(catalog, 'directory', lambda **kwargs: {'instruments': [{'id': 'US:SOXL'}, {'id': 'KR:069500'}]})
    preload.seed()
    with store.connect() as db:
        db.execute("UPDATE portfolio_market_preload SET quote_after=now()+interval '1 day'")
        before = db.execute('SELECT * FROM portfolio_market_preload ORDER BY instrument').fetchall()
    preload.seed()
    with store.connect() as db:
        assert db.execute('SELECT * FROM portfolio_market_preload ORDER BY instrument').fetchall() == before
    assert len(before) == 3


async def test_quotes_precede_archives_and_new_customer_uses_preloaded_quote(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('KR:069500'),('US:SOXL')")
    calls = []
    # The next KR quote is due at 17:00 the day after `now`, and the second pass compares it with the database clock.
    # A fixed past date makes that quote due again once the real clock passes it, so anchor `now` to today.
    now = datetime.now(preload.KST).replace(hour=17, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(work, 'today', lambda: now.date())
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    async def load(symbol, end, **kwargs):
        calls.append((symbol, kwargs['history_start']))
        return {**record(currency='USD' if symbol == 'SOXL' else 'KRW'),
                'history_complete': kwargs['history_start'] is None, 'repair_since': '2026-09-01'}
    serve(monkeypatch, load)
    assert await preload.collect_one(session=preload.PriceSession(), now=now)
    dashboard.save('new', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    assert work.tracking('new')['market']['payload']['assets'][0]['value'] == 1100
    with store.connect() as db:
        db.execute("UPDATE portfolio_quote_refresh SET refresh_after=now()+interval '1 minute'")
    assert await preload.collect_one(session=preload.PriceSession(), now=now)
    assert [c[0] for c in calls] == ['069500.KS', 'SOXL']
    assert all(c[1] is not None for c in calls), 'No archive may delay catalogue price coverage'


async def test_preload_yields_to_running_customer_quotes(isolated_db):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")
        db.execute("""INSERT INTO portfolio_quote_refresh(instrument,since,refresh_after,lease_until)
            VALUES ('US:VOO',current_date,now()+interval '1 minute',now()+interval '5 minutes')""")
    assert not await preload.collect_one(session=preload.PriceSession(), now=NOW), 'A warm background child must not starve a customer price request'
    with store.connect() as db:
        assert db.execute("SELECT lease_until FROM portfolio_market_preload").fetchone()['lease_until'] is None


async def test_scheduled_preload_turn_runs_with_customer_refresh_due(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('KR:069500')")
        db.execute("INSERT INTO portfolio_quote_refresh(instrument,since) VALUES ('US:VOO',current_date)")
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    async def load(*args, **kwargs):
        return record()
    serve(monkeypatch, load)
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW)
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM portfolio_dashboard_prices WHERE instrument='KR:069500'").fetchone()
        assert db.execute("SELECT 1 FROM portfolio_quote_refresh WHERE refresh_after<=now()").fetchone()


async def test_preload_yields_to_customer_analysis_and_source_errors_back_off(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")
    job = store.submit('customer', 'new', {})
    assert not await preload.collect_one(session=preload.PriceSession(), now=NOW)
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET state='failed' WHERE id=%s", (job['id'],))
    async def fail(*args, **kwargs):
        raise ValueError('PRICE_PROVIDER_RATE_LIMIT')
    serve(monkeypatch, fail)
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW)
    with store.connect() as db:
        row = db.execute("SELECT * FROM portfolio_market_preload WHERE instrument='US:SOXL'").fetchone()
    assert row['failures'] == 1 and row['last_error'] == 'PRICE_PROVIDER_RATE_LIMIT'
    assert row['quote_after'] == NOW + timedelta(minutes=15) and row['lease_until'] is None
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:VOO')")
    assert not await preload.collect_one(session=preload.PriceSession(), now=NOW), 'A source-wide rate limit must pause other symbols too'


def test_loader_requests_only_the_window_and_records_where_it_started(monkeypatch):
    # Analysis treats a record whose first bar comes well after this start as complete from the listing.
    calls = []
    class Ticker:
        history_metadata = {'currency': 'USD'}
        def history(self, **kwargs):
            calls.append(kwargs)
            return record(currency='USD')['frame']
    monkeypatch.setattr(market_data.yf, 'Ticker', lambda _: Ticker())
    monkeypatch.setattr(market_data, 'repair_prices', lambda ticker, frame, *args, **kwargs: (frame, {}))
    recent = market_data.load_prices('SOXL', date(2026, 10, 17), history_start=date(2026, 9, 12))
    archive = market_data.load_prices('SOXL', date(2026, 10, 17))
    assert calls[0]['start'] == '2026-09-12' and 'period' not in calls[0]
    assert calls[1]['period'] == 'max'
    assert recent['history_start'] == '2026-09-12' and archive['history_start'] is None


async def test_new_customer_symbol_precedes_existing_quote_refreshes(isolated_db, monkeypatch):
    from edge_portfolio import dashboard_quotes as quotes
    calls = []
    async def fetch(ids, *args, **kwargs):
        calls.extend(ids)
    monkeypatch.setattr(work, 'records_for', fetch)
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[
        asset(), {**asset(), 'id': 'b', 'instrument_id': 'US:SOXL', 'currency': 'USD'}])))
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), {**record(), 'requested_at': '2026-10-18'})
        db.execute("UPDATE portfolio_quote_refresh SET refresh_after=now()-interval '30 seconds' WHERE instrument='KR:069500'")
    await quotes.collect_once()
    await quotes.collect_once()
    assert calls == ['FX', 'US:SOXL'], 'Missing conversion and new prices precede refreshes that already have values'


async def test_warm_provider_reuses_child_but_timeout_kills_it_and_returns_capacity(monkeypatch):
    import asyncio
    import json
    import sys
    from edge_portfolio.price_fetch import serialize
    payload = serialize(record())
    code = ("import sys,json,time\nfor line in sys.stdin:\n"
            " request=json.loads(line)\n"
            " if request['symbol']=='HANG':time.sleep(60)\n"
            f" print({payload!r},flush=True)\n")
    original = asyncio.create_subprocess_exec
    calls = []
    async def spawn(*args, **kwargs):
        calls.append(args)
        return await original(sys.executable, '-u', '-c', code, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    session = preload.PriceSession()
    try:
        for symbol in ['SOXL', 'VOO']:
            await session.fetch(symbol, date(2026, 10, 20), None)
        assert len(calls) == 1
        child = session.process
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(.2):
                await session.fetch('HANG', date(2026, 10, 20), None)
        assert child.returncode is not None and session.process is None and not session.admitted
        assert market_data._PRICE_SLOTS.acquire(blocking=False)
        assert not market_data._PRICE_SLOTS.acquire(blocking=False)
        market_data._PRICE_SLOTS.release()
    finally:
        await session.close()


async def test_warm_provider_retains_raw_quote_when_repair_fails(monkeypatch):
    import asyncio
    import sys
    from edge_portfolio.price_fetch import serialize
    payload = serialize({**record(), '_checkpoint': True})
    code = ("import sys\nsys.stdin.readline()\n"
            f"print({payload!r},flush=True)\n"
            "print('{\"error\":\"PRICE_PROVIDER_RATE_LIMIT\"}',flush=True)\n")
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        return await original(sys.executable, '-u', '-c', code, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    session = preload.PriceSession()
    try:
        saved = await session.fetch('SOXL', date(2026, 10, 20), None)
        assert len(saved['frame']) == 2
        assert saved['price_recovery']['reason'] == 'PRICE_PROVIDER_RATE_LIMIT'
        assert session.process is None and not session.admitted
    finally:
        await session.close()


async def test_usable_quote_with_rate_limited_repair_still_pauses_bulk_source(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL'),('US:VOO')")
    async def load(*args, **kwargs):
        return {**record(currency='USD'), 'history_complete': False,
                'price_recovery': {'status': 'unavailable', 'reason': 'PRICE_PROVIDER_RATE_LIMIT'}}
    serve(monkeypatch, load)
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW)
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM portfolio_dashboard_prices WHERE instrument='US:SOXL'").fetchone()
    assert not await preload.collect_one(session=preload.PriceSession(), now=NOW)


@pytest.mark.parametrize('limited', [True, False])
async def test_a_later_stored_fetch_answers_the_request(isolated_db, monkeypatch, limited):
    # A write rejected because a later fetch already stored this session is no failure: that record is as current.
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")
        work.publish_prices(db, 'US:SOXL', date(2026, 10, 17), {**record(currency='USD'), 'requested_at': '2099-01-01'})
    async def load(*args, **kwargs):
        return {**record(currency='USD'), 'price_recovery': {'reason': 'PRICE_PROVIDER_RATE_LIMIT' if limited else ''}}
    serve(monkeypatch, load)
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW)
    with store.connect() as db:
        row = db.execute("SELECT * FROM portfolio_market_preload").fetchone()
        stored = db.execute("SELECT payload->>'requested_at' AS r FROM portfolio_dashboard_prices").fetchone()['r']
    assert stored == '2099-01-01' and row['failures'] == 0 and row['lease_until'] is None
    assert row['last_error'] == ('PRICE_PROVIDER_RATE_LIMIT' if limited else None), 'A rate limit still pauses the bulk source'
    assert row['quote_after'] == preload.next_quote('US:SOXL', NOW), 'Next fetched after the next close'
    if limited:
        with store.connect() as db:
            db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:VOO')")
        assert not await preload.collect_one(session=preload.PriceSession(), now=NOW)


def test_fx_preload_runs_after_utc_day_advances():
    assert preload.next_quote('FX', NOW.replace(hour=7)) == NOW.replace(hour=9, minute=30)


async def test_valuation_reuses_recent_fx_without_demanding_two_year_archive(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    with store.connect() as db:
        work.publish_prices(db, 'FX', date(2026, 10, 17), {**record(), 'history_complete': False,
            'repair_since': '2026-09-12', 'actions_checked_on': str(NOW.date()),
            'requested_at': NOW.isoformat(), 'fetched_at': datetime.now(preload.KST).isoformat()})
    async def fail(*args, **kwargs):
        pytest.fail('Fresh FX quote must not request a full archive')
    serve(monkeypatch, fail)
    records, failures = await work.records_for(['FX'], NOW.date(), valuation_since={}, session=PriceSession())
    assert 'FX' in records and not failures


async def test_preload_rejects_stale_positive_fx_despite_recent_empty_rows(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 20))
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('FX')")
    class Session:
        async def fetch(self, *args, **kwargs):
            result = record(prices=(1300., float('nan')))
            result['frame'].index = pd.to_datetime(['2026-10-01', '2026-10-19'])
            return result
    await preload.collect_one(now=NOW, session=Session())
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM portfolio_dashboard_prices").fetchone()
        assert db.execute("SELECT last_error FROM portfolio_market_preload").fetchone()['last_error'] == 'PRICE_STALE'


async def test_shutdown_while_waiting_does_not_start_provider_and_releases_claim(isolated_db, monkeypatch):
    import asyncio
    import threading
    stopping = threading.Event()
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")
    async def forbidden(*args, **kwargs):
        pytest.fail('Shutdown must not start another provider process')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', forbidden)
    assert market_data._PRICE_SLOTS.acquire(blocking=False)
    try:
        task = asyncio.create_task(preload.collect_one(stopping, now=NOW, session=preload.PriceSession(stopping)))
        await asyncio.sleep(.1)
        stopping.set()
        assert not await task
    finally:
        market_data._PRICE_SLOTS.release()
    with store.connect() as db:
        row = db.execute('SELECT * FROM portfolio_market_preload').fetchone()
    assert row['lease_until'] is None and row['failures'] == 0


async def test_slow_admission_does_not_lose_raw_prices_at_collector_deadline(isolated_db, monkeypatch):
    import asyncio
    import sys
    from edge_portfolio.price_fetch import serialize
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")
    payload = serialize({**record(currency='USD'), '_checkpoint': True, 'history_complete': False})
    code = f"import sys,time\nsys.stdin.readline()\nprint({payload!r},flush=True)\ntime.sleep(60)\n"
    original_spawn, original_timeout = asyncio.create_subprocess_exec, asyncio.timeout
    async def spawn(*args, **kwargs):
        await asyncio.sleep(.3)
        return await original_spawn(sys.executable, '-u', '-c', code, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout({30: .8}.get(seconds, seconds)))
    session = preload.PriceSession()
    try:
        assert await preload.collect_one(now=NOW, session=session)
        with store.connect() as db:
            saved = db.execute("SELECT payload FROM portfolio_dashboard_prices").fetchone()['payload']
            assert db.execute('SELECT failures FROM portfolio_market_preload').fetchone()['failures'] == 0
        assert saved['frame'][-1]['Close'] == 110
        assert saved['price_recovery']['status'] == 'unavailable'
        assert session.process is None and not session.admitted
    finally:
        await session.close()


@pytest.mark.parametrize('failure', ['PRICE_PROVIDER_RATE_LIMIT', 'PRICE_UNAVAILABLE', 'stale', 'invalid'])
def test_production_warm_loader_uses_existing_fallback_for_primary_failure(monkeypatch, failure):
    from edge_portfolio import price_fetch, data_providers
    def primary(*args, **kwargs):
        if failure.startswith('PRICE'):
            raise ValueError(failure)
        result = record()
        if failure == 'stale':
            result['frame'].index = pd.to_datetime(['2020-01-01', '2020-01-02'])
        else:
            result['frame']['Close'] = float('nan')
        return result
    calls = []
    def fallback(symbol, end):
        calls.append((symbol, end))
        return {**record(), 'provider': 'existing-fallback'}
    monkeypatch.setattr(price_fetch, 'load_prices', primary)
    monkeypatch.setattr(data_providers, 'load_fallback_prices', fallback)
    result = price_fetch.load_preload_prices('KRW=X', date(2026, 10, 17), date(2026, 9, 12), lambda _: None)
    assert calls == [('KRW=X', date(2026, 10, 17))]
    assert result['provider'] == 'existing-fallback' and result['history_start'] == '2026-09-12'
    assert result['primary_failure'] == {'stale': 'PRICE_STALE', 'invalid': 'PRICE_INVALID'}.get(failure, failure)


def test_fallback_series_from_1970_is_cut_to_the_requested_window(monkeypatch):
    # The fallback returns every bar since 1970; storing that would bring back the whole-archive memory cost.
    from edge_portfolio import price_fetch, data_providers
    monkeypatch.setattr(price_fetch, 'load_prices', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('PRICE_UNAVAILABLE')))
    long = record()
    long['frame'] = pd.concat([pd.DataFrame({'Close': 1., 'Adj Close': 1., 'Dividends': 0.}, index=pd.to_datetime(['1970-01-02'])), long['frame']])
    monkeypatch.setattr(data_providers, 'load_fallback_prices', lambda symbol, end: {**long, 'provider': 'existing-fallback'})
    result = price_fetch.load_preload_prices('KRW=X', date(2026, 10, 17), date(2026, 9, 12), lambda _: None)
    assert [str(day.date()) for day in result['frame'].index] == ['2026-10-15', '2026-10-16']


@pytest.mark.parametrize('symbol,currency,provider', [('069500.KS', 'KRW', 'FinanceDataReader:NAVER:069500'), ('247540.KQ', 'KRW', 'FinanceDataReader:NAVER:247540'), ('SOXL', 'USD', 'FinanceDataReader:YAHOO:SOXL')])
def test_warm_fallback_preserves_primary_actual_prices_actions_and_split_warning(monkeypatch, symbol, currency, provider):
    from edge_portfolio import price_fetch, data_providers
    primary = {**record(currency=currency), 'pending_splits': ['2026-10-17']}
    primary['frame']['Adj Close'] = float('nan')
    primary['frame']['Stock Splits'] = [2., 0.]
    def load(*args, **kwargs):
        kwargs['on_raw'](primary)
        return primary
    fallback = {**record(prices=(90., 99.), currency=currency), 'provider': provider,
                'return_basis': 'total_return', 'source_url': 'https://example.com/fallback'}
    monkeypatch.setattr(price_fetch, 'load_prices', load)
    monkeypatch.setattr(data_providers, 'load_fallback_prices', lambda *args: fallback)
    saved = price_fetch.load_preload_prices(symbol, date(2026, 10, 17), date(2026, 9, 12), lambda _: None)
    assert list(saved['frame']['Close']) == [100., 110.]
    assert list(saved['frame']['Adj Close']) == [90., 99.]
    assert saved['frame']['Stock Splits'].iloc[0] == 2
    assert saved['frame']['Dividends'].iloc[1] == 2
    assert saved['pending_splits'] == ['2026-10-17']
    assert saved['return_source_url'] == fallback['source_url']


async def test_failed_missing_symbols_cannot_starve_older_cached_refreshes(isolated_db, monkeypatch):
    from edge_portfolio import dashboard_quotes as quotes
    calls = []
    async def collect(ids, *args, **kwargs):
        calls.extend(ids)
    monkeypatch.setattr(work, 'records_for', collect)
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[
        asset(), {**asset(), 'id': 'b', 'instrument_id': 'US:SOXL', 'currency': 'USD'}])))
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), {**record(), 'requested_at': '2026-10-18'})
        db.execute("UPDATE portfolio_quote_refresh SET refresh_after=now()-interval '3 minutes' WHERE instrument='KR:069500'")
    await quotes.collect_once()
    assert calls == ['KR:069500'], 'One refresh interval of priority cannot indefinitely override overdue work'


@pytest.mark.parametrize('limited', [True, False])
async def test_a_rejected_write_that_leaves_an_old_session_is_retried(isolated_db, monkeypatch, limited):
    # The stored record is for an older session and this fetch may not replace it (older closes): retry soon, never
    # wait for tomorrow's close with a stale record.
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 20))
    old = {**record(), 'requested_at': '2026-10-17', 'actions_checked_on': '2026-10-17'}
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('KR:069500')")
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
    async def load(*args, **kwargs):
        result = record()
        result['frame'].index = pd.to_datetime(['2026-10-14', '2026-10-15'])
        return {**result, **({'primary_failure': 'PRICE_PROVIDER_RATE_LIMIT'} if limited else {})}
    serve(monkeypatch, load)
    await preload.collect_one(session=preload.PriceSession(), now=NOW)
    with store.connect() as db:
        row = db.execute("SELECT last_error,quote_after FROM portfolio_market_preload").fetchone()
    assert row['last_error'] == ('PRICE_PROVIDER_RATE_LIMIT' if limited else 'PRICE_STALE'), 'A rate limit must still pause the bulk source'
    assert row['quote_after'] == NOW + timedelta(minutes=15)


async def test_analysis_waits_only_for_what_the_store_lacks_and_reports_the_collectors_failure(monkeypatch):
    # The analysis never calls a provider: stored prices answer at once, only missing ones are
    # requested from the collector (in order) and awaited, and a collector failure is that instrument's code.
    stored = {'KR:A': record()}
    reads, requested = [], []
    polls = iter([{}, {'KR:B': None}, {'KR:C': 'PRICE_STALE', 'KR:D': 'PRICE_PUBLICATION_SUPERSEDED'}])
    def saved_prices(keys, as_of, rejected=None, fetched_since=None):
        reads.append((keys, fetched_since))
        rejected.update({key: 'PRICE_HISTORY_INCOMPLETE' for key in keys if key not in stored})
        return {key: stored[key] for key in keys if key in stored}
    def request(keys):
        assert reads[-1][0] == keys, 'A missing instrument is requested before the next one is read'
        requested.extend(keys)
        stored['KR:B'] = record()  # The collector will publish B.
        return 'at:' + keys[0]
    monkeypatch.setattr(work, 'saved_prices', saved_prices)
    monkeypatch.setattr(preload, 'request_prices', request)
    monkeypatch.setattr(preload, 'finished_requests', lambda requested: next(polls))
    monkeypatch.setattr(preload.PriceSession, 'fetch', lambda *a, **k: pytest.fail('analysis must not call a provider'))
    records, failures = {}, {'KR:A': 'PRICE_QUEUE_TIMEOUT'}
    await preload.stored_prices(['KR:A', 'KR:B', 'KR:C', 'KR:D'], date(2026, 10, 12), records, failures, poll=0)
    assert requested == ['KR:B', 'KR:C', 'KR:D']
    assert set(records) == {'KR:A', 'KR:B'}
    assert failures == {'KR:C': 'PRICE_STALE', 'KR:D': 'PRICE_HISTORY_INCOMPLETE'}, \
        'A collector code outside the price failures falls back to why the stored record is unusable'
    assert (['KR:B'], 'at:KR:B') in reads and (['KR:C'], 'at:KR:C') in reads, 'Each instrument is judged against its own request time'


async def test_a_deadline_keeps_every_price_already_read(monkeypatch):
    def saved_prices(keys, as_of, rejected=None, fetched_since=None):
        if keys == ['KR:B']:
            import time
            time.sleep(.2)
        return {key: record() for key in keys}
    monkeypatch.setattr(work, 'saved_prices', saved_prices)
    records, failures = {}, {}
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(.1):
            await preload.stored_prices(['KR:A', 'KR:B'], date(2026, 10, 12), records, failures, poll=0)
    assert set(records) == {'KR:A'}


async def test_a_lost_request_status_fails_the_waiting_prices_instead_of_the_analysis(monkeypatch):
    monkeypatch.setattr(work, 'saved_prices', lambda keys, as_of, rejected=None, fetched_since=None: {})
    monkeypatch.setattr(preload, 'request_prices', lambda keys: 'requested')
    def lost(requested):
        raise RuntimeError('statement timeout')
    monkeypatch.setattr(preload, 'finished_requests', lost)
    failures = {}
    await preload.stored_prices(['KR:A'], date(2026, 10, 12), {}, failures, poll=0)
    assert failures == {'KR:A': 'PRICE_READ_FAILED'}


async def test_a_store_that_cannot_take_requests_fails_missing_prices_with_the_reason(monkeypatch):
    # A read-only review container (or a database fault) cannot queue requests; waiting would only burn the budget.
    monkeypatch.setattr(work, 'saved_prices', lambda keys, as_of, rejected=None, fetched_since=None: rejected.update({k: 'PRICE_STALE' for k in keys}) or {})
    def refuse(keys):
        raise RuntimeError('read only')
    monkeypatch.setattr(preload, 'request_prices', refuse)
    failures = {}
    await preload.stored_prices(['KR:A'], date(2026, 10, 12), {}, failures, poll=0)
    assert failures == {'KR:A': 'PRICE_STALE'}


async def test_urgent_request_is_served_at_any_hour_beside_customer_work_with_the_stored_year(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")  # A scheduled refresh is also due.
    store.submit('customer', 'new', {})  # The analysis that waits.
    requested = preload.request_prices(['KR:069500'])
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    calls = []
    async def load(symbol, end, **kwargs):
        calls.append((symbol, kwargs['history_start']))
        return {**record(), 'history_complete': True}
    serve(monkeypatch, load)
    night = NOW.replace(hour=3)
    assert not await preload.collect_one(session=preload.PriceSession(), now=night), 'Scheduled refreshes keep their window and yield to customers'
    assert preload.finished_requests({'KR:069500': requested}) == {}
    assert await preload.collect_one(session=preload.PriceSession(), now=night, urgent=True)
    assert calls == [('069500.KS', work.price_window(date(2026, 10, 17)))], 'Only the waiting instrument, with its stored year'
    assert preload.finished_requests({'KR:069500': requested}) == {'KR:069500': None}
    with store.connect() as db:
        payload = db.execute("SELECT payload FROM portfolio_dashboard_prices WHERE instrument='KR:069500'").fetchone()['payload']
    assert datetime.fromisoformat(payload['requested_at']) >= requested, 'The stored record says it was fetched after the request'
    assert not await preload.collect_one(session=preload.PriceSession(), now=night, urgent=True), 'A served request is not fetched again'
    later = preload.request_prices(['KR:069500'])
    assert preload.finished_requests({'KR:069500': requested}) == {'KR:069500': None}, \
        'Another analysis requesting the same instrument does not hide the answer already given'
    assert preload.finished_requests({'KR:069500': later}) == {}
    with store.connect() as db:
        db.execute("UPDATE portfolio_market_preload SET urgent_at=NULL WHERE instrument='KR:069500'")
    async def fail(*args, **kwargs):
        raise ValueError('PRICE_STALE')
    serve(monkeypatch, fail)
    requested = preload.request_prices(['KR:360750'])
    assert await preload.collect_one(session=preload.PriceSession(), now=night, urgent=True)
    assert preload.finished_requests({'KR:360750': requested}) == {'KR:360750': 'PRICE_STALE'}


async def test_a_request_made_during_a_fetch_is_fetched_again(isolated_db, monkeypatch):
    # The running fetch started before the second analysis asked, so it cannot answer that analysis.
    first = preload.request_prices(['KR:069500'])
    joined = []
    async def load(symbol, end, **kwargs):
        if not joined:
            joined.append(preload.request_prices(['KR:069500']))
        return {**record(), 'history_complete': True}
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    serve(monkeypatch, load)
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW, urgent=True)
    assert preload.finished_requests({'KR:069500': first}) == {'KR:069500': None}
    assert preload.finished_requests({'KR:069500': joined[0]}) == {}
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW, urgent=True), 'The request made during the fetch is still queued'
    assert preload.finished_requests({'KR:069500': joined[0]}) == {'KR:069500': None}


def child(monkeypatch, code):
    """Replace the warm price process with `code` (a Python script reading requests from stdin); returns the requests log."""
    import sys
    spawned = []
    original = asyncio.create_subprocess_exec
    async def spawn(*args, **kwargs):
        spawned.append(args)
        return await original(sys.executable, '-u', '-c', code, **kwargs)
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
    return spawned


async def test_waiting_for_the_slot_does_not_spend_the_provider_deadline(monkeypatch):
    # A busy worker is not a bad source: only the fetch itself is bounded.
    from edge_portfolio.price_fetch import serialize
    child(monkeypatch, f"import sys\nfor line in sys.stdin:\n print({serialize(record())!r},flush=True)\n")
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout(2 if seconds == 30 else seconds))
    session = preload.PriceSession()
    assert market_data._PRICE_SLOTS.acquire(blocking=False)
    try:
        pending = asyncio.create_task(session.fetch('SOXL', date(2026, 10, 20), None))
        await asyncio.sleep(2.5)
        assert not pending.done()
    finally:
        market_data._PRICE_SLOTS.release()
    try:
        assert (await asyncio.wait_for(pending, 10))['currency'] == 'KRW'
    finally:
        await session.close()


async def test_warm_process_hands_the_slot_to_a_waiting_newsletter_quote(monkeypatch):
    # Dashboard quotes and analysis requests can keep the warm process busy all day; a newsletter quote still gets its turn.
    from edge_portfolio.price_fetch import serialize
    spawned = child(monkeypatch, f"import sys\nfor line in sys.stdin:\n print({serialize(record())!r},flush=True)\n")
    session = preload.PriceSession()
    order = []
    try:
        await session.fetch('SOXL', date(2026, 10, 20), None)
        async def newsletter():
            async with market_data.price_slot():
                order.append('newsletter')
                await asyncio.sleep(.1)
        waiting = asyncio.create_task(newsletter())
        await asyncio.sleep(.1)
        assert not order, 'The warm process holds the slot between fetches'
        await session.fetch('VOO', date(2026, 10, 20), None)
        order.append('collector')
        await waiting
        assert order == ['newsletter', 'collector']
        assert len(spawned) == 2, 'The process restarts after the handover'
    finally:
        await session.close()


async def test_warm_process_passes_the_held_repair_window_and_keeps_the_sanitized_reason(monkeypatch):
    # The dashboard repairs only since the holding started; a provider error arrives as its sanitized code.
    import json
    code = ("import sys,json\nfor line in sys.stdin:\n"
            " print(json.dumps({'error':'PRICE_STALE','request':json.loads(line)}),flush=True)\n")
    child(monkeypatch, code)
    sent = []
    original = market_data.decode_prices
    def decode(body):
        sent.append(json.loads(body)['request'])
        return original(body)
    monkeypatch.setattr(market_data, 'decode_prices', decode)
    session = preload.PriceSession()
    try:
        with pytest.raises(ValueError, match='PRICE_STALE'):
            await session.fetch('SOXL', date(2026, 10, 20), date(2025, 10, 13), repair_since=date(2026, 10, 1))
    finally:
        await session.close()
    assert sent == [{'symbol': 'SOXL', 'end': '2026-10-20', 'start': '2025-10-13', 'repair_since': '2026-10-01', 'failure': None}]


async def test_a_stalled_primary_leaves_the_fallback_its_own_time(monkeypatch):
    # Yahoo can block past the deadline before returning any closes; the fallback still runs, in a new process.
    from edge_portfolio.price_fetch import serialize
    code = ("import sys,json,time\nfor line in sys.stdin:\n"
            " request=json.loads(line)\n"
            " if not request['failure']:time.sleep(60)\n"
            f" print({serialize({**record(), 'primary_failure': 'PRICE_PROVIDER_TIMEOUT'})!r},flush=True)\n")
    spawned = child(monkeypatch, code)
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout({30: .5, 15: 5}.get(seconds, seconds)))
    session = preload.PriceSession()
    try:
        result = await session.fetch('069500.KS', date(2026, 10, 20), None)
    finally:
        await session.close()
    assert result['primary_failure'] == 'PRICE_PROVIDER_TIMEOUT'
    assert len(spawned) == 2, 'The stalled process is killed and the fallback runs in a fresh one'


def test_warm_loader_never_checkpoints_closes_that_are_old_or_in_another_currency(monkeypatch):
    # A checkpoint stands in for the record when the parent's deadline hits, and is published as is.
    from edge_portfolio import price_fetch, data_providers
    old = record()
    old['frame'].loc[old['frame'].index[-1], 'Close'] = float('nan')  # The last row is current, its close is not.
    old['frame'].index = pd.to_datetime(['2026-10-01', '2026-10-16'])
    def primary(*args, on_raw, **kwargs):
        on_raw(old)
        on_raw(record(currency='USD'))
        raise ValueError('PRICE_PROVIDER_TIMEOUT')
    monkeypatch.setattr(price_fetch, 'load_prices', primary)
    monkeypatch.setattr(data_providers, 'load_fallback_prices', lambda *args: {**record(), 'provider': 'fallback'})
    checkpoints = []
    price_fetch.load_preload_prices('069500.KS', date(2026, 10, 17), None, checkpoints.append)
    assert checkpoints == []


def test_warm_fallback_keeps_the_repaired_actual_closes(monkeypatch):
    # Repair fills held sessions the raw response lacked; merging the raw closes would drop them again.
    from edge_portfolio import price_fetch, data_providers
    raw = record()
    raw['frame'].loc[raw['frame'].index[0], 'Close'] = float('nan')
    repaired = record()
    repaired['frame']['Adj Close'] = float('nan')  # Returns are still unusable, so the fallback supplies them.
    def primary(*args, on_raw, **kwargs):
        on_raw(raw)
        return repaired
    monkeypatch.setattr(price_fetch, 'load_prices', primary)
    monkeypatch.setattr(data_providers, 'load_fallback_prices', lambda *args: {
        **record(prices=(90., 99.)), 'provider': 'FinanceDataReader:NAVER:069500', 'return_basis': 'total_return',
        'source_url': 'https://example.com/fallback'})
    checkpoints = []
    saved = price_fetch.load_preload_prices('069500.KS', date(2026, 10, 17), None, checkpoints.append)
    assert list(saved['frame']['Close']) == [100., 110.]
    assert list(saved['frame']['Adj Close']) == [90., 99.]
    assert checkpoints[-1] is repaired, 'A fallback that fails or stalls still leaves the parent the repaired closes'


async def test_a_stalled_fallback_after_a_rate_limit_keeps_the_rate_limit(monkeypatch):
    # The rate limit pauses every bulk fetch for 15 minutes; a second fallback call would hide it.
    code = ("import sys,time\nsys.stdin.readline()\n"
            "print('{\"_fallback\": \"PRICE_PROVIDER_RATE_LIMIT\"}',flush=True)\ntime.sleep(60)\n")
    spawned = child(monkeypatch, code)
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout(.5 if seconds == 30 else seconds))
    session = preload.PriceSession()
    try:
        with pytest.raises(ValueError, match='PRICE_PROVIDER_RATE_LIMIT'):
            await session.fetch('SOXL', date(2026, 10, 20), None)
    finally:
        await session.close()
    assert len(spawned) == 1, 'The fallback already ran; it is not started again'


@pytest.mark.parametrize('ending', ['exit', 'late'])
async def test_a_late_primary_failure_still_leaves_the_fallback_its_time(monkeypatch, ending):
    # Yahoo can fail just before the deadline; the fallback that starts then gets its own 15 seconds,
    # and a fallback process that dies keeps the rate limit as the reason.
    from edge_portfolio.price_fetch import serialize
    marker = "print('{\"_fallback\": \"PRICE_PROVIDER_RATE_LIMIT\"}',flush=True)\n"
    if ending == 'exit':
        code = f"import sys\nsys.stdin.readline()\n{marker}"
    else:
        code = (f"import sys,time\nsys.stdin.readline()\ntime.sleep(.4)\n{marker}time.sleep(.4)\n"
                f"print({serialize({**record(), 'primary_failure': 'PRICE_PROVIDER_RATE_LIMIT'})!r},flush=True)\n")
    child(monkeypatch, code)
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout(.6 if seconds == 30 else seconds))
    session = preload.PriceSession()
    try:
        if ending == 'exit':
            with pytest.raises(ValueError, match='PRICE_PROVIDER_RATE_LIMIT'):
                await session.fetch('SOXL', date(2026, 10, 20), None)
        else:
            assert (await session.fetch('SOXL', date(2026, 10, 20), None))['primary_failure'] == 'PRICE_PROVIDER_RATE_LIMIT'
    finally:
        await session.close()


async def test_each_fetch_replaces_the_stored_window_and_starts_at_the_oldest_holding(isolated_db, monkeypatch):
    # 2026-10-26: whole archives (KO since 1962) were merged into and kept; every read of them cost the worker memory.
    # The collector now stores one window per instrument: a year, or from the oldest holding's basis date.
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    archive = record()
    archive['frame'] = pd.concat([pd.DataFrame({'Close': 50., 'Adj Close': 40., 'Dividends': 0.}, index=pd.to_datetime(['1962-01-02'])), archive['frame']])
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('KR:069500'),('KR:360750')")
        db.execute("INSERT INTO portfolio_quote_refresh(instrument,since) VALUES ('KR:360750','2023-03-02')")
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), {**archive, 'requested_at': '2026-10-17T00:00:00+00:00'})
    starts = {}
    async def load(symbol, end, **kwargs):
        starts[symbol] = kwargs['history_start']
        return record()
    serve(monkeypatch, load)
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW)
    with store.connect() as db:  # NOW is in the past, so its next close is already due again.
        db.execute("UPDATE portfolio_market_preload SET quote_after=now()+interval '1 day' WHERE instrument='KR:069500'")
    assert await preload.collect_one(session=preload.PriceSession(), now=NOW)
    assert starts == {'069500.KS': date(2025, 10, 2), '360750.KS': date(2023, 3, 2)}
    with store.connect() as db:
        first = db.execute("SELECT payload->'frame'->0->>'date' AS d FROM portfolio_dashboard_prices WHERE instrument='KR:069500'").fetchone()['d']
    assert first == '2026-10-15', 'The 1962 archive is replaced, not merged into'



def test_a_response_that_omits_an_announced_split_keeps_it_pending(isolated_db):
    # The split is unproven until its session closes; dropping the flag would value the holding on an unknown basis.
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), {**record(), 'pending_splits': ['2026-10-19'], 'requested_at': '2026-10-17T01:00:00+00:00'})
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), {**record(), 'requested_at': '2026-10-17T02:00:00+00:00'})
        stored = db.execute("SELECT payload FROM portfolio_dashboard_prices").fetchone()['payload']
    assert stored['pending_splits'] == ['2026-10-19'] and stored['requested_at'].startswith('2026-10-17T02')
