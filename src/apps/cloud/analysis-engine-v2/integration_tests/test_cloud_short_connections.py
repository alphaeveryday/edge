"""Cloud analyses hold no database session while the model runs (ALPHA-1167).

The single-analysis workflow's slot row owns the run, so publication is refused once that slot is
reclaimed, and a dropped or refused connection is repeated inside the same attempt instead of paying
for the model again. The dashboard keeps its analysis ID lock and long connections.
"""
from functools import partial
from pathlib import Path
import os
from threading import Event, Thread
import time
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.analysis import service
from edge_analysis_v2.cloud import worker
from edge_analysis_v2.storage import database
from edge_analysis_v2.storage.publications import OwnershipLost, PublicationStore
from edge_analysis_v2.tools.fixture_data import make_fixture

OUTLOOK_ROWS = ('outlook_items', 'outlook_factors', 'outlook_conclusion_keywords', 'outlook_factor_metrics',
                'outlook_issue_items')
ANALYSIS_AT = '2026-09-21T10:00:00+09:00'


@pytest.fixture
def cloud(tmp_path):
    dsn = os.environ['V2_TEST_DSN']
    settings = conninfo_to_dict(dsn)
    if (settings.get('host'), settings.get('port'), settings.get('dbname')) != ('127.0.0.1', '55439', 'analysis_v2'):
        raise ValueError('Requires isolated local analysis_v2 database')
    tag = 'short-conn-' + uuid4().hex[:12]
    faults = {'refuse': 0, 'kill_on': None, 'pause': None, 'raise_on': None}
    created = []

    def admin():
        return psycopg.connect(dsn, autocommit=True)

    class FaultCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            text = query if isinstance(query, str) else ''
            if faults['kill_on'] and faults['kill_on'] in text:
                faults['kill_on'] = None  # the server ends this session in the middle of the transaction
                with admin() as other:
                    other.execute('SELECT pg_terminate_backend(%s)', (self.connection.info.backend_pid,))
            if faults['raise_on'] and faults['raise_on'][0] in text:
                error, faults['raise_on'] = faults['raise_on'][1], None
                raise error
            pause = faults['pause']
            if pause and pause['match'] in text and not pause['entered'].is_set():
                pause['entered'].set()
                pause['release'].wait(10)
            return super().execute(query, params, **kwargs)

    def factory():
        if faults['refuse']:
            faults['refuse'] -= 1
            raise database.ResultDatabaseError('refused', transient=True)
        connection = psycopg.connect(dsn, autocommit=True, application_name=tag)
        connection.execute('SET ROLE edge_analysis_v2_writer')  # the real writer grants, not superuser
        connection.cursor_factory = FaultCursor
        return connection

    def sessions():
        with admin() as other:
            return other.execute('SELECT count(*) FROM pg_stat_activity WHERE application_name=%s', (tag,)).fetchone()[0]

    def owned(identity, kind='outlook'):
        owner = 'arn:aws:states:ap-northeast-2:1:execution:edge-dev-analysis-v2:' + identity
        with admin() as other:
            other.execute('INSERT INTO analysis_execution_slots(execution_arn,started_by,request_key) VALUES (%s,%s,%s)',
                          (owner, identity[:32], kind + ':' + identity))
        return owner

    def rows(identity):
        with admin() as other:
            return {table: other.execute(f'SELECT count(*) FROM {table} WHERE analysis_id=%s', (identity,)).fetchone()[0]
                    for table in OUTLOOK_ROWS}

    def status(identity, kind='outlook'):
        with admin() as other:
            row = other.execute(f'SELECT status, error_message FROM {kind}_analyses WHERE analysis_id=%s', (identity,)).fetchone()
            return row and row[0], row and row[1]

    def execute(model, *, owner, identity=None, kind='outlook'):
        identity = identity or uuid4().hex
        created.append(identity)
        return service.execute_request(kind=kind, fixture=make_fixture(analysis_at=ANALYSIS_AT), connection_factory=factory,
                                       key='fake-key', artifacts=tmp_path/identity, analysis_id=identity,
                                       model_call=model, owner=owner), identity

    namespace = type('Cloud', (), {})()
    namespace.__dict__.update(dsn=dsn, faults=faults, factory=factory, sessions=sessions, owned=owned, rows=rows,
                              status=status, execute=execute, admin=admin, created=created, tmp=tmp_path)
    database.STATS.reset()
    yield namespace
    with admin() as other:
        for identity in created:
            other.execute('DELETE FROM tenant_delivery WHERE movement_analysis_id=%s', (identity,))
            other.execute('DELETE FROM tool_runs WHERE movement_analysis_id=%s OR outlook_analysis_id=%s', (identity, identity))
            for table in OUTLOOK_ROWS + ('movement_items', 'movement_analyses', 'outlook_analyses'):
                other.execute(f'DELETE FROM {table} WHERE analysis_id=%s', (identity,))
            other.execute('DELETE FROM analysis_execution_slots WHERE started_by=%s', (identity[:32],))


