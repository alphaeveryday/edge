from datetime import date, datetime
import pandas as pd
import pytest
from edge_portfolio import dashboard, dashboard_work as work, dashboard_quotes as quotes, store
from test_store import isolated_db  # noqa: F401


def asset(**changes):
    return dashboard.Asset(id='a', instrument_id='KR:069500', name='KODEX 200', account='ISA', quantity=10., average=80., **changes).model_dump(mode='json')


def record(prices=(100., 110.), currency='KRW', dividends=(0., 2.)):
    return {'frame': pd.DataFrame({'Close': prices, 'Adj Close': prices, 'Dividends': dividends}, index=pd.to_datetime(['2026-10-15', '2026-10-16'])), 'currency': currency, 'source_url': 'https://example.com/prices'}


def test_us_cache_advances_only_after_the_session_closes_even_within_one_korean_day():
    day = date(2026, 10, 16)
    assert work.price_end('US:VOO', day, datetime.fromisoformat('2026-10-16T01:00:00+09:00')) == date(2026, 10, 15)
    assert work.price_end('US:VOO', day, datetime.fromisoformat('2026-10-16T06:00:00+09:00')) == date(2026, 10, 16)
    assert work.price_end('FX', day, datetime.fromisoformat('2026-10-16T01:00:00+09:00')) == date(2026, 10, 15)


def test_korean_cache_includes_todays_close_only_after_the_market_closes():
    day = date(2026, 10, 16)
    assert work.price_end('KR:069500', day, datetime.fromisoformat('2026-10-16T15:29:00+09:00')) == date(2026, 10, 16)
    assert work.price_end('KR:069500', day, datetime.fromisoformat('2026-10-16T15:31:00+09:00')) == date(2026, 10, 17)


def test_instrument_identity_survives_analysis_import():
    result = dashboard.from_analysis({'input': {'holdings': [{'instrument_id': 'KR:069500', 'name': 'KODEX 200', 'account': 'ISA', 'amount_krw': '100000'}]}})
    assert result['assets'][0]['instrument_id'] == 'KR:069500'
    again = dashboard.from_analysis({'input': {'holdings': [{'instrument_id': 'KR:069500', 'name': 'KODEX 200', 'account': 'ISA', 'amount_krw': '100000'}]}})
    assert again['assets'][0]['id'] != result['assets'][0]['id'], 'Replacement imports must establish a fresh quantity basis'


def test_known_legacy_names_are_resolved_but_unknown_names_are_not_guessed():
    a = asset()
    a['instrument_id'] = None
    assert work.identify(a)['instrument_id'] == 'KR:069500'
    assert work.identify({**a, 'name': 'my fund'})['instrument_id'] is None


def test_customer_cannot_send_arbitrary_price_symbols():
    with pytest.raises(ValueError, match='SELECT_LISTED_INSTRUMENT'):
        work.identify({**asset(), 'instrument_id': 'https://internal'})


def test_price_weight_and_profit_need_no_customer_quote_or_target():
    result = work.valuation({'assets': [asset()]}, {'KR:069500': record()}, None, date(2026, 10, 18))
    assert result['assets'][0]['value'] == 1100
    assert result['assets'][0]['profit'] == 300
    assert result['missing'] == []


def test_us_profit_uses_current_fx_without_inventing_purchase_fx():
    a = {**asset(), 'instrument_id': 'US:VOO', 'currency': 'USD'}
    result = work.valuation({'assets': [a]}, {'US:VOO': record(currency='USD'), 'FX': record((1300., 1400.))}, None, date(2026, 10, 18))
    assert result['assets'][0]['value'] == 1540000
    assert result['assets'][0]['profit'] == 420000


def test_amount_import_with_an_average_has_profit_even_without_entered_quantity():
    a = {**asset(), 'instrument_id': 'US:QLD', 'currency': 'USD', 'quantity': None, 'amount': 140000., 'average': 80.}
    result = work.valuation({'assets': [a]}, {'US:QLD': record((90., 100.), currency='USD'), 'FX': record((1300., 1400.))}, None, date(2026, 10, 18))
    q = result['assets'][0]
    assert q['quantity'] == 1
    assert q['profit'] == 28000
    assert q['profit'] / (q['value'] - q['profit']) == .25


def test_missing_fx_is_not_zero_or_customer_supplied_stale_fx():
    a = {**asset(), 'instrument_id': 'US:VOO', 'currency': 'USD'}
    result = work.valuation({'assets': [a], 'fx': 1}, {'US:VOO': record(currency='USD')}, None, date(2026, 10, 18))
    assert result['assets'] == [] and result['missing'] == ['a']


