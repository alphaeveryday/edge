from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from edge_portfolio import dashboard, dashboard_work as work, market_data, market_preload as preload, store
from test_dashboard_work import asset, record
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
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    assert await preload.collect_one(now=now)
    dashboard.save('new', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    assert work.tracking('new')['market']['payload']['assets'][0]['value'] == 1100
    with store.connect() as db:
        db.execute("UPDATE portfolio_quote_refresh SET refresh_after=now()+interval '1 minute'")
    assert await preload.collect_one(now=now)
    assert [c[0] for c in calls] == ['069500.KS', 'SOXL']
    assert all(c[1] is not None for c in calls), 'No archive may delay catalogue price coverage'


async def test_preload_yields_to_running_customer_quotes(isolated_db):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")
        db.execute("""INSERT INTO portfolio_quote_refresh(instrument,since,refresh_after,lease_until)
            VALUES ('US:VOO',current_date,now()+interval '1 minute',now()+interval '5 minutes')""")
    assert not await preload.collect_one(now=NOW), 'A warm background child must not starve a customer price request'
    with store.connect() as db:
        assert db.execute("SELECT lease_until FROM portfolio_market_preload").fetchone()['lease_until'] is None


async def test_scheduled_preload_turn_runs_with_customer_refresh_due(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('KR:069500')")
        db.execute("INSERT INTO portfolio_quote_refresh(instrument,since) VALUES ('US:VOO',current_date)")
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    async def load(*args, **kwargs):
        return record()
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    assert await preload.collect_one(now=NOW)
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM portfolio_dashboard_prices WHERE instrument='KR:069500'").fetchone()
        assert db.execute("SELECT 1 FROM portfolio_quote_refresh WHERE refresh_after<=now()").fetchone()


async def test_preload_yields_to_customer_analysis_and_source_errors_back_off(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")
    job = store.submit('customer', 'new', {})
    assert not await preload.collect_one(now=NOW)
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET state='failed' WHERE id=%s", (job['id'],))
    async def fail(*args, **kwargs):
        raise ValueError('PRICE_PROVIDER_RATE_LIMIT')
    monkeypatch.setattr(market_data, 'fetch_prices', fail)
    assert await preload.collect_one(now=NOW)
    with store.connect() as db:
        row = db.execute("SELECT * FROM portfolio_market_preload WHERE instrument='US:SOXL'").fetchone()
    assert row['failures'] == 1 and row['last_error'] == 'PRICE_PROVIDER_RATE_LIMIT'
    assert row['quote_after'] == NOW + timedelta(minutes=15) and row['lease_until'] is None
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:VOO')")
    assert not await preload.collect_one(now=NOW), 'A source-wide rate limit must pause other symbols too'


def test_daily_window_preserves_archive_but_changed_adjustments_do_not_mix(isolated_db):
    old = {**record(), 'history_complete': True, 'repair_since': '2020-01-01',
           'requested_at': '2026-10-17T00:00:00+00:00'}
    old['frame'] = pd.concat([pd.DataFrame({'Close': [90.], 'Adj Close': [90.], 'Dividends': [0.]},
        index=pd.to_datetime(['2020-01-02'])), old['frame']])
    recent = {**record(), 'history_complete': False, 'repair_since': '2026-09-01',
              'requested_at': '2026-10-18T00:00:00+00:00'}
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
        assert len(saved['frame']) == 3 and saved['history_complete']
        assert saved['repair_since'] == '2020-01-01'
        recent['frame']['Close'] *= .5
        recent['requested_at'] = '2026-10-19T00:00:00+00:00'
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
        assert len(saved['frame']) == 2 and not saved['history_complete']


def test_recent_loader_requests_only_window_and_keeps_archive_for_explicit_backfill(monkeypatch):
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
    assert recent['history_complete'] is False and archive['history_complete'] is True


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


async def test_recent_quotes_never_satisfy_a_full_backtest_after_source_failure(isolated_db, monkeypatch):
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17),
            {**record(), 'history_complete': False, 'repair_since': '2020-01-01',
             'requested_at': '2026-10-18', 'actions_checked_on': '2026-10-18'})
    async def fail(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(market_data, 'fetch_prices', fail)
    records, failures = await work.records_for(['KR:069500'], date(2026, 10, 18))
    assert not records and failures == ['KR:069500']


@pytest.mark.parametrize('price_only', [False, True])
def test_recent_update_preserves_repair_debt_and_never_promotes_price_only_history(isolated_db, price_only):
    old = {**record(), 'history_complete': True, 'repair_since': '2020-01-01',
           'requested_at': '2026-10-17', 'price_recovery': {'status': 'partial', 'unresolved_dates': ['2020-01-03']}}
    if price_only:
        old['return_basis'] = 'price'
    recent = {**record(), 'history_complete': False, 'repair_since': '2026-09-01',
              'requested_at': '2026-10-18', 'price_recovery': {'status': 'not_needed'}}
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    if price_only:
        assert saved['history_complete'] is False
    else:
        assert saved['price_recovery'] == old['price_recovery']


def test_distribution_adjustment_rebases_archive_without_losing_dividend_events(isolated_db):
    old = {**record(), 'history_complete': True, 'repair_since': '2020-01-01', 'requested_at': '2026-10-17'}
    old['frame'] = pd.concat([pd.DataFrame({'Close': [90.], 'Adj Close': [90.], 'Dividends': [3.]},
        index=pd.to_datetime(['2020-01-02'])), old['frame']])
    recent = {**record(), 'history_complete': False, 'repair_since': '2026-09-01', 'requested_at': '2026-10-18'}
    recent['frame']['Adj Close'] *= .98
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['history_complete'] and len(saved['frame']) == 3
    assert saved['frame'][0]['Adj Close'] == pytest.approx(88.2)
    assert saved['frame'][0]['Dividends'] == 3


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
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    assert await preload.collect_one(now=NOW)
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM portfolio_dashboard_prices WHERE instrument='US:SOXL'").fetchone()
    assert not await preload.collect_one(now=NOW)


@pytest.mark.parametrize('quote_due', [True, False])
@pytest.mark.parametrize('limited', [True, False])
async def test_superseded_archive_remains_due_for_retry(isolated_db, monkeypatch, quote_due, limited):
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument,quote_after) VALUES ('US:SOXL',now()+interval '1 day')")
        if quote_due:
            db.execute("UPDATE portfolio_market_preload SET quote_after=now()-interval '1 day'")
        work.publish_prices(db, 'US:SOXL', date(2026, 10, 17),
            {**record(currency='USD'), 'history_complete': False, 'requested_at': '2099-01-01'})
    async def load(*args, **kwargs):
        return {**record(currency='USD'), 'history_complete': True,
                'price_recovery': {'reason': 'PRICE_PROVIDER_RATE_LIMIT' if limited else ''}}
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    assert await preload.collect_one(now=NOW)
    with store.connect() as db:
        row = db.execute("SELECT *,history_after<now()+interval '2 minutes' AS soon,quote_after>now() AS deferred FROM portfolio_market_preload").fetchone()
    assert row['last_error'] == ('PRICE_PROVIDER_RATE_LIMIT' if limited else 'PRICE_PUBLICATION_SUPERSEDED') and row['soon']
    assert row['lease_until'] is None and row['deferred']
    if limited:
        with store.connect() as db:
            db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:VOO')")
        assert not await preload.collect_one(now=NOW)


def test_partial_adjusted_prices_cannot_erase_existing_archive(isolated_db):
    old = {**record(), 'history_complete': True, 'repair_since': '2020-01-01', 'requested_at': '2026-10-17'}
    old['frame'] = pd.concat([pd.DataFrame({'Close': [90.], 'Adj Close': [90.], 'Dividends': [3.]},
        index=pd.to_datetime(['2020-01-02'])), old['frame']])
    recent = {**record(), 'history_complete': False, 'repair_since': '2026-09-01', 'requested_at': '2026-10-18',
              'price_recovery': {'status': 'partial'}}
    recent['frame'].iloc[0, recent['frame'].columns.get_loc('Adj Close')] = float('nan')
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['history_complete'] is False and len(saved['frame']) == 3
    assert saved['frame'][1]['Adj Close'] is None
    assert saved['frame'][0]['Dividends'] == 3


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
    monkeypatch.setattr(market_data, 'fetch_prices', fail)
    records, failures = await work.records_for(['FX'], NOW.date(), valuation_since={})
    assert 'FX' in records and not failures


async def test_preload_rejects_stale_positive_fx_despite_recent_empty_rows(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 20))
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('FX')")
    class Session:
        async def fetch(self, *args):
            result = record(prices=(1300., float('nan')))
            result['frame'].index = pd.to_datetime(['2026-10-01', '2026-10-19'])
            return result
    await preload.collect_one(now=NOW, session=Session())
    with store.connect() as db:
        assert not db.execute("SELECT 1 FROM portfolio_dashboard_prices").fetchone()
        assert db.execute("SELECT last_error FROM portfolio_market_preload").fetchone()['last_error'] == 'PRICE_STALE'


