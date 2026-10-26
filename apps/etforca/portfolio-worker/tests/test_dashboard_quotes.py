import asyncio
import time
from datetime import date
import pytest

from edge_portfolio.market_preload import PriceSession
from edge_portfolio import dashboard, dashboard_work as work, dashboard_quotes as quotes, market_data, store
from test_dashboard_work import asset, record, serve
from test_store import isolated_db  # noqa: F401


async def test_continuous_customer_refreshes_leave_bounded_preload_turns(monkeypatch):
    from edge_portfolio import market_preload as preload
    stopping = asyncio.Event()
    clock, events, seeds = [0], [], []
    async def customer(*args, **kwargs):
        events.append('customer')
        clock[0] += 20
        if len(events) >= 8:
            stopping.set()
        return True  # There is always another customer quote due.
    async def background(*args, urgent=False, **kwargs):
        if urgent:
            return False  # No analysis is waiting.
        events.append('preload')
        if events.count('preload') == 2:
            stopping.set()
        return True
    monkeypatch.setattr(quotes, 'monotonic', lambda: clock[0], raising=False)
    monkeypatch.setattr(quotes, 'collect_once', customer)
    monkeypatch.setattr(preload, 'collect_one', background)
    monkeypatch.setattr(preload, 'seed', lambda: seeds.append('seed'))
    await quotes.collection_loop(stopping)
    assert events == ['customer'] * 3 + ['preload'] + ['customer'] * 3 + ['preload']
    assert seeds == ['seed'], 'Catalogue seeding must not run once per customer quote'


async def test_an_analysis_waiting_for_prices_is_served_before_customer_quotes(monkeypatch):
    # The analysis reads only the store, so its missing instruments must not wait behind the
    # dashboard queue or the minute between preload turns.
    from edge_portfolio import market_preload as preload
    stopping = asyncio.Event()
    events, waiting = [], ['KR:A', 'KR:B']
    async def background(*args, urgent=False, **kwargs):
        if urgent and waiting:
            events.append('urgent:' + waiting.pop(0))
            return True
        return False
    async def customer(*args, **kwargs):
        events.append('customer')
        stopping.set()
        return True
    monkeypatch.setattr(quotes, 'collect_once', customer)
    monkeypatch.setattr(preload, 'collect_one', background)
    monkeypatch.setattr(preload, 'seed', lambda: None)
    await quotes.collection_loop(stopping)
    assert events == ['urgent:KR:A', 'urgent:KR:B', 'customer']


async def test_customer_quotes_reuse_the_warm_preload_process(isolated_db, monkeypatch):
    # A new process per holding re-imports the libraries and calendars and fetches a new cookie (5-8s each).
    from edge_portfolio import market_preload as preload
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    session = preload.PriceSession()
    assert market_data._PRICE_SLOTS.acquire(blocking=False)
    session.admitted = True
    used = []
    async def fetch(ids, day, *, session, **kwargs):
        used.append(session)
        return {}, []
    monkeypatch.setattr(work, 'records_for', fetch)
    try:
        assert await quotes.collect_once(preload_session=session)
        assert used == [session] and session.admitted, 'The warm process stays open for the next turn'
    finally:
        await session.close()


async def test_idle_collection_reuses_preload_child_and_cancellation_releases_slot(monkeypatch):
    from edge_portfolio import market_preload as preload
    sessions = []
    async def customer(*args, **kwargs):
        return False
    async def background(*args, session):
        sessions.append(session)
        if len(sessions) == 1:
            assert market_data._PRICE_SLOTS.acquire(blocking=False)
            session.admitted = True
            return True
        assert session is sessions[0] and session.admitted
        raise asyncio.CancelledError
    monkeypatch.setattr(quotes, 'collect_once', customer)
    monkeypatch.setattr(preload, 'collect_one', background)
    monkeypatch.setattr(preload, 'seed', lambda: None)
    with pytest.raises(asyncio.CancelledError):
        await quotes.collection_loop(asyncio.Event())
    assert not sessions[0].admitted
    assert market_data._PRICE_SLOTS.acquire(blocking=False), 'Shutdown must not strand the shared price slot'
    market_data._PRICE_SLOTS.release()