@pytest.mark.parametrize('quantity,amount', [(0., None), (None, 0.)])
async def test_zero_holdings_need_no_quotes_fx_or_dividends_and_cannot_block_daily_records(isolated_db, monkeypatch, quantity, amount):
    zero = {**asset(), 'id': 'zero', 'instrument_id': 'US:VOO', 'currency': 'USD', 'quantity': quantity, 'amount': amount, 'average': None, 'price_date': '2024-01-01'}
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset(), zero])))
    calls = []
    def prices(db, since, day):
        assert since['KR:069500'] > date(2024, 1, 1), 'A zero balance must not extend active holdings repair history'
        calls.append(list(since))
        return {'KR:069500': record()}
    monkeypatch.setattr(quotes, 'cached', prices)
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    await work.work_once()
    result = work.tracking('owner')
    assert calls == [['KR:069500']]
    assert result['days'][0]['value'] == 1100
    assert result['market']['payload']['missing'] == []
    assert result['market']['payload']['dividend_missing'] == ['a'], 'Only the positive holding with short history needs dividend data'
    assert result['market']['payload']['assets'][1]['value'] == 0
    assert result['market']['payload']['assets'][1]['instrument_id'] == 'US:VOO'


def test_amount_import_tracks_market_moves_instead_of_resetting_value_each_day():
    a = {**asset(), 'quantity': None, 'amount': 1100.}
    first = work.valuation({'assets': [a]}, {'KR:069500': record()}, None, date(2026, 10, 18))
    next_day = work.valuation({'assets': [a]}, {'KR:069500': record((110., 121.))}, first, date(2026, 10, 19))
    assert next_day['assets'][0]['quantity'] == 10
    assert next_day['assets'][0]['value'] == 1210
    changed = work.valuation({'assets': [{**a, 'amount': 2420.}]}, {'KR:069500': record((110., 121.))}, first, date(2026, 10, 19))
    assert changed['assets'][0]['quantity'] == 20


def test_forecast_crosses_year_boundary_without_fabricating_payment_dates():
    r = record()
    r['frame'] = pd.DataFrame({'Close': 100., 'Adj Close': 100., 'Dividends': 0.}, index=pd.bdate_range('2025-10-01', '2026-10-16'))
    r['frame'].loc['2026-01-16', 'Dividends'] = 2.
    result = work.forecast(asset(), r, 10, 1, date(2026, 10, 18))
    assert result == [{'asset_id': 'a', 'month': '2027-01', 'amount': 20}]
    assert 'date' not in result[0]


def test_unknown_distribution_series_is_not_zero_income():
    r = record()
    del r['frame']['Dividends']
    result = work.valuation({'assets': [asset()]}, {'KR:069500': r}, None, date(2026, 10, 18))
    assert result['dividend_missing'] == ['a']


def test_split_adjusts_quantity_and_average_without_faking_a_loss_or_repeating_the_split():
    a = asset()
    before = record()
    before['frame'] = before['frame'].iloc[:1]
    first = work.valuation({'assets': [a]}, {'KR:069500': before}, None, date(2026, 10, 16))
    split = record((50., 55.))
    split['frame']['Stock Splits'] = [0., 2.]
    result = work.valuation({'assets': [a]}, {'KR:069500': split}, first, date(2026, 10, 18))
    assert result['assets'][0]['value'] == 1100
    assert result['assets'][0]['quantity'] == 20
    assert result['assets'][0]['average'] == 40
    repeat = work.valuation({'assets': [a]}, {'KR:069500': split}, result, date(2026, 10, 19))
    assert repeat['assets'] == result['assets']
    sold = {**a, 'quantity_revision': 'customer-edit', 'average': 40.}
    updated = work.valuation({'assets': [sold]}, {'KR:069500': split}, result, date(2026, 10, 19))
    assert updated['assets'][0]['quantity'] == 10, 'An explicit sale back to the original quantity must not replay an old split'
    assert updated['assets'][0]['value'] == 550


def test_catalog_removal_cannot_hide_an_existing_portfolio_or_block_unrelated_edits(isolated_db, monkeypatch):
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    monkeypatch.setattr(work, 'directory', lambda **kwargs: {'instruments': []})
    saved = dashboard.read('owner')
    assert saved['workbook']['assets'][0]['instrument_id'] is None
    assert saved['workbook']['assets'][0]['quantity'] == 10
    result = work.valuation(saved['workbook'], {}, None, date(2026, 10, 18))
    assert result['missing'] == ['a']
    saved['workbook']['title'] = 'Still editable'
    assert dashboard.save('owner', dashboard.Save(**saved))['workbook']['title'] == 'Still editable'


def test_editing_average_price_does_not_reset_an_amount_import_to_its_original_value():
    a = {**asset(), 'quantity': None, 'amount': 1100.}
    first = work.valuation({'assets': [a]}, {'KR:069500': record()}, None, date(2026, 10, 18))
    changed = work.valuation({'assets': [{**a, 'average': 90.}]}, {'KR:069500': record((110., 121.))}, first, date(2026, 10, 19))
    assert changed['assets'][0]['value'] == 1210
    assert changed['assets'][0]['profit'] == 310


def test_missing_quantity_or_fx_does_not_claim_zero_forecast():
    a = {**asset(), 'quantity': None, 'amount': None}
    result = work.valuation({'assets': [a]}, {'KR:069500': record()}, None, date(2026, 10, 18))
    assert result['dividend_missing'] == ['a']


