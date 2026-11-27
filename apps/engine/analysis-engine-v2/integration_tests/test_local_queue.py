"""Outbox claim, close and local run SQL on a disposable, loopback-only PostgreSQL.

The statements run as edge_analysis_v2_writer with only the grants of the claim migration,
so a missing privilege or a wider one than intended fails here rather than on the host.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
from threading import Event
from unittest.mock import Mock
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.cloud import local
from edge_analysis_v2.cloud.consumer import poll_once
from edge_analysis_v2.cloud.local import DESTINATION, LOCAL, LocalAdmission, LocalRunner, OutboxQueue
from edge_analysis_v2.storage.requests import RequestStore

MIGRATIONS = Path(__file__).resolve().parents[4]/'libs/schema/migrations-cloud'
REQUEST = dict(analysis_id='a'*32, kind='movement', etf_code='091160', analysis_at='2026-12-01T00:30:00+00:00',
               source=dict(event_id='fire-1', event_type='PriceTriggerFired', trigger_id='trigger-1', generation=1))


@pytest.fixture
def database():
    dsn = os.environ['V2_ADMISSION_TEST_DSN']
    target = conninfo_to_dict(dsn)
    assert target['host']=='127.0.0.1' and target['port']=='55446' and target['dbname']=='analysis_v2'
    assert target.get('hostaddr') in (None,'127.0.0.1')
    schema = 'local_queue_'+uuid4().hex
    def connect(role=None):
        connection = psycopg.connect(dsn, autocommit=True, options=f'-c search_path={schema}')
        if role:
            connection.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
        return connection
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            with connect() as c:
                for name in ('V202607311410__add_dataset_commit_outbox.sql', 'V202610021500__add_v2_execution_requests.sql',
                             'V202611271200__allow_v2_outbox_claim.sql'):
                    # The disposable schema stands in for public.
                    c.execute((MIGRATIONS/name).read_text(encoding='utf-8').replace('SET search_path TO public;', ''))
                c.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO edge_analysis_v2_writer').format(sql.Identifier(schema)))
            yield connect
        finally:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def insert(connect, event_id, *, event_type='PriceTriggerFired', destination=DESTINATION, payload=None):
    with connect() as c:
        c.execute('''INSERT INTO dataset_commit_outbox(event_id,event_type,destination,aggregate_id,generation,payload)
            VALUES (%s,%s,%s,%s,1,%s::jsonb)''', (event_id, event_type, destination, event_id, json.dumps(payload or {})))


def row(connect, event_id):
    with connect() as c:
        return c.execute('''SELECT status,attempt_count,claimed_by,last_error,published_at IS NOT NULL
            FROM dataset_commit_outbox WHERE event_id=%s''', (event_id,)).fetchone()


def queue(connect):
    stop = Mock()   # an empty poll must not really wait
    return OutboxQueue(lambda: connect('edge_analysis_v2_writer'), stop)


def receive(q, limit=1):
    return q.receive_message(QueueUrl=DESTINATION, MaxNumberOfMessages=limit, WaitTimeSeconds=20)['Messages']


def expire(connect, event_id):
    with connect() as c:
        c.execute("UPDATE dataset_commit_outbox SET claim_expires_at=now()-interval '1 second' WHERE event_id=%s", (event_id,))


def test_concurrent_consumers_never_receive_the_same_event(database):
    """Two admissions of one event are harmless, but two deliveries of every event double the load."""
    for index in range(40):
        insert(database, f'event-{index:02d}')
    def drain(_):
        q, seen = queue(database), []
        while messages := receive(q, 3):
            seen += [json.loads(message['Body'])['event_id'] for message in messages]
        return seen
    with ThreadPoolExecutor(8) as pool:
        received = [event for seen in pool.map(drain, range(8)) for event in seen]
    assert sorted(received) == [f'event-{index:02d}' for index in range(40)]


def test_only_this_destination_is_claimed_and_a_relay_hold_is_respected(database):
    insert(database, 'news-1', event_type='NewsExtractionRequested', destination='news-extraction-realtime')
    insert(database, 'fire-1')
    with database() as c:   # the Relay claims with the same two columns
        c.execute("UPDATE dataset_commit_outbox SET claimed_by='relay-1', claim_expires_at=now()+interval '150 seconds' WHERE event_id='fire-1'")
    assert receive(queue(database), 10) == []
    assert row(database, 'news-1')[:3] == ('NEW', 0, None)
    expire(database, 'fire-1')
    assert [json.loads(m['Body'])['event_id'] for m in receive(queue(database), 10)] == ['fire-1']


def test_a_late_acknowledgement_cannot_close_the_next_delivery(database):
    insert(database, 'fire-1')
    first, second = queue(database), queue(database)
    stale = receive(first)[0]
    assert receive(second) == []          # still held
    expire(database, 'fire-1')
    current = receive(second)[0]
    first.delete_message(QueueUrl=DESTINATION, ReceiptHandle=stale['ReceiptHandle'])
    assert row(database, 'fire-1')[0] == 'NEW'
    second.delete_message(QueueUrl=DESTINATION, ReceiptHandle=current['ReceiptHandle'])
    assert row(database, 'fire-1')[0::4] == ('PUBLISHED', True)


def test_an_event_that_is_never_admitted_ends_dead_with_a_reason(database):
    """Deliveries are counted when claimed, so a consumer that dies before recording is counted too."""
    insert(database, 'fire-1')
    q = queue(database)
    for delivery in range(1, local.MAX_RECEIVES + 1):
        assert len(receive(q)) == 1
        assert row(database, 'fire-1')[:2] == ('NEW', delivery)
        expire(database, 'fire-1')
    assert receive(q) == []
    assert row(database, 'fire-1')[0::3] == ('DEAD', 'DB_MAX_RECEIVE')
    assert receive(q) == []


def test_the_writer_can_close_a_delivery_but_not_rewrite_the_event(database):
    insert(database, 'fire-1')
    with database('edge_analysis_v2_writer') as c:
        for statement in ("UPDATE dataset_commit_outbox SET payload='{}'::jsonb", "UPDATE dataset_commit_outbox SET destination='x'",
                          'DELETE FROM dataset_commit_outbox', "INSERT INTO dataset_commit_outbox(event_id) VALUES ('x')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(statement)


def test_an_event_is_admitted_once_then_run_once(database, tmp_path, monkeypatch):
    """The whole local path: outbox row, unchanged admission loop, pending request, one child process."""
    monkeypatch.setattr(local, 'ARTIFACTS', tmp_path)
    insert(database, 'fire-1', payload={'entity_id': '091160'})
    insert(database, 'revert-1', event_type='ExposureReverted', payload={'entity_id': '091160'})
    store, retract = RequestStore(database), Mock()
    def load(raw):
        event = json.loads(raw)
        return event if event['event_type'] == 'ExposureReverted' else REQUEST
    q, stop = queue(database), Event()
    for _ in range(3):
        poll_once(q, DESTINATION, load, LocalAdmission(store), stop, retract)
    assert row(database, 'fire-1')[0] == row(database, 'revert-1')[0] == 'PUBLISHED'
    retract.assert_called_once()

    class Process:
        code = None
        def poll(self):
            return self.code
    spawned = []
    runner = LocalRunner(database, store, 2, spawn=lambda raw, slots: spawned.append(json.loads(raw)) or Process.instance,
                         now=lambda: datetime.fromisoformat(REQUEST['analysis_at']))
    Process.instance = Process()
    runner.tick()
    runner.tick()
    assert [item['analysis_id'] for item in spawned] == [REQUEST['analysis_id']]
    assert spawned[0]['source']['event_id'] == 'fire-1'
    Process.instance.code = 1
    runner.tick()
    runner.tick()
    assert len(spawned) == 1
    with database() as c:
        assert c.execute('SELECT accepted_at IS NOT NULL, execution_arn FROM analysis_execution_requests').fetchall() == [
            (True, LOCAL + REQUEST['analysis_id'])]


def test_pending_requests_run_oldest_first_and_only_local_ones(database, tmp_path, monkeypatch):
    """A burst waits in line; picking the newest first would starve the first movers of the day.
    Requests admitted through the AWS workflow share the table and are not this runner's to run."""
    monkeypatch.setattr(local, 'ARTIFACTS', tmp_path)
    rows = [('1'*32, LOCAL+'1'*32, '09:03', '000001'), ('2'*32, LOCAL+'2'*32, '09:01', '000002'),
            ('3'*32, LOCAL+'3'*32, '09:02', '000003'), ('4'*32, 'arn:aws:states:x:4', '09:00', '000004')]
    with database() as c:
        for identity, arn, minute, etf in rows:
            item = dict(analysis_id=identity, kind='movement', etf_code=etf, analysis_at=f'2026-12-01T{minute}:00+09:00')
            c.execute('''INSERT INTO analysis_execution_requests(analysis_id,input_json,execution_arn,created_at)
                VALUES (%s,%s,%s,%s)''', (identity, json.dumps(item), arn, f'2026-12-01T{minute}:00+09:00'))

    class Done:
        def poll(self):
            return 0
    spawned = []
    runner = LocalRunner(database, RequestStore(database), 1, now=lambda: datetime.fromisoformat('2026-12-01T10:00:00+09:00'),
                         spawn=lambda raw, slots: spawned.append(json.loads(raw)['analysis_id']) or Done())
    for _ in range(6):
        runner.tick()
    assert spawned == ['2'*32, '3'*32, '1'*32]