async def test_saves_serve_shared_prices_immediately_and_only_new_instrument_is_queued(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    fetched = []
    async def fetch(symbol, day, **kwargs):
        fetched.append(symbol)
        return record()
    serve(monkeypatch, fetch)
    dashboard.save('first', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    assert await quotes.collect_once()
    before = time.monotonic()
    dashboard.save('second', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    assert time.monotonic() - before < 1, 'A cached holding save must never wait for its provider'
    assert work.tracking('second')['market']['payload']['assets'][0]['value'] == 1100
    with store.connect() as db:
        deadline = db.execute("SELECT refresh_after FROM portfolio_quote_refresh WHERE instrument='KR:069500'").fetchone()['refresh_after']
    added = {**asset(), 'id': 'b', 'instrument_id': 'KR:360750'}
    dashboard.save('second', dashboard.Save(version=1, workbook=dashboard.Workbook(assets=[asset(), added])))
    state = work.tracking('second')['market']['payload']
    assert state['assets'][0]['value'] == 1100 and state['pending_prices'] == ['b']
    with store.connect() as db:
        assert db.execute("SELECT refresh_after FROM portfolio_quote_refresh WHERE instrument='KR:069500'").fetchone()['refresh_after'] == deadline
    assert await quotes.collect_once()
    assert not await quotes.collect_once()
    assert fetched == ['069500.KS', '360750.KS']
    await work.work_once()
    await work.work_once()
    assert work.tracking('second')['market']['payload']['pending_prices'] == []


async def test_slow_provider_and_duplicate_collectors_cannot_block_saved_values(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    started, release = asyncio.Event(), asyncio.Event()
    async def fetch(*args, **kwargs):
        started.set()
        await release.wait()
        return record()
    serve(monkeypatch, fetch)
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    pending = asyncio.create_task(quotes.collect_once())
    await started.wait()
    try:
        assert not await quotes.collect_once(), 'A claimed instrument has only one provider request'
        before = time.monotonic()
        changed = {**asset(), 'quantity': 20.}
        dashboard.save('owner', dashboard.Save(version=1, workbook=dashboard.Workbook(assets=[changed])))
        assert time.monotonic() - before < 1
        assert work.tracking('owner')['market']['version'] == 2
        assert await work.work_once()
    finally:
        release.set()
        await pending
    await work.work_once()
    assert work.tracking('owner')['market']['payload']['assets'][0]['quantity'] == 20


@pytest.mark.parametrize('remaining', [[], [{**asset(), 'quantity': 0.}], [{**asset(), 'quantity': None, 'amount': 0.}]])
async def test_removed_instrument_never_keeps_spending_provider_calls(isolated_db, monkeypatch, remaining):
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    dashboard.save('owner', dashboard.Save(version=1, workbook=dashboard.Workbook(assets=remaining)))
    async def fail(*args, **kwargs):
        raise AssertionError('An unwatched instrument must not be collected')
    monkeypatch.setattr(work, 'records_for', fail)
    assert await quotes.collect_once()
    assert not await quotes.collect_once()
    with store.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM portfolio_quote_refresh').fetchone()['n'] == 0


async def test_expired_collector_cannot_reschedule_another_collectors_lease(isolated_db, monkeypatch):
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    leases = []
    async def fetch(*args, **kwargs):
        with store.connect() as db:
            leases.append(db.execute("UPDATE portfolio_quote_refresh SET lease_until=now()+interval '10 minutes' RETURNING lease_until").fetchone()['lease_until'])
        return {}, ['KR:069500']
    monkeypatch.setattr(work, 'records_for', fetch)
    await quotes.collect_once()
    with store.connect() as db:
        assert db.execute('SELECT lease_until FROM portfolio_quote_refresh').fetchone()['lease_until'] == leases[0]


async def test_positive_quantity_takes_precedence_over_an_old_zero_import_amount(isolated_db, monkeypatch):
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[{**asset(), 'amount': 0.}])))
    calls = []
    async def fetch(ids, *args, **kwargs):
        calls.extend(ids)
        return {}, ids
    monkeypatch.setattr(work, 'records_for', fetch)
    assert await quotes.collect_once()
    assert calls == ['KR:069500']


async def test_previous_day_actions_cannot_value_a_new_post_split_quantity(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    async def fetch(*args, **kwargs):
        return record()
    serve(monkeypatch, fetch)
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    await quotes.collect_once()
    await work.work_once()
    previous = work.tracking('owner')
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 19))
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    dashboard.save('owner', dashboard.Save(version=1, workbook=dashboard.Workbook(assets=[{**asset(), 'quantity': 20.}])))
    await work.work_once()
    current = work.tracking('owner')
    assert current['market']['payload']['assets'] == previous['market']['payload']['assets']
    assert current['market']['payload']['pending_prices'] == ['a']
    assert current['days'] == previous['days'], 'An old action check must not create a new split-day balance'