async def test_daily_worker_records_without_browser_and_keeps_history_on_edit(isolated_db, monkeypatch):
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    async def prices(*args, **kwargs):
        return {'KR:069500': record()}, []
    monkeypatch.setattr(quotes, 'cached', lambda *args: {'KR:069500': record()})
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    assert await work.work_once()
    result = work.tracking('owner')
    assert result['days'][0]['value'] == 1100
    assert work.tracking('other') == {'market': None, 'days': [], 'archives': []}
    dashboard.save('owner', dashboard.Save(version=1, workbook=dashboard.Workbook(assets=[])))
    assert await work.work_once()
    assert work.tracking('owner')['days'] == result['days'], 'Removing holdings must not delete past results'


async def test_edit_during_price_collection_cannot_publish_stale_holdings(isolated_db, monkeypatch):
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    async def prices(*args, **kwargs):
        dashboard.save('owner', dashboard.Save(version=1, workbook=dashboard.Workbook(assets=[])))
        return {'KR:069500': record()}, []
    monkeypatch.setattr(work, 'records_for', prices)
    assert await quotes.collect_once()
    assert work.tracking('owner')['market']['version'] == 2
    assert work.tracking('owner')['market']['payload']['assets'] == []
    assert dashboard.read('owner')['version'] == 2
    assert dashboard.read('owner')['workbook']['assets'] == []


async def test_price_failure_keeps_previous_daily_records(isolated_db, monkeypatch):
    async def prices(*args, **kwargs):
        return {}, ['KR:069500']
    monkeypatch.setattr(work, 'records_for', prices)
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    await work.work_once()
    assert work.tracking('owner')['days'] == []
    assert work.tracking('owner')['market']['payload']['missing'] == ['a']


def test_a_temporary_outage_cannot_reset_amount_tracking_or_split_baselines():
    a = {**asset(), 'quantity': None, 'amount': 1100.}
    first = work.valuation({'assets': [a]}, {'KR:069500': record()}, None, date(2026, 10, 18))
    failed = work.valuation({'assets': [a]}, {}, first, date(2026, 10, 19))
    recovered = work.valuation({'assets': [a]}, {'KR:069500': record((110., 121.))}, failed, date(2026, 10, 20))
    assert failed['bases'] == first['bases']
    assert recovered['assets'][0]['value'] == 1210


def test_short_zero_dividend_series_is_unknown_not_a_zero_annual_forecast():
    assert work.forecast(asset(), record(dividends=(0., 0.)), 10, 1, date(2026, 10, 18)) is None


def test_replacing_holdings_archives_manual_history_with_original_names(isolated_db):
    w = dashboard.Workbook(assets=[asset()], dividends=[{'id': 'd', 'asset_id': 'a', 'date': '2026-10-01', 'status': '실수령', 'amount': 100.}], months=[{'month': '2026-09', 'value': 1000.}])
    dashboard.save('owner', dashboard.Save(version=0, workbook=w))
    dashboard.save('owner', dashboard.Save(version=1, workbook=dashboard.Workbook()))
    archived = work.tracking('owner')['archives'][0]['payload']
    assert archived['dividends'][0]['amount'] == 100
    assert archived['months'][0]['value'] == 1000
    assert 'KODEX 200' in archived['assets'][0]['name']
    assert work.tracking('other')['archives'] == []


async def test_collection_deadline_keeps_completed_prices(isolated_db, monkeypatch):
    import asyncio
    from edge_portfolio import market_data
    original_timeout = asyncio.timeout
    monkeypatch.setattr(work.asyncio, 'timeout', lambda _: original_timeout(1))
    async def fetch(symbol, day, *, on_raw=None, on_primary=None, provider_timeout=None, repair_since=None):
        if symbol == '360750.KS':
            await asyncio.sleep(10)
        return record()
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    records, failures = await work.records_for(['KR:069500', 'KR:360750'], date(2026, 10, 18))
    assert set(records) == {'KR:069500'}
    assert failures == ['KR:360750']


async def test_a_slow_first_provider_cannot_starve_a_healthy_later_holding(isolated_db, monkeypatch):
    import asyncio
    from edge_portfolio import market_data
    wait_for = asyncio.wait_for
    monkeypatch.setattr(work.asyncio, 'wait_for', lambda task, timeout: wait_for(task, min(timeout, .05)))
    async def fetch(symbol, day, *, on_raw=None, on_primary=None, provider_timeout=None, repair_since=None):
        if symbol == '069500.KS':
            await asyncio.wait_for(asyncio.sleep(20), timeout=provider_timeout)
        return record()
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    async with asyncio.timeout(5):
        records, failures = await work.records_for(['KR:069500', 'KR:360750'], date(2026, 10, 18))
    assert set(records) == {'KR:360750'}
    assert failures == ['KR:069500']


async def test_dashboard_waits_for_capacity_before_starting_provider_deadlines(isolated_db, monkeypatch):
    import asyncio
    from edge_portfolio import market_data
    wait_for = asyncio.wait_for
    monkeypatch.setattr(work.asyncio, 'wait_for', lambda task, timeout: wait_for(task, min(timeout, .02)))
    async def fetch(*args, **kwargs):
        return record()
    monkeypatch.setattr(market_data, '_fetch_prices', fetch)
    assert market_data._PRICE_SLOTS.acquire(blocking=False)
    try:
        pending = asyncio.create_task(work.records_for(['KR:069500'], date(2026, 10, 18)))
        await asyncio.sleep(.05)
        assert not pending.done(), 'A queued quote is not a failed source'
    finally:
        market_data._PRICE_SLOTS.release()
    records, failures = await wait_for(pending, 2)
    assert set(records) == {'KR:069500'}
    assert failures == []