def news(kwargs):
    return kwargs['call']('get_issue_evidence', {'news_ids': [kwargs['initial']['news'][0]['news_id']],
                                                 'include_body': False})['tool_run_id']


def outlook_model(calls, during=None):
    """Fake model: two evidence calls and one body edit around a pause standing for the model wait."""
    async def model(**kwargs):
        calls.append(kwargs['initial'].get('previous_analysis'))
        reference = news(kwargs)
        kwargs['call']('write_outlook_body', {'title': '공급 계약 확인', 'items': [
            {'id': 'supply', 'title_keyword': '계약 물량', 'sentences': ['계약 물량을 확보했어요.'], 'tool_run_ids': [reference]}]})
        if during:
            during()
        news(kwargs)
        return {'outlook': {'direction': '상승'}, 'summary_card': {'title': '계약 물량', 'summary': '공급 계획을 확인해요.'},
                'factors': [{'type': factor, 'sticker': '중립', 'sentence': '관측 자료를 확인했어요.'}
                            for factor in ('이슈', '차트', '매크로', '밸류', '수급')],
                'conclusion': {'title': '공급 계획', 'supports': [{'label': '계약', 'tool_run_ids': [reference]}],
                               'burdens': [], 'sentence': '계약 이행을 확인해요.'},
                'issue_detail': {'headline': '공급 일정 확인', 'items': [
                    {'title_keyword': '공급 계약', 'sentence': '물량을 확보했어요.', 'sentiment': 'positive', 'tool_run_ids': [reference]}]}}
    return model


def baseline_rows(cloud):
    identity = uuid4().hex
    cloud.execute(outlook_model([]), owner=cloud.owned(identity), identity=identity)
    return cloud.rows(identity)


def test_cloud_run_holds_no_session_while_the_model_waits(cloud):
    seen = {}
    identity = uuid4().hex
    screen, _ = cloud.execute(outlook_model([], during=lambda: seen.update(cloud=cloud.sessions())),
                              owner=cloud.owned(identity), identity=identity)
    assert seen['cloud'] == 0 and screen['detail']['items']
    # The dashboard path keeps its two connections (work with the analysis ID lock, and audit) for the whole run.
    cloud.execute(outlook_model([], during=lambda: seen.update(dashboard=cloud.sessions())), owner=None)
    assert seen['dashboard'] == 2


def test_reclaimed_slot_refuses_a_new_publication_and_leaves_no_rows(cloud):
    identity = uuid4().hex
    owner = cloud.owned(identity)
    def reclaim():
        with cloud.admin() as other:
            other.execute('DELETE FROM analysis_execution_slots WHERE execution_arn=%s', (owner,))
    with pytest.raises(OwnershipLost):
        cloud.execute(outlook_model([], during=reclaim), owner=owner, identity=identity)
    state, error = cloud.status(identity)
    assert state == 'failed' and 'reclaimed' in error
    assert set(cloud.rows(identity).values()) == {0}