def test_collector_releases_quote_locks_before_waking_workbooks(isolated_db, monkeypatch):
    from contextlib import contextmanager
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    entered, release = Event(), Event()
    original = store.connect
    class Connection:
        def __init__(self, db):
            self.db = db
        def execute(self, query, *args):
            if 'UPDATE portfolio_workbooks w SET refresh_after=least(refresh_after,now())' in query:
                entered.set()
                assert release.wait(10)
            return self.db.execute(query, *args)
    @contextmanager
    def connect():
        with original() as db:
            yield Connection(db)
    async def fetch(*args, **kwargs):
        return {}, []
    monkeypatch.setattr(store, 'connect', connect)
    monkeypatch.setattr(work, 'records_for', fetch)
    with ThreadPoolExecutor(1) as pool:
        task = pool.submit(asyncio.run, quotes.collect_once())
        try:
            assert entered.wait(10)
            with original() as db:
                db.execute("SELECT owner FROM portfolio_workbooks WHERE owner='owner' FOR UPDATE")
                db.execute('SELECT instrument FROM portfolio_quote_refresh FOR UPDATE NOWAIT')
        finally:
            release.set()
        assert task.result()


async def test_shutdown_after_claim_releases_it_without_starting_a_provider(isolated_db, monkeypatch):
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    class Stopping:
        checks = 0
        def is_set(self):
            self.checks += 1
            return self.checks > 1
    async def fail(*args, **kwargs):
        raise AssertionError('No new provider request after shutdown')
    monkeypatch.setattr(work, 'records_for', fail)
    assert await quotes.collect_once(Stopping())
    with store.connect() as db:
        assert db.execute('SELECT lease_until FROM portfolio_quote_refresh').fetchone()['lease_until'] is None