@pytest.mark.parametrize('currency,fx,expected', [('KRW', None, 100.), ('USD', 1300., 130000.)])
def test_legacy_cash_quantity_keeps_its_recorded_value(currency, fx, expected):
    old = {**asset(), 'category': '현금', 'currency': currency, 'quantity': 100., 'price': 1., 'amount': None, 'instrument_id': None}
    converted = work.identify(old, fx=fx)
    assert converted['amount'] == expected
    assert converted['quantity'] is None
    assert converted['currency'] == 'KRW'
    result = work.valuation({'assets': [old], 'fx': fx}, {}, None, date(2026, 10, 18))
    assert result['assets'][0]['value'] == expected


@pytest.mark.parametrize('currency', ['KRW', 'USD'])
async def test_interrupted_optional_repair_keeps_valid_raw_prices_only(isolated_db, monkeypatch, currency):
    import asyncio
    from edge_portfolio import market_data
    wait_for = asyncio.wait_for
    monkeypatch.setattr(work.asyncio, 'wait_for', lambda task, timeout: wait_for(task, .05))
    async def fetch(symbol, day, *, on_raw=None, on_primary=None, provider_timeout=None, repair_since=None):
        try:
            await asyncio.wait_for(asyncio.sleep(20), timeout=provider_timeout)
        except (asyncio.CancelledError, TimeoutError):
            on_raw(record(currency=currency))
            raise
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    records, failures = await work.records_for(['KR:069500'], date(2026, 10, 18))
    assert list(records) == (['KR:069500'] if currency == 'KRW' else [])
    assert failures == ([] if currency == 'KRW' else ['KR:069500'])


def test_quantity_entered_on_split_day_is_not_split_again_when_prices_catch_up():
    a = {**asset(), 'quantity': 20., 'average': 40., 'quantity_as_of': '2026-10-16', 'average_as_of': '2026-10-16'}
    old = record()
    old['frame'] = old['frame'].iloc[:1]
    old['pending_splits'] = ['2026-10-16']
    first = work.valuation({'assets': [a]}, {'KR:069500': old}, None, date(2026, 10, 16))
    assert first['assets'] == []
    assert first['pending_splits'] == ['a']
    split = record((50., 55.))
    split['frame']['Stock Splits'] = [0., 2.]
    result = work.valuation({'assets': [a]}, {'KR:069500': split}, first, date(2026, 10, 17))
    assert result['assets'][0]['quantity'] == 20
    assert result['assets'][0]['average'] == 40
    assert result['assets'][0]['value'] == 1100


async def test_entry_dates_are_server_owned_and_unrelated_saves_preserve_them(isolated_db):
    a = {**asset(), 'quantity_as_of': '1900-01-01', 'average_as_of': '1900-01-01'}
    saved = dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[a])))
    first = saved['workbook']['assets'][0]
    assert first['quantity_as_of'] != '1900-01-01'
    assert first['average_as_of'] == first['quantity_as_of']
    first['account'] = 'IRP'
    saved = dashboard.save('owner', dashboard.Save(version=1, workbook=dashboard.Workbook(assets=[first])))
    assert saved['workbook']['assets'][0]['quantity_as_of'] == first['quantity_as_of']


def test_adjusted_only_fallback_is_for_returns_not_current_asset_values():
    r = {**record(), 'provider': 'FinanceDataReader:NAVER:069500'}
    result = work.valuation({'assets': [asset()]}, {'KR:069500': r}, None, date(2026, 10, 18))
    assert result['missing'] == ['a']
    assert result['assets'] == []


def test_legacy_currency_mismatch_requires_relinking_without_reinterpreting_cost():
    a = {**asset(), 'name': 'VOO', 'instrument_id': None, 'currency': 'KRW', 'average': 500000.}
    resolved = work.identify(a, strict=False)
    assert resolved['instrument_id'] is None
    assert resolved['currency'] == 'KRW'
    assert resolved['average'] == 500000
    with pytest.raises(ValueError, match='SELECT_LISTED_INSTRUMENT'):
        work.identify(a)


def test_editing_split_adjusted_average_back_to_original_number_uses_the_new_entry_day():
    a = {**asset(), 'quantity_as_of': '2026-10-15', 'average_as_of': '2026-10-15'}
    old = record()
    old['frame'] = old['frame'].iloc[:1]
    first = work.valuation({'assets': [a]}, {'KR:069500': old}, None, date(2026, 10, 15))
    split = record((50., 55.))
    split['frame']['Stock Splits'] = [0., 2.]
    prior = work.valuation({'assets': [a]}, {'KR:069500': split}, first, date(2026, 10, 16))
    assert prior['assets'][0]['average'] == 40
    corrected = {**a, 'average_revision': 'explicit-edit', 'average_as_of': '2026-10-16'}
    result = work.valuation({'assets': [corrected]}, {'KR:069500': split}, prior, date(2026, 10, 17))
    assert result['assets'][0]['average'] == 80
    assert result['assets'][0]['quantity'] == 20