def test_reclaim_waits_for_a_publication_holding_the_slot_and_follows_its_commit(cloud):
    identity = uuid4().hex
    owner = cloud.owned(identity)
    pause = {'match': 'INSERT INTO outlook_items', 'entered': Event(), 'release': Event()}
    cloud.faults['pause'] = pause
    done = {}
    def publish():
        cloud.execute(outlook_model([]), owner=owner, identity=identity)
        done['published'] = time.monotonic()
    def reclaim():
        with psycopg.connect(cloud.dsn, autocommit=True) as other:
            other.execute('SET ROLE edge_analysis_v2_writer')
            other.execute('DELETE FROM analysis_execution_slots WHERE execution_arn=%s', (owner,))
        done['reclaimed'] = time.monotonic()
    first = Thread(target=publish)
    first.start()
    assert pause['entered'].wait(10)  # the publication transaction holds the slot row
    second = Thread(target=reclaim)
    second.start()
    second.join(1.5)
    blocked = second.is_alive()
    pause['release'].set()
    first.join(10)
    second.join(10)
    assert blocked and done['reclaimed'] >= done['published'] - 0.5
    assert cloud.status(identity)[0] == 'completed' and cloud.rows(identity)['outlook_items'] > 0


def test_session_ended_mid_publication_leaves_nothing_partial_and_the_repeat_publishes_once(cloud):
    expected = baseline_rows(cloud)
    calls = []
    identity = uuid4().hex
    cloud.faults['kill_on'] = 'INSERT INTO outlook_items'
    cloud.execute(outlook_model(calls), owner=cloud.owned(identity), identity=identity)
    assert cloud.status(identity)[0] == 'completed' and cloud.rows(identity) == expected
    assert len(calls) == 1 and database.STATS.retries >= 1


def test_lost_commit_reply_is_recovered_without_duplicates_or_a_second_model_call(cloud, monkeypatch):
    expected = baseline_rows(cloud)
    original = PublicationStore.save_outlook
    lost = {'armed': True}
    def save(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if lost['armed']:
            lost['armed'] = False
            raise psycopg.OperationalError('reply lost after commit')
        return result
    monkeypatch.setattr(PublicationStore, 'save_outlook', save)
    calls = []
    identity = uuid4().hex
    cloud.execute(outlook_model(calls), owner=cloud.owned(identity), identity=identity)
    assert not lost['armed'] and len(calls) == 1
    assert cloud.status(identity)[0] == 'completed' and cloud.rows(identity) == expected


def test_refused_connections_during_the_run_do_not_call_the_model_again(cloud):
    calls = []
    identity = uuid4().hex
    def outage():
        cloud.faults['refuse'] = 3  # the next three connections fail: the tool record and the rest wait
    cloud.execute(outlook_model(calls, during=outage), owner=cloud.owned(identity), identity=identity)
    assert len(calls) == 1 and cloud.status(identity)[0] == 'completed'
    assert database.STATS.retries == 3 and cloud.faults['refuse'] == 0


@pytest.mark.parametrize('error', [psycopg.errors.QueryCanceled('canceling statement due to statement timeout'),
                                   psycopg.errors.CheckViolation('new row violates check constraint')])
def test_database_errors_that_are_not_connection_failures_are_not_repeated(cloud, error):
    # A statement timeout or a constraint violation repeats identically; retrying would only hide it.
    identity = uuid4().hex
    cloud.faults['raise_on'] = ('INSERT INTO outlook_items', error)
    calls = []
    with pytest.raises(type(error)):
        cloud.execute(outlook_model(calls), owner=cloud.owned(identity), identity=identity)
    assert cloud.status(identity)[0] == 'failed' and database.STATS.retries == 0 and len(calls) == 1
    assert set(cloud.rows(identity).values()) == {0}


def test_dashboard_failure_record_never_hides_the_original_error(cloud):
    identity = uuid4().hex
    cloud.faults['kill_on'] = 'INSERT INTO outlook_items'
    # The killed work connection cannot record the failure; the cause must still be the dropped session.
    with pytest.raises(psycopg.OperationalError):
        cloud.execute(outlook_model([]), owner=None, identity=identity)
    assert cloud.status(identity)[0] == 'running'


def test_dashboard_keeps_the_analysis_id_lock(cloud):
    identity = uuid4().hex
    inside, release = Event(), Event()
    def hold():
        inside.set()
        release.wait(10)
    first = Thread(target=lambda: cloud.execute(outlook_model([], during=hold), owner=None, identity=identity))
    first.start()
    assert inside.wait(10)
    with pytest.raises(ValueError, match='already executing'):
        cloud.execute(outlook_model([]), owner=None, identity=identity)
    release.set()
    first.join(10)
    assert cloud.status(identity)[0] == 'completed'


class LocalS3:
    def __init__(self, root):
        self.root = Path(root)

    def put_object(self, *, Bucket, Key, Body, IfNoneMatch=None, **_):
        path = self.root/Key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Body)
        return {}