async def test_a_reclaimed_collector_cannot_publish_its_prices(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    async def fetch(*args, **kwargs):
        with store.connect() as db:
            db.execute("UPDATE portfolio_quote_refresh SET lease_until=now()+interval '10 minutes'")
        return record()
    serve(monkeypatch, fetch)
    await quotes.collect_once()
    with store.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM portfolio_dashboard_prices').fetchone()['n'] == 0


async def test_legacy_names_are_canonicalized_before_the_collector_checks_activity(isolated_db, monkeypatch):
    from psycopg.types.json import Jsonb
    with store.connect() as db:
        db.execute('INSERT INTO portfolio_workbooks(owner,version,payload) VALUES (%s,1,%s)', ('legacy', Jsonb({'assets': [{**asset(), 'instrument_id': None}]})))
    await work.work_once()
    calls = []
    async def fetch(ids, *args, **kwargs):
        calls.extend(ids)
        return {}, ids
    monkeypatch.setattr(work, 'records_for', fetch)
    await quotes.collect_once()
    assert calls == ['KR:069500']


async def test_return_only_fallback_cannot_replace_actual_close_cache(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    async def actual(*args, **kwargs):
        return {**record(), 'provider': 'Yahoo', 'price_recovery': {'status': 'partial'}}
    serve(monkeypatch, actual)
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    await quotes.collect_once()
    async def adjusted_only(*args, **kwargs):
        return {**record((999., 999.)), 'provider': 'FinanceDataReader:NAVER:adjusted', 'return_basis': 'total_return'}
    serve(monkeypatch, adjusted_only)
    returns, _ = await work.records_for(['KR:069500'], date(2026, 10, 18), session=PriceSession())
    assert returns['KR:069500']['provider'].startswith('FinanceDataReader:NAVER:')
    dashboard.save('other', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    assert work.tracking('other')['market']['payload']['assets'][0]['value'] == 1100, 'Adjusted history must not evict actual closes needed by every owner'


async def test_a_slow_old_backtest_cannot_overwrite_newer_split_observations(isolated_db, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0
    async def fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
            return record()
        return {**record(), 'pending_splits': ['2026-10-19']}
    serve(monkeypatch, fetch)
    older = asyncio.create_task(work.records_for(['KR:069500'], date(2026, 10, 18), session=PriceSession()))
    await started.wait()
    try:
        await work.records_for(['KR:069500'], date(2026, 10, 18), valuation_since={'KR:069500': date(2026, 10, 18)}, session=PriceSession())
    finally:
        release.set()
        await older
    with store.connect() as db:
        assert db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']['pending_splits'] == ['2026-10-19']


async def test_new_fetch_cannot_move_fx_observation_backwards(isolated_db, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 20))
    fresh = record((1400., 1450.))
    fresh['frame'].index = pd.to_datetime(['2026-10-16', '2026-10-19'])
    async def fetch(*args, **kwargs):
        return {**fresh, 'price_recovery': {'status': 'partial'}}
    serve(monkeypatch, fetch)
    await work.records_for(['FX'], date(2026, 10, 20), valuation_since={'FX': date(2026, 10, 20)}, session=PriceSession())
    async def stale(*args, **kwargs):
        return record((1300., 1320.))
    serve(monkeypatch, stale)
    await work.records_for(['FX'], date(2026, 10, 20), session=PriceSession())
    with store.connect() as db:
        payload = db.execute("SELECT payload FROM portfolio_dashboard_prices WHERE instrument='FX'").fetchone()['payload']
    assert payload['observed_on'] == '2026-10-19' and payload['frame'][-1]['Close'] == 1450


async def test_quote_notification_preserves_the_oldest_waiting_workbook(isolated_db, monkeypatch):
    dashboard.save('waiting', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    with store.connect() as db:
        previous = db.execute("UPDATE portfolio_workbooks SET refresh_after=now()-interval '1 day' RETURNING refresh_after").fetchone()['refresh_after']
    async def fetch(*args, **kwargs):
        return {}, []
    monkeypatch.setattr(work, 'records_for', fetch)
    await quotes.collect_once()
    with store.connect() as db:
        assert db.execute('SELECT refresh_after FROM portfolio_workbooks').fetchone()['refresh_after'] == previous


def test_valuation_reads_only_the_window_it_uses_and_values_the_same(isolated_db, monkeypatch):
    # 2026-10-25 OOM loop: every valuation parsed whole archives (decades of days) several times a second.
    # The database now cuts each archive to the window a valuation reads; the result must not change.
    import pandas as pd
    from edge_portfolio.price_fetch import serialize
    day, end = date(2026, 10, 18), date(2026, 10, 17)
    monkeypatch.setattr(work, 'price_end', lambda *args: end)
    sessions = market_data.expected_sessions('KR:069500', pd.Timestamp('2023-01-02'), pd.Timestamp('2026-10-16'))
    frame = pd.DataFrame({'Close': 100., 'Adj Close': 100., 'Dividends': 0., 'Stock Splits': 0.}, index=sessions)
    frame.loc[frame.index.day <= 3, 'Dividends'] = 1.  # Monthly distributions, so the 12-month window is visible.
    frame.loc[sessions[10], 'Stock Splits'] = 2.  # An old split outside the holding must stay outside.
    full = {'frame': frame, 'currency': 'KRW', 'source_url': 'https://example.com', 'history_complete': True,
            'actions_checked_on': str(day)}
    with store.connect() as db:
        db.execute('INSERT INTO portfolio_dashboard_prices VALUES (%s,%s,%s::jsonb)', ('KR:069500', end, serialize(full)))
    holding = {**asset(), 'quantity_as_of': '2026-10-01', 'average_as_of': '2026-10-01'}
    since = {'KR:069500': date(2026, 10, 1)}
    with store.connect() as db:
        windowed = quotes.cached(db, since, day)['KR:069500']
    assert windowed['frame'].index[0] >= pd.Timestamp('2025-10-03'), 'Only about a year is read, not the whole archive'
    workbook = {'assets': [holding]}
    assert work.valuation(workbook, {'KR:069500': windowed}, None, day) == work.valuation(workbook, {'KR:069500': full}, None, day)
    assert work.valuation(workbook, {'KR:069500': windowed}, None, day)['assets'][0]['dividend_yield_pct'], 'The 12-month dividend window survives'