@pytest.mark.parametrize('missing_column', [True, False, 'zero'])
def test_partial_updates_keep_actions_and_publish_raw_prices_without_adjustment_anchors(isolated_db, missing_column):
    old = {**record(), 'history_complete': True, 'requested_at': '2026-10-17'}
    old['frame']['Stock Splits'] = [2., 0.]
    recent = {**record(prices=(100., 110.)), 'history_complete': False, 'requested_at': '2026-10-18'}
    recent['frame']['Adj Close'] = float('nan')
    recent['frame']['Dividends'] = float('nan')
    if not missing_column:
        recent['frame']['Stock Splits'] = float('nan')
    if missing_column == 'zero':
        recent['frame']['Stock Splits'] = 0.
        recent['frame']['Dividends'] = 0.
    recent['frame'].loc[pd.Timestamp('2026-10-19'), 'Close'] = 120.
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        assert work.publish_prices(db, 'KR:069500', date(2026, 10, 20), recent)
        saved = db.execute("SELECT payload FROM portfolio_dashboard_prices").fetchone()['payload']
    assert saved['history_complete'] is False
    assert saved['frame'][-1]['Close'] == 120
    assert saved['frame'][0]['Stock Splits'] == 2
    assert saved['frame'][1]['Dividends'] == 2
    assert all(row['Adj Close'] is None for row in saved['frame'])