class LocalSession:
    def __init__(self, root):
        self.s3 = LocalS3(root)

    def client(self, name):
        assert name == 's3'
        return self.s3


def test_movement_published_but_interrupted_before_delivery_is_delivered_by_the_repeat_without_the_model(cloud, monkeypatch):
    identity = uuid4().hex
    owner = cloud.owned(identity, kind='movement')
    cloud.created.append(identity)
    with cloud.admin() as other:
        tenant = other.execute("INSERT INTO tenant(tenant_name,environment,status) VALUES (%s,'DEV','ACTIVE') RETURNING tenant_id",
                               ('short-conn-' + identity[:12],)).fetchone()[0]
    calls = []
    async def model(**kwargs):
        calls.append(1)
        reference = news(kwargs)
        return {'new_items': [{'candidate_id': 'new', 'type': '이슈', 'title_keyword': '계약', 'sentence': '물량을 확보했어요.',
                               'sentiment': 'positive', 'tool_run_ids': [reference]}],
                'selected_item_ids': ['new'], 'summary': '공급 계약을 확인해요.'}
    interrupted = {'armed': True}
    deliver = worker.enqueue_movement
    def enqueue(connection, analysis_id):
        if interrupted['armed']:
            interrupted['armed'] = False
            raise RuntimeError('task stopped after publication')
        return deliver(connection, analysis_id)
    monkeypatch.setattr(worker, 'connect_results', lambda ca_path, **_: cloud.factory())
    monkeypatch.setattr(worker, 'load_sources', lambda *_: make_fixture(analysis_at=ANALYSIS_AT))
    monkeypatch.setattr(worker, 'load_research_observations', lambda connection, source: source)
    monkeypatch.setattr(worker, 'execute_request', partial(service.execute_request, model_call=model))
    monkeypatch.setattr(worker, 'enqueue_movement', enqueue)
    request = {'analysis_id': identity, 'kind': 'movement', 'etf_code': '091160', 'analysis_at': ANALYSIS_AT}
    run = lambda: worker.run(request, bucket='local', ca_path=Path('.'), folder=cloud.tmp/identity, key='fake-key',
                             model='stub', session=LocalSession(cloud.tmp/'s3'), owner=owner)
    try:
        with pytest.raises(RuntimeError):
            run()
        with cloud.admin() as other:
            assert other.execute('SELECT count(*) FROM tenant_delivery WHERE movement_analysis_id=%s', (identity,)).fetchone()[0] == 0
        assert cloud.status(identity, 'movement')[0] == 'completed'
        run()  # the same request again: delivery only, through the existing recovery path
        with cloud.admin() as other:
            delivered = other.execute('SELECT count(*) FROM tenant_delivery WHERE movement_analysis_id=%s AND tenant_id=%s',
                                      (identity, tenant)).fetchone()[0]
        assert delivered == 1 and len(calls) == 1
    finally:
        with cloud.admin() as other:
            other.execute('DELETE FROM tenant_delivery WHERE tenant_id=%s', (tenant,))
            other.execute('DELETE FROM tenant WHERE tenant_id=%s', (tenant,))
