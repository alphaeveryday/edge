import hashlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from edge_portfolio import store
from edge_portfolio.api import app
from test_store import isolated_db

ORIGIN = {'Origin': 'http://localhost:5180'}
PASSWORD = 'test-only-random-admin-password-32'


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setenv('PORTFOLIO_ADMIN_PASSWORD_SHA256', hashlib.sha256(PASSWORD.encode()).hexdigest())
    with TestClient(app) as client:
        yield client


def login(client):
    return client.post('/api/portfolio/admin/login', json={'username': 'admin', 'password': PASSWORD}, headers=ORIGIN)


def test_admin_is_separate_from_visitors_and_logout_revokes_session(client):
    client.get('/api/portfolio/session')
    paths = ['/overview', '/records?kind=analyses', '/records?kind=drafts', '/records?kind=reports', '/records/analyses/unknown']
    for path in paths:
        assert client.get('/api/portfolio/admin' + path).status_code == 401
    assert login(client).status_code == 200
    assert client.get('/api/portfolio/admin/overview').status_code == 200
    token = client.cookies.get('edge_portfolio_admin')
    assert token
    assert client.post('/api/portfolio/admin/logout', headers=ORIGIN).status_code == 200
    client.cookies.set('edge_portfolio_admin', token)
    assert client.get('/api/portfolio/admin/overview').status_code == 401


def test_auth_fails_closed_on_rotation_expiration_and_foreign_origin(client, monkeypatch):
    assert client.post('/api/portfolio/admin/login', json={'username': 'admin', 'password': PASSWORD}, headers={'Origin': 'https://evil.example'}).status_code == 403
    response = login(client)
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert 'Path=/api/portfolio/admin' in response.headers['set-cookie']
    with store.connect() as db:
        db.execute("UPDATE portfolio_admin_sessions SET expires_at=now()-interval '1 second'")
    assert client.get('/api/portfolio/admin/overview').status_code == 401
    assert login(client).status_code == 200
    monkeypatch.setenv('PORTFOLIO_ADMIN_PASSWORD_SHA256', 'a' * 64)
    assert client.get('/api/portfolio/admin/overview').status_code == 401
    monkeypatch.delenv('PORTFOLIO_ADMIN_PASSWORD_SHA256')
    assert login(client).status_code == 503


def test_login_limit_is_shared_and_does_not_store_passwords(client):
    for _ in range(20):
        response = client.post('/api/portfolio/admin/login', json={'username': 'admin', 'password': 'wrong'}, headers=ORIGIN)
        assert response.status_code == 401
    assert login(client).status_code == 429
    with store.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM portfolio_admin_login_attempts').fetchone()['n'] == 20


def test_heartbeat_deduplicates_tabs_and_expires_activity(client):
    assert client.post('/api/portfolio/presence', headers=ORIGIN).status_code == 401
    client.get('/api/portfolio/session')
    for _ in range(3):
        assert client.post('/api/portfolio/presence', headers=ORIGIN).status_code == 200
    assert login(client).status_code == 200
    response = client.get('/api/portfolio/admin/overview')
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['active_visitors'] == 1
    assert response.json()['today_visitors'] == 1
    with store.connect() as db:
        db.execute("UPDATE portfolio_presence SET last_seen=now()-interval '121 seconds'")
    assert client.get('/api/portfolio/admin/overview').json()['active_visitors'] == 0


