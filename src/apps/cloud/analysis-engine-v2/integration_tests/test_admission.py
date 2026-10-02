"""Admission and exact-trigger SQL on a disposable, loopback-only PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from edge_analysis_v2.cloud.admission import RequestConflict, canonical_input
from edge_analysis_v2.sources.triggers import load_event_request
from edge_analysis_v2.sources.database import load_prices
from edge_analysis_v2.storage.requests import RequestStore


@pytest.fixture
def connections():
    dsn = os.environ['V2_ADMISSION_TEST_DSN']
    target = conninfo_to_dict(dsn)
    assert target['host']=='127.0.0.1' and target['port']=='55446' and target['dbname']=='analysis_v2'
    assert target.get('hostaddr') in (None,'127.0.0.1')
    schema = 'admission_'+uuid4().hex
    def connect():
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row, options=f'-c search_path={schema}')
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            with connect() as c:
                migration = Path(__file__).resolve().parents[4]/'libs/schema/migrations-cloud/V202610021500__add_v2_execution_requests.sql'
                c.execute(migration.read_text(encoding='utf-8'))
            yield connect
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_concurrent_reservation_cannot_overwrite_input_or_rebind_event(connections):
    store = RequestStore(connections)
    request = dict(analysis_id='a'*32, kind='movement', etf_code='091160',
        analysis_at='2026-10-02T01:00:02Z', source=dict(event_id='event-1',
        event_type='PriceTriggerFired',trigger_id='trigger-1',generation=1))
    raw = canonical_input(request)
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(lambda _:store.reserve(request,'arn:fixed',raw), range(10)))
    assert len({r['created_at'] for r in rows})==1
    with pytest.raises(RequestConflict):
        other = request | {'etf_code':'102110'}
        store.reserve(other,'arn:fixed',canonical_input(other))
    with pytest.raises(RequestConflict):
        other = request | {'analysis_id':'b'*32}
        store.reserve(other,'arn:other',canonical_input(other))
    store.accept(request['analysis_id'],'arn:fixed')
    accepted = store.reserve(request,'arn:fixed',raw)['accepted_at']
    store.accept(request['analysis_id'],'arn:fixed')
    assert store.reserve(request,'arn:fixed',raw)['accepted_at']==accepted
    with connections() as c:
        assert c.execute('SELECT count(*) AS n FROM analysis_execution_requests').fetchone()['n']==1


@pytest.fixture
def source(connections):
    with connections() as c:
        c.execute('''CREATE TABLE instrument(instrument_id text,ticker text,market_code text,instrument_type text);
            CREATE TABLE dataset_commit_outbox(event_id text,event_type text,destination text,payload jsonb,generation int);
            CREATE TABLE minute_price_trigger(trigger_id text,entity_id text,session_id text,window_start timestamptz,
                generation int,trigger_kind text,close_price numeric,created_at timestamptz);
            CREATE TABLE price_daily(instrument_id text,trade_date date,close_price numeric,volume numeric,
                turnover_value numeric,price_basis text,available_at timestamptz);
            INSERT INTO instrument VALUES('instrument-etf','091160','XKRX','ETF');
            INSERT INTO minute_price_trigger VALUES
                ('original','091160','session','2026-10-02T00:59:00Z',1,'FIRE',10300,'2026-10-02T01:01:02Z'),
                ('before','091160','session','2026-10-02T00:55:00Z',1,'FIRE',10100,'2026-10-02T00:56:02Z'),
                ('later','091160','session','2026-10-02T01:00:00Z',1,'FIRE',99999,'2026-10-02T01:01:01Z');''')
        payload = dict(trigger_id='original',entity_id='091160',session_id='session',
                       window_start='2026-10-02T00:59:00+00:00',generation=1,close_price='10300')
        c.execute('INSERT INTO dataset_commit_outbox VALUES (%s,%s,%s,%s::jsonb,%s)',
                  ('PriceTriggerFired:original:0','PriceTriggerFired','price-explanation-realtime',json.dumps(payload),1))
        yield c, dict(event_id='PriceTriggerFired:original:0',event_type='PriceTriggerFired',payload=payload)


def test_event_identity_and_price_survive_late_delivery_and_newer_trigger(source):
    c, message = source
    request = load_event_request(c,json.dumps(message))
    assert request == load_event_request(c,json.dumps(message,sort_keys=True))
    assert request['source']['trigger_id']=='original'
    assert datetime.fromisoformat(request['analysis_at'])==datetime(2026,10,2,1,1,2,tzinfo=timezone.utc)
    data = dict(context={'etf_code':'091160','analysis_at':request['analysis_at']},
                trading_dates=['2026-10-01','2026-10-02'],source_instrument_ids={'091160':'instrument-etf'})
    load_prices(c,data,request=request)
    assert [r['price'] for r in data['price_snapshots']]==[10100,10300]


@pytest.mark.parametrize('change',[
    "UPDATE minute_price_trigger SET generation=2 WHERE trigger_id='original'",
    "UPDATE minute_price_trigger SET entity_id='102110' WHERE trigger_id='original'",
    "UPDATE minute_price_trigger SET trigger_kind='REVERT' WHERE trigger_id='original'",
    "UPDATE minute_price_trigger SET close_price=999 WHERE trigger_id='original'",
    "UPDATE dataset_commit_outbox SET generation=2",
    "UPDATE dataset_commit_outbox SET destination='other'",
    "DELETE FROM minute_price_trigger WHERE trigger_id='original'",
])
def test_mismatched_source_cannot_be_replaced_with_latest_price(source,change):
    c,message = source
    c.execute(change)
    with pytest.raises(ValueError):
        load_event_request(c,json.dumps(message))


def test_message_and_worker_cutoff_must_match_the_durable_event(source):
    c,message = source
    request = load_event_request(c,json.dumps(message))
    message['payload']['close_price']='999'
    with pytest.raises(ValueError):
        load_event_request(c,json.dumps(message))
    request['analysis_at']='2026-10-02T02:00:00Z'
    data = dict(context={'etf_code':'091160','analysis_at':request['analysis_at']},
                trading_dates=['2026-10-01','2026-10-02'],source_instrument_ids={'091160':'instrument-etf'})
    with pytest.raises(ValueError):
        load_prices(c,data,request=request)