@pytest.mark.parametrize('changed_basis', [True, False])
def test_archive_merge_cannot_make_a_stale_response_fresh_or_clear_pending_split(isolated_db, changed_basis):
    old = {**record(), 'history_complete': True, 'requested_at': '2026-10-17', 'pending_splits': ['2026-10-20']}
    old['frame'].loc[pd.Timestamp('2026-10-19')] = [120., 120., 0.]
    recent = {**record(), 'history_complete': False, 'requested_at': '2026-10-20', 'pending_splits': []}
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 20), old)
        assert not work.publish_prices(db, 'KR:069500', date(2026, 10, 20), recent)
        recent['frame'] = old['frame'].copy()
        if changed_basis:
            recent['frame']['Close'] *= .5
        assert work.publish_prices(db, 'KR:069500', date(2026, 10, 20), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['observed_on'] == '2026-10-19' and saved['pending_splits'] == ['2026-10-20']


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


def test_one_adjustment_anchor_cannot_extrapolate_through_a_dividend_date(isolated_db):
    old = {**record(prices=(100., 98.), dividends=(0., 0.)), 'history_complete': True, 'requested_at': '2026-10-17'}
    recent = {**record(prices=(100., 98.)), 'history_complete': False, 'requested_at': '2026-10-18'}
    recent['frame']['Adj Close'] = [98., float('nan')]
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['history_complete'] is False
    assert saved['frame'][1]['Close'] == 98 and saved['frame'][1]['Dividends'] == 2
    assert saved['frame'][1]['Adj Close'] is None, 'A missing post-dividend anchor must not become 96.04'


def test_action_only_weekend_row_does_not_invalidate_valid_adjustment_anchors(isolated_db):
    old = {**record(), 'history_complete': True, 'requested_at': '2026-10-17'}
    old['frame'].loc[pd.Timestamp('2026-10-11')] = [float('nan'), float('nan'), 3.]
    old['frame'] = old['frame'].sort_index()
    recent = {**old, 'history_complete': False, 'requested_at': '2026-10-18'}
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['history_complete'] is True
    assert saved['frame'][0]['Dividends'] == 3
    assert saved['frame'][-1]['Adj Close'] == 110


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
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original_timeout({30: .8, 40: 1.}.get(seconds, seconds)))
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