def test_first_automatic_quote_uses_a_known_legacy_snapshot_date_for_splits():
    a = {**asset(), 'price_date': '2026-10-15'}
    split = record((50., 55.))
    split['frame']['Stock Splits'] = [0., 2.]
    result = work.valuation({'assets': [a]}, {'KR:069500': split}, None, date(2026, 10, 18))
    assert result['assets'][0]['quantity'] == 20
    assert result['assets'][0]['profit'] == 300


async def test_adjusted_only_cached_prices_do_not_block_actual_close_recovery(isolated_db, monkeypatch):
    from edge_portfolio import market_data
    calls = []
    async def fetch(symbol, day, *, on_raw=None, on_primary=None, provider_timeout=None, repair_since=None):
        calls.append(symbol)
        return {**record(), **({'provider': 'FinanceDataReader:NAVER:069500'} if len(calls) == 1 else {})}
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    await work.records_for(['KR:069500'], date(2026, 10, 18))
    recovered, failures = await work.records_for(['KR:069500'], date(2026, 10, 18))
    assert len(calls) == 2
    assert failures == []
    result = work.valuation({'assets': [asset()]}, recovered, None, date(2026, 10, 18))
    assert result['assets'][0]['value'] == 1100


async def test_each_provider_gets_time_even_when_the_primary_stalls(monkeypatch):
    import asyncio
    from edge_portfolio import market_data
    calls = []
    async def fetch(symbol, day, *, on_raw=None, provider='yahoo'):
        calls.append(provider)
        if provider == 'yahoo':
            await asyncio.sleep(20)
        return record()
    monkeypatch.setattr(market_data, '_fetch_prices', fetch)
    result = await market_data.fetch_prices('069500.KS', date(2026, 10, 18), provider_timeout=.02)
    assert calls == ['yahoo', 'fallback']
    assert result['currency'] == 'KRW'


async def test_resolving_a_legacy_symbol_does_not_rebase_unchanged_split_inputs(isolated_db):
    from psycopg.types.json import Jsonb
    a = {**asset(), 'instrument_id': None, 'price_date': '2026-10-15'}
    payload = dashboard.Workbook(assets=[a]).model_dump(mode='json')
    with store.connect() as db:
        db.execute('INSERT INTO portfolio_workbooks(owner,version,payload) VALUES (%s,1,%s)', ('owner', Jsonb(payload)))
    loaded = dashboard.read('owner')
    loaded['workbook']['title'] = 'renamed'
    loaded['workbook']['assets'][0]['price_date'] = None
    saved = dashboard.save('owner', dashboard.Save.model_validate(loaded))
    assert saved['workbook']['assets'][0]['quantity_as_of'] == '2026-10-15'
    split = record((50., 55.))
    split['frame']['Stock Splits'] = [0., 2.]
    result = work.valuation(saved['workbook'], {'KR:069500': split}, None, date(2026, 10, 18))
    assert result['assets'][0]['quantity'] == 20
    assert result['assets'][0]['profit'] == 300


def test_manually_linking_a_legacy_alias_keeps_snapshot_but_replacing_a_known_fund_resets_it(isolated_db):
    from psycopg.types.json import Jsonb
    a = {**asset(), 'name': 'my old fund alias', 'instrument_id': None, 'price_date': '2026-10-15'}
    payload = dashboard.Workbook(assets=[a]).model_dump(mode='json')
    with store.connect() as db:
        db.execute('INSERT INTO portfolio_workbooks(owner,version,payload) VALUES (%s,1,%s)', ('owner', Jsonb(payload)))
    loaded = dashboard.read('owner')
    loaded['workbook']['assets'][0].update(instrument_id='KR:069500', name='KODEX 200', price_date=None)
    saved = dashboard.save('owner', dashboard.Save.model_validate(loaded))
    assert saved['workbook']['assets'][0]['quantity_as_of'] == '2026-10-15'
    assert saved['workbook']['assets'][0]['average_as_of'] == '2026-10-15'
    split = record((50., 55.))
    split['frame']['Stock Splits'] = [0., 2.]
    result = work.valuation(saved['workbook'], {'KR:069500': split}, None, date(2026, 10, 18))
    assert result['assets'][0]['quantity'] == 20
    assert result['assets'][0]['profit'] == 300
    saved['workbook']['assets'][0]['instrument_id'] = 'KR:360750'
    replaced = dashboard.save('owner', dashboard.Save.model_validate(saved))
    assert replaced['workbook']['assets'][0]['quantity_as_of'] != '2026-10-15'


async def test_adjusted_fallback_keeps_captured_actual_prices_actions_and_source(isolated_db, monkeypatch):
    from edge_portfolio import market_data
    async def fetch(symbol, day, *, on_raw=None, provider='yahoo'):
        if provider == 'yahoo':
            raw = record()
            raw['frame']['Adj Close'] = float('nan')
            return raw
        return {**record((100., 120.)), 'provider': 'FinanceDataReader:NAVER:069500', 'return_basis': 'total_return', 'source_url': 'https://example.com/returns'}
    monkeypatch.setattr(market_data, '_fetch_prices', fetch)
    records, failures = await work.records_for(['KR:069500'], date(2026, 10, 18))
    result = work.valuation({'assets': [asset()]}, records, None, date(2026, 10, 18))
    assert result['assets'][0]['value'] == 1100
    assert records['KR:069500']['frame']['Dividends'].iloc[-1] == 2