def test_dashboard_preserves_submitted_snapshot_and_latest_draft(client):
    from edge_portfolio.contracts import DraftInput
    draft_id = str(uuid4())
    payload = DraftInput(holdings=[], preferences={'note': '<script>private</script>'}).model_dump(mode='json')
    store.save_draft(draft_id, 'visitor', payload)
    job = store.submit('visitor', 'analysis', {**payload, 'draft_id': draft_id})
    store.claim()
    store.finish(job['id'], result={'sections': {}})
    store.submit('visitor', 'chat', {'question': 'followup'}, parent_id=job['id'])
    store.request_report(job['id'], 'visitor', 'private@example.com')
    payload['preferences']['note'] = 'edited draft'
    store.save_draft(draft_id, 'visitor', payload)
    login(client)
    overview = client.get('/api/portfolio/admin/overview').json()
    assert overview['today_jobs'] == {'queued': 0, 'running': 0, 'succeeded': 1, 'failed': 0}
    rows = client.get('/api/portfolio/admin/records?kind=analyses').json()
    assert rows['total'] == 1
    assert 'input' not in rows['items'][0]
    saved = client.get('/api/portfolio/admin/records/analyses/' + job['id']).json()
    assert saved['input']['preferences']['note'] == '<script>private</script>'
    draft = client.get('/api/portfolio/admin/records/drafts/' + draft_id).json()
    assert draft['input']['preferences']['note'] == 'edited draft'
    reports = client.get('/api/portfolio/admin/records?kind=reports').json()
    assert reports['items'][0]['email'] == 'private@example.com'
    assert client.get('/api/portfolio/admin/records?kind=analyses&state=failed').json()['total'] == 0
    assert client.get('/api/portfolio/admin/records?kind=analyses&page=2').json()['items'] == []
    assert client.get('/api/portfolio/admin/records?kind=not-a-table').status_code == 422
    assert client.get('/api/portfolio/admin/records/drafts/not-a-uuid').status_code == 404


def test_korean_day_boundary_and_daily_counts_do_not_mix_days(client):
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_visit_days VALUES ((now() AT TIME ZONE 'Asia/Seoul')::date,'today'),((now() AT TIME ZONE 'Asia/Seoul')::date-1,'yesterday')")
        db.execute("""INSERT INTO portfolio_jobs(id,owner,request_key,input,created_at) VALUES
            ('today','visitor','a','{}',date_trunc('day',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul'),
            ('yesterday','visitor','b','{}',(date_trunc('day',now() AT TIME ZONE 'Asia/Seoul') AT TIME ZONE 'Asia/Seoul')-interval '1 microsecond')""")
    login(client)
    overview = client.get('/api/portfolio/admin/overview').json()
    assert overview['today_visitors'] == 1
    assert overview['today_jobs']['queued'] == 1
    assert [row['visitors'] for row in overview['daily']] == [0, 0, 0, 0, 0, 1, 1]
    assert [row['analyses'] for row in overview['daily']] == [0, 0, 0, 0, 0, 1, 1]
    assert client.get('/api/portfolio/admin/records?kind=analyses&days=1').json()['total'] == 1


@pytest.mark.parametrize('change', ['save', 'delete'])
def test_draft_detail_cannot_mix_two_concurrent_saves(client, monkeypatch, change):
    from edge_portfolio import admin_store
    from edge_portfolio.contracts import DraftInput
    draft_id = str(uuid4())
    before = DraftInput(holdings=[], question='old question', preferences={'note': 'old note'}).model_dump(mode='json')
    after = DraftInput(holdings=[], question='new question', preferences={'note': 'new note'}).model_dump(mode='json')
    store.save_draft(draft_id, 'visitor', before)
    connect = store.connect
    changed = False
    class Connection:
        def __init__(self): self.db = connect()
        def __enter__(self): self.db.__enter__(); return self
        def __exit__(self, *args): return self.db.__exit__(*args)
        def execute(self, query, args=None):
            nonlocal changed
            cursor = self.db.execute(query, args)
            if query.startswith('SELECT payload FROM portfolio_input_portfolio') and not changed:
                changed = True
                if change == 'save':
                    store.save_draft(draft_id, 'visitor', after)
                else:
                    with connect() as writer:
                        writer.execute('DELETE FROM portfolio_input_drafts WHERE id=%s', (draft_id,))
            return cursor
    monkeypatch.setattr(store, 'connect', Connection)
    result = admin_store.detail('drafts', draft_id)['input']
    assert changed
    assert (result['question'], result['preferences']['note']) == ('old question', 'old note')