def test_action_only_row_cannot_replace_a_second_priced_anchor(isolated_db):
    old = {**record(prices=(100., 98.), dividends=(0., 0.)), 'history_complete': True, 'requested_at': '2026-10-17'}
    old['frame'].loc[pd.Timestamp('2026-10-11')] = [float('nan'), float('nan'), 3.]
    old['frame'] = old['frame'].sort_index()
    recent = {**old, 'frame': old['frame'].drop(pd.Timestamp('2026-10-15')).copy(),
              'history_complete': False, 'requested_at': '2026-10-18'}
    recent['frame'].loc[pd.Timestamp('2026-10-16'), 'Dividends'] = 2.
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert not saved['history_complete'] and len(saved['frame']) == 3
    assert all(row['Adj Close'] is None for row in saved['frame'])


@pytest.mark.parametrize('changed_ratio', [True, False])
def test_changed_adjustments_or_actions_preserve_cash_history_but_require_fresh_adjusted_archive(isolated_db, changed_ratio):
    old = {**record(prices=(100., 98.)), 'history_complete': True, 'requested_at': '2026-10-17'}
    old['frame']['Adj Close'] = [98., 98.]
    recent = {**record(prices=(100., 98.), dividends=(0., 0.)), 'history_complete': False, 'requested_at': '2026-10-18'}
    if not changed_ratio:
        recent['frame']['Adj Close'] = [98., 98.]
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['history_complete'] is False
    assert saved['frame'][1]['Dividends'] == 2
    assert saved['frame'][1]['Close'] == 98


def test_expanding_short_cache_preserves_valid_adjustments_and_new_dividends(isolated_db):
    old = {**record(), 'history_complete': False, 'requested_at': '2026-10-17'}
    expanded = {**record(), 'history_complete': False, 'requested_at': '2026-10-18'}
    expanded['frame'].loc[pd.Timestamp('2025-11-03')] = [90., 85., 3.]
    expanded['frame'] = expanded['frame'].sort_index()
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), expanded)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert [row['Dividends'] for row in saved['frame']] == [3., 0., 2.]
    assert [row['Adj Close'] for row in saved['frame']] == [85., 100., 110.]


def test_new_action_before_overlap_anchors_requires_full_adjustment_read(isolated_db):
    old = {**record(), 'history_complete': True, 'requested_at': '2026-10-17'}
    old['frame'].loc[pd.Timestamp('2020-01-02')] = [90., 90., 0.]
    old['frame'] = old['frame'].sort_index()
    recent = {**record(), 'history_complete': False, 'requested_at': '2026-10-18'}
    recent['frame'].loc[pd.Timestamp('2026-10-11')] = [float('nan'), float('nan'), 3.]
    recent['frame'] = recent['frame'].sort_index()
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert not saved['history_complete'] and len(saved['frame']) == 4
    assert saved['frame'][1]['Dividends'] == 3
    assert saved['frame'][0]['Adj Close'] is None


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
    assert result['provider'] == 'existing-fallback' and not result['history_complete']
    assert result['primary_failure'] == {'stale': 'PRICE_STALE', 'invalid': 'PRICE_INVALID'}.get(failure, failure)


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