async def test_incomplete_cached_returns_are_retried_without_losing_actual_closes(isolated_db, monkeypatch):
    from psycopg.types.json import Jsonb
    from edge_portfolio import market_data
    from edge_portfolio.price_fetch import serialize
    import json
    r = record()
    r['frame']['Adj Close'] = float('nan')
    with store.connect() as db:
        db.execute('INSERT INTO portfolio_dashboard_prices VALUES (%s,%s,%s)', ('KR:069500', work.price_end('KR:069500', date(2026, 10, 18)), Jsonb(json.loads(serialize(r)))))
    calls = []
    async def fetch(symbol, day, **kwargs):
        calls.append(symbol)
        return record()
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    records, failures = await work.records_for(['KR:069500'], date(2026, 10, 18))
    assert calls == ['069500.KS']
    assert failures == []
    assert records['KR:069500']['frame']['Adj Close'].notna().all()


def test_annual_distribution_this_month_is_preserved_in_the_next_twelve_months():
    r = record()
    r['frame'] = pd.DataFrame({'Close': 100., 'Adj Close': 100., 'Dividends': 0.}, index=pd.bdate_range('2025-10-01', '2026-10-16'))
    r['frame'].loc['2026-10-15', 'Dividends'] = 5.
    assert work.forecast(asset(), r, 10, 1, date(2026, 10, 18)) == [{'asset_id': 'a', 'month': '2027-10', 'amount': 50}]


@pytest.mark.parametrize('recovery', ['not_needed', 'partial'])
async def test_adding_a_holding_reuses_quotes_but_action_checks_refresh_each_minute(isolated_db, monkeypatch, recovery):
    from edge_portfolio import market_data
    calls = []
    async def fetch(symbol, day, **kwargs):
        calls.append(symbol)
        return {**record(), 'price_recovery': {'status': recovery}}
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    day = date(2026, 10, 18)
    since = {'KR:069500': day, 'KR:360750': day}
    await work.records_for(['KR:069500'], day, valuation_since=since)
    records, failures = await work.records_for(list(since), day, valuation_since=since)
    assert calls == ['069500.KS', '360750.KS'], 'Adding one holding must not invalidate another holding or another owner’s shared quote'
    assert failures == []
    with store.connect() as db:
        db.execute("UPDATE portfolio_dashboard_prices SET payload=jsonb_set(payload,'{fetched_at}',to_jsonb((now()-interval '61 seconds')::text)) WHERE instrument='KR:069500'")
    await work.records_for(list(since), day, valuation_since=since)
    assert calls == ['069500.KS', '360750.KS', '069500.KS'], 'Only expired quotes refresh; late split announcements must still be detected'


async def test_a_collection_deadline_cannot_reclassify_failed_expired_quotes_as_fresh(isolated_db, monkeypatch):
    import asyncio
    from edge_portfolio import market_data
    day = date(2026, 10, 18)
    since = {'KR:069500': day, 'KR:360750': day}
    async def fetch(*args, **kwargs):
        return record()
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    await work.records_for(list(since), day, valuation_since=since)
    with store.connect() as db:
        db.execute("UPDATE portfolio_dashboard_prices SET payload=payload-'actions_checked_on'")
    timeout = asyncio.timeout
    monkeypatch.setattr(work.asyncio, 'timeout', lambda _: timeout(1))
    async def unavailable(symbol, *args, **kwargs):
        if symbol == '069500.KS':
            raise TimeoutError()
        await asyncio.sleep(10)
    monkeypatch.setattr(market_data, 'fetch_prices', unavailable)
    records, failures = await work.records_for(list(since), day, valuation_since=since)
    assert records == {}
    assert failures == sorted(since), 'Both the prior failure and interrupted refresh remain failures; UI keeps their last valuations instead'


@pytest.mark.parametrize('known', [False, True])
def test_missing_quantity_never_accumulates_duplicate_quotes_on_minute_refreshes(known):
    prior = work.valuation({'assets': [asset()]}, {'KR:069500': record()}, None, date(2026, 10, 18)) if known else None
    unknown = {**asset(), 'quantity': None, 'amount': None, 'weight': 100.}
    for _ in range(3):
        prior = work.valuation({'assets': [unknown]}, {'KR:069500': record()}, prior, date(2026, 10, 19))
        assert len(prior['assets']) == 1
        assert prior['assets'][0]['value'] == (1100 if known else None)
        assert prior['missing'] == ['a']