def test_price_only_incoming_quotes_cannot_be_promoted_to_total_return_history(isolated_db):
    old = {**record(), 'history_complete': True, 'return_basis': 'total_return', 'requested_at': '2026-10-17'}
    recent = {**record(), 'history_complete': False, 'return_basis': 'price', 'requested_at': '2026-10-18'}
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['history_complete'] is False and saved['return_basis'] == 'price'
    assert saved['frame'][-1]['Close'] == 110 and saved['frame'][-1]['Dividends'] == 2


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


def test_missing_actions_keep_correction_due_after_they_leave_recent_window(isolated_db):
    old = {**record(), 'history_complete': True, 'requested_at': '2026-10-17'}
    recent = {**record(), 'history_complete': False, 'requested_at': '2026-10-20'}
    recent['frame'] = recent['frame'].drop(columns=['Dividends'])
    recent['frame'].loc[pd.Timestamp('2026-10-19')] = [120., 120.]
    with store.connect() as db:
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
        work.publish_prices(db, 'KR:069500', date(2026, 10, 20), recent)
        saved = db.execute('SELECT payload FROM portfolio_dashboard_prices').fetchone()['payload']
    assert saved['history_complete'] is True and saved['actions_complete'] is False
    assert saved['frame'][-1]['Dividends'] is None and saved['frame'][1]['Dividends'] == 2


async def test_missing_dividends_keep_backfill_due_without_hiding_valid_prices(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    monkeypatch.setattr(work, 'today', lambda: NOW.date())
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument,quote_after) VALUES ('KR:069500',now()+interval '1 day')")
    async def load(*args, **kwargs):
        result = {**record(), 'history_complete': True}
        result['frame']['Dividends'] = float('nan')
        return result
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    await preload.collect_one(now=NOW)
    with store.connect() as db:
        assert db.execute('SELECT history_after FROM portfolio_market_preload').fetchone()['history_after'] is not None
    dashboard.save('customer', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    assert work.tracking('customer')['market']['payload']['assets'][0]['value'] == 1100


@pytest.mark.parametrize('has_splits', [True, False])
async def test_archive_finishes_only_with_both_action_columns(isolated_db, monkeypatch, has_splits):
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument,quote_after) VALUES ('KR:069500',now()+interval '1 day')")
    async def load(*args, **kwargs):
        result = {**record(), 'history_complete': True}
        if has_splits:
            result['frame']['Stock Splits'] = 0.
        return result
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    await preload.collect_one(now=NOW)
    with store.connect() as db:
        row = db.execute('SELECT history_after FROM portfolio_market_preload').fetchone()
    assert (row['history_after'] is None) is has_splits


async def test_rejected_old_quote_cannot_skip_today_refresh_because_archive_is_complete(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 20))
    old = {**record(), 'history_complete': True, 'requested_at': '2026-10-17', 'actions_checked_on': '2026-10-17'}
    old['frame']['Stock Splits'] = 0.
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('KR:069500')")
        work.publish_prices(db, 'KR:069500', date(2026, 10, 17), old)
    async def load(*args, **kwargs):
        result = record()
        result['frame'].index = pd.to_datetime(['2026-10-14', '2026-10-15'])
        return {**result, 'history_complete': False}
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    await preload.collect_one(now=NOW)
    with store.connect() as db:
        row = db.execute("SELECT last_error,quote_after<now()+interval '2 minutes' AS soon FROM portfolio_market_preload").fetchone()
    assert row['last_error'] == 'PRICE_PUBLICATION_SUPERSEDED' and row['soon']


async def test_analysis_waits_only_for_what_the_store_lacks_and_reports_the_collectors_failure(monkeypatch):
    # The analysis never calls a provider: stored prices answer at once, only missing ones are
    # requested from the collector (in order) and awaited, and a collector failure is that instrument's code.
    stored = {'KR:A': record()}
    requested, polls = [], iter([{}, {'KR:B': None}, {'KR:C': 'PRICE_STALE'}])
    def saved_prices(keys, as_of, rejected=None, latest=True):
        assert latest == ('KR:A' in keys), 'Only a read after the collector served a request relaxes the latest-session rule'
        rejected.update({key: 'PRICE_NOT_STORED' for key in keys if key not in stored})
        return {key: stored[key] for key in keys if key in stored}
    def request(keys):
        requested.extend(keys)
        stored['KR:B'] = record()  # The collector will publish B.
        return 'requested'
    monkeypatch.setattr(work, 'saved_prices', saved_prices)
    monkeypatch.setattr(preload, 'request_prices', request)
    monkeypatch.setattr(preload, 'finished_requests', lambda keys, requested: next(polls))
    monkeypatch.setattr(market_data, 'fetch_prices', lambda *a, **k: pytest.fail('analysis must not call a provider'))
    records, failures = {}, {'KR:A': 'PRICE_QUEUE_TIMEOUT'}
    await preload.stored_prices(['KR:A', 'KR:B', 'KR:C'], date(2026, 10, 12), records, failures, poll=0)
    assert requested == ['KR:B', 'KR:C']
    assert set(records) == {'KR:A', 'KR:B'} and failures == {'KR:C': 'PRICE_STALE'}


async def test_a_store_that_cannot_take_requests_fails_missing_prices_with_the_reason(monkeypatch):
    # A read-only review container (or a database fault) cannot queue requests; waiting would only burn the budget.
    monkeypatch.setattr(work, 'saved_prices', lambda keys, as_of, rejected=None: rejected.update({k: 'PRICE_STALE' for k in keys}) or {})
    def refuse(keys):
        raise RuntimeError('read only')
    monkeypatch.setattr(preload, 'request_prices', refuse)
    failures = {}
    await preload.stored_prices(['KR:A'], date(2026, 10, 12), {}, failures, poll=0)
    assert failures == {'KR:A': 'PRICE_STALE'}


async def test_urgent_request_is_served_at_any_hour_beside_customer_work_with_the_whole_archive(isolated_db, monkeypatch):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_market_preload(instrument) VALUES ('US:SOXL')")  # A scheduled refresh is also due.
    store.submit('customer', 'new', {})  # The analysis that waits.
    requested = preload.request_prices(['KR:069500'])
    monkeypatch.setattr(work, 'price_end', lambda *args: date(2026, 10, 17))
    calls = []
    async def load(symbol, end, **kwargs):
        calls.append((symbol, kwargs['history_start']))
        return {**record(), 'history_complete': True}
    monkeypatch.setattr(market_data, 'fetch_prices', load)
    night = NOW.replace(hour=3)
    assert not await preload.collect_one(now=night), 'Scheduled refreshes keep their window and yield to customers'
    assert preload.finished_requests(['KR:069500'], requested) == {}
    assert await preload.collect_one(now=night, urgent=True)
    assert calls == [('069500.KS', None)], 'Only the waiting instrument, with its whole archive'
    assert preload.finished_requests(['KR:069500'], requested) == {'KR:069500': None}
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM portfolio_dashboard_prices WHERE instrument='KR:069500'").fetchone()
    assert not await preload.collect_one(now=night, urgent=True), 'A served request is not fetched again'
    async def fail(*args, **kwargs):
        raise ValueError('PRICE_STALE')
    monkeypatch.setattr(market_data, 'fetch_prices', fail)
    requested = preload.request_prices(['KR:360750'])
    assert await preload.collect_one(now=night, urgent=True)
    assert preload.finished_requests(['KR:360750'], requested) == {'KR:360750': 'PRICE_STALE'}