async def test_background_refresh_keeps_last_values_without_writing_a_false_daily_snapshot(isolated_db, monkeypatch):
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[asset()])))
    monkeypatch.setattr(quotes, 'cached', lambda *args: {'KR:069500': record()})
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    assert await work.work_once()
    first = work.tracking('owner')
    with store.connect() as db:
        due = db.execute("SELECT extract(epoch FROM refresh_after-now()) AS seconds FROM portfolio_workbooks WHERE owner='owner'").fetchone()['seconds']
        assert 0 < due <= 60
    monkeypatch.setattr(quotes, 'cached', lambda *args: {})
    saved = dashboard.read('owner')
    saved['workbook']['assets'][0]['quantity'] = 20.
    dashboard.save('owner', dashboard.Save(**saved))
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 19))
    assert await work.work_once()
    after = work.tracking('owner')
    assert after['market']['payload']['assets'] == first['market']['payload']['assets']
    assert after['days'] == first['days'], 'An old displayed quote is not a newly observed daily balance'
    with store.connect() as db:
        due = db.execute("SELECT extract(epoch FROM refresh_after-now()) AS seconds FROM portfolio_workbooks WHERE owner='owner'").fetchone()['seconds']
        assert 0 < due <= 60, 'A failed refresh retries on the minute without clearing customer values'


def test_last_values_are_never_reused_for_a_different_instrument_with_the_same_row_id():
    old = work.valuation({'assets': [asset()]}, {'KR:069500': record()}, None, date(2026, 10, 18))
    replacement = {**asset(), 'instrument_id': 'KR:360750'}
    updated = work.valuation({'assets': [replacement]}, {}, old, date(2026, 10, 19))
    assert updated['assets'] == [] and updated['forecasts'] == []


def test_a_failed_refresh_cannot_make_an_old_current_month_dividend_look_fresh():
    prior = work.valuation({'assets': [asset()]}, {'KR:069500': record()}, None, date(2026, 10, 19))
    prior['forecasts'] = [{'asset_id': 'a', 'month': month, 'amount': 100} for month in ['2026-10', '2026-11']]
    result = work.valuation({'assets': [asset()]}, {}, prior, date(2026, 10, 21))
    assert result['assets'] == prior['assets'], 'Keep last valuation on screen'
    assert result['forecasts'] == [{'asset_id': 'a', 'month': '2026-11', 'amount': 100}], 'Current-month estimates require today’s observations, even when other values are retained'
    again = work.valuation({'assets': [asset()]}, {}, result, date(2026, 11, 1))
    assert again['forecasts'] == [], 'Crossing a month cannot revive an estimate from an earlier observation'


async def test_valuation_skips_old_price_repairs_but_backtest_upgrades_its_cache(isolated_db, monkeypatch):
    from edge_portfolio import market_data
    repairs = []
    async def fetch(symbol, day, **kwargs):
        repair = kwargs['repair_since']
        repairs.append(repair)
        return {**record(), **({'repair_since': min(repair, date(2025, 10, 16)).isoformat()} if repair else {})}
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    day = date(2026, 10, 18)
    await work.records_for(['KR:069500'], day, valuation_since={'KR:069500': day})
    await work.records_for(['KR:069500'], day, valuation_since={'KR:069500': day})
    assert repairs == [day], 'A current dashboard quote is reusable without repairing historical gaps'
    await work.records_for(['KR:069500'], day)
    assert repairs == [day, None], 'A later backtest must still request the historical repair pass'
    await work.records_for(['KR:069500'], day, valuation_since={'KR:069500': date(2023, 10, 1)})
    assert repairs == [day, None, date(2023, 10, 1)], 'An unmarked old backtest cache only covers two years, not a longer-held split basis'


async def test_failed_earlier_basis_upgrade_cannot_publish_a_missing_held_split(isolated_db, monkeypatch):
    from edge_portfolio import market_data
    r = record()
    r['frame'] = pd.DataFrame({'Close': 100., 'Adj Close': 100.}, index=pd.bdate_range('2024-10-01', '2026-10-16')).drop(pd.Timestamp('2024-11-05'))
    r['repair_since'] = '2025-10-16'
    async def fetch(*args, **kwargs):
        return r
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    day = date(2026, 10, 18)
    fresh, _ = await work.records_for(['KR:069500'], day, valuation_since={'KR:069500': day})
    assert fresh
    async def unavailable(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(market_data, 'fetch_prices', unavailable)
    records, failures = await work.records_for(['KR:069500'], day, valuation_since={'KR:069500': date(2024, 10, 1)})
    assert records == {}
    assert failures == ['KR:069500']


async def test_rejected_held_gap_cache_is_retried_next_minute_even_when_scope_is_marked_complete(isolated_db, monkeypatch):
    from edge_portfolio import market_data
    calls = []
    async def fetch(*args, **kwargs):
        calls.append(kwargs['repair_since'])
        r = record()
        r['frame'] = pd.DataFrame({'Close': 100., 'Adj Close': 100.}, index=pd.bdate_range('2026-10-12', '2026-10-16'))
        r['repair_since'] = '2025-10-16'
        if len(calls) == 1:
            r['frame'] = r['frame'].drop(pd.Timestamp('2026-10-14'))
        return r
    monkeypatch.setattr(market_data, 'fetch_prices', fetch)
    args = dict(valuation_since={'KR:069500': date(2026, 10, 12)})
    records, failures = await work.records_for(['KR:069500'], date(2026, 10, 18), **args)
    assert records == {} and failures == ['KR:069500']
    records, failures = await work.records_for(['KR:069500'], date(2026, 10, 18), **args)
    assert len(calls) == 1 and records == {} and failures == ['KR:069500'], 'Edits within the same minute do not hammer a provider with the same unresolved gap'
    with store.connect() as db:
        db.execute("UPDATE portfolio_dashboard_prices SET payload=jsonb_set(payload,'{fetched_at}',to_jsonb((now()-interval '61 seconds')::text))")
    records, failures = await work.records_for(['KR:069500'], date(2026, 10, 18), **args)
    assert len(calls) == 2 and records and not failures, 'A rejected cached gap must recover without manual cache deletion'


def test_replaced_quantity_and_average_do_not_reuse_obsolete_split_bases():
    old = asset(quantity_revision='old', average_revision='old')
    previous = work.valuation({'assets': [old]}, {'KR:069500': record()}, None, date(2026, 10, 18))['bases']['a']
    previous.update(as_of='2024-01-01', average_day='2024-01-01')
    current = {**old, 'quantity_revision': 'new', 'average_revision': 'new', 'quantity_as_of': '2026-10-18', 'average_as_of': '2026-10-18'}
    assert work.basis_dates(current, previous, '2026-10-19') == ('2026-10-18', '2026-10-18')
    assert work.basis_dates(old, previous, '2026-10-19') == ('2024-01-01', '2024-01-01')
    cleared = {**current, 'average': None, 'average_as_of': '2024-01-01'}
    assert work.basis_dates(cleared, previous, '2026-10-19') == ('2026-10-18', '2026-10-18')


def test_truncated_provider_history_cannot_hide_a_held_split():
    assert work.held_history_missing('KR:069500', record(), date(2024, 1, 1), date(2026, 10, 16))
    assert not work.held_history_missing('KR:069500', record(), date(2026, 10, 15), date(2026, 10, 16))
    assert work.held_history_missing('KR:069500', record(), date(2026, 10, 19), date(2026, 10, 19)), 'A missing latest session must not pair post-split quantity with pre-split price'
    stale = record()
    stale['frame'] = stale['frame'].iloc[:-1]
    assert work.held_history_missing('KR:069500', stale, date(2026, 10, 18), date(2026, 10, 16)), 'A weekend entry still needs the most recent completed session'


async def test_each_instrument_repairs_only_its_own_holding_basis(isolated_db, monkeypatch):
    old = asset(quantity_as_of='2024-01-01', average_as_of='2024-01-01')
    new = {**asset(), 'id': 'new', 'instrument_id': 'US:QLD', 'currency': 'USD', 'quantity_as_of': '2026-10-18', 'average_as_of': '2026-10-18'}
    dashboard.save('owner', dashboard.Save(version=0, workbook=dashboard.Workbook(assets=[old, new])))
    from psycopg.types.json import Jsonb
    with store.connect() as db:
        saved = dashboard.read('owner')['workbook']
        saved['assets'] = [old, new]
        db.execute("UPDATE portfolio_workbooks SET payload=%s,refresh_after=now() WHERE owner='owner'", (Jsonb(saved),))
    calls = []
    def prices(db, since, day):
        calls.append(since)
        return {}
    monkeypatch.setattr(quotes, 'cached', prices)
    monkeypatch.setattr(work, 'today', lambda: date(2026, 10, 18))
    assert await work.work_once()
    assert calls == [{'KR:069500': date(2024, 1, 1), 'US:QLD': date(2026, 10, 18), 'FX': date(2026, 10, 18)}]


def test_analyses_keep_the_global_admission_limit_without_backtests(isolated_db):
    # 백테스트를 없앴으니 대기열 한도는 분석 작업만 센다 — 사라진 표를 세다 막히면 분석 접수가 멈춘다.
    for i in range(20):
        store.submit(f'owner-{i}', f'key-{i}', {'holdings': []})
    with pytest.raises(ValueError, match='QUEUE_FULL'):
        store.submit('analysis-owner', 'analysis-key', {'holdings': []})
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET state='failed'")
    assert store.submit('analysis-owner', 'analysis-key', {'holdings': []})['id']


def test_analysis_reuses_only_archives_as_current_as_a_new_fetch():
    # ALPHA-1437: a reused archive must equal what the provider would return for as_of,
    # otherwise the analysis would silently compare an older window.
    import json
    from edge_portfolio.price_fetch import serialize
    as_of = date(2026, 10, 16)
    def saved(days, **extra):
        prices = [100. + i for i in range(len(days))]
        frame = pd.DataFrame({'Close': prices, 'Adj Close': prices}, index=pd.to_datetime(days))
        return json.loads(serialize({'frame': frame, 'currency': 'KRW', 'source_url': 'https://example.com', 'history_complete': True, **extra}))
    current = work.analysis_archive('KR:069500', saved(['2026-10-14', '2026-10-15', '2026-10-16']), as_of)
    assert [str(d.date()) for d in current['frame'].index] == ['2026-10-14', '2026-10-15'], 'The as_of session is cut like a fetch'
    assert work.analysis_archive('KR:069500', saved(['2026-10-13', '2026-10-14']), as_of) is None, 'Missing the last finished session'
    assert work.analysis_archive('KR:069500', saved(['2026-10-14', '2026-10-15'], history_complete=False), as_of) is None, 'Recent quotes only'
    assert work.analysis_archive('KR:069500', saved(['2026-10-14', '2026-10-15'], return_basis='price'), as_of) is None
