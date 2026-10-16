from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from edge_portfolio import store
from edge_portfolio.admin_app import app
from edge_portfolio.api import app as public_app
from test_store import isolated_db

ORIGIN = {'Origin': 'http://localhost:5180'}


def test_test_records_are_explicit_and_filter_counts_match_rows(client):
    from edge_portfolio import admin_store
    customer = store.submit('same-owner', 'customer', {'question': 'same question'})
    trial = store.submit('same-owner', 'test', {'question': 'same question'})
    admin_store.mark_tests([trial['id']], '20 scenarios / case 01')
    admin_store.mark_tests([trial['id']], '20 scenarios / case 01')
    for scope, expected in [('all', 2), ('customers', 1), ('tests', 1)]:
        rows = client.get(f'/api/portfolio/admin/records?kind=analyses&scope={scope}').json()
        summary = client.get('/api/portfolio/admin/overview').json()
        assert rows['total'] == len(rows['items']) == expected
        assert summary['queued'] == summary['today_jobs']['queued'] == 1
        assert sum(day['analyses'] for day in summary['daily']) == 1
    rows = client.get('/api/portfolio/admin/records?kind=analyses&scope=customers').json()
    assert rows['items'][0]['id'] == customer['id'], 'Same visitor/question does not identify a test'
    detail = client.get('/api/portfolio/admin/records/analyses/' + trial['id']).json()
    assert detail['test_label'] == '20 scenarios / case 01'
    with pytest.raises(ValueError, match='UNKNOWN_JOB'):
        admin_store.mark_tests([customer['id'], 'absent'], 'batch')
    assert admin_store.detail('analyses', customer['id'])['test_label'] is None
    assert client.get('/api/portfolio/admin/records?kind=analyses').json()['total'] == 1
    assert client.get('/api/portfolio/admin/records?kind=analyses&scope=invalid').status_code == 422


def test_customer_metrics_and_email_cleanup_keep_real_requests_from_mixed_visitors(client):
    from edge_portfolio import admin_store
    from edge_portfolio.contracts import DraftInput
    for owner in ('test-only', 'customer', 'mixed'):
        admin_store.heartbeat(owner)
        store.save_draft(str(uuid4()), owner, DraftInput(holdings=[]).model_dump(mode='json'))
    jobs = [store.submit(owner, key, {}) for owner, key in
            [('test-only', 'test'), ('customer', 'real'), ('mixed', 'test'), ('mixed', 'real')]]
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET state='succeeded'")
    for job, owner in zip(jobs, ('test-only', 'customer', 'mixed', 'mixed')):
        store.request_report(job['id'], owner, 'same@example.test')
    admin_store.mark_tests([jobs[0]['id'], jobs[2]['id']], 'confirmed')
    summary = admin_store.overview()
    assert summary['active_visitors'] == summary['today_visitors'] == 2
    assert sum(d['visitors'] for d in summary['daily']) == 2
    assert summary['today_jobs']['succeeded'] == 2
    assert summary['total_report_requests'] == 2
    assert summary['today_unique_emails'] == summary['total_unique_emails'] == 1
    assert admin_store.records('drafts', 7, '', 1)['total'] == 2
    assert admin_store.delete_test_reports() == 2
    assert admin_store.delete_test_reports() == 0
    with store.connect() as db:
        assert {r['job_id'] for r in db.execute('SELECT job_id FROM portfolio_report_requests')} == {jobs[1]['id'], jobs[3]['id']}


def test_report_records_inherit_test_label_without_classifying_drafts(client):
    from edge_portfolio import admin_store
    job = store.submit('visitor', 'test', {})
    store.claim()
    store.finish(job['id'], result={})
    store.request_report(job['id'], 'visitor', 'test@example.test')
    admin_store.mark_tests([job['id']], 'confirmed test')
    assert admin_store.records('reports', 7, '', 1, 'customers')['total'] == 0
    assert admin_store.records('reports', 7, '', 1, 'tests')['items'][0]['test_label'] == 'confirmed test'

@pytest.fixture
def client(isolated_db):
    with TestClient(app, base_url='http://localhost:18080') as client:
        yield client


def test_heartbeat_deduplicates_tabs_and_expires_activity(client):
    with TestClient(public_app) as visitor:
        assert visitor.post('/api/portfolio/presence', headers=ORIGIN).status_code == 401
        visitor.get('/api/portfolio/session')
        for _ in range(3):
            assert visitor.post('/api/portfolio/presence', headers=ORIGIN).status_code == 200
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
    overview = client.get('/api/portfolio/admin/overview').json()
    assert overview['today_visitors'] == 1
    assert overview['today_jobs']['queued'] == 1
    assert [row['visitors'] for row in overview['daily']] == [0, 0, 0, 0, 0, 1, 1]
    assert [row['analyses'] for row in overview['daily']] == [0, 0, 0, 0, 0, 1, 1]
    assert client.get('/api/portfolio/admin/records?kind=analyses&days=1').json()['total'] == 1


def test_email_metrics_count_normalized_addresses_and_saved_records_on_korean_day(client):
    empty = client.get('/api/portfolio/admin/overview').json()
    assert [empty[k] for k in ('today_unique_emails', 'total_unique_emails', 'total_report_requests')] == [0, 0, 0]
    with store.connect() as db:
        db.execute("INSERT INTO portfolio_jobs(id,owner,request_key,input,state) VALUES ('report','midnight','report','{}','succeeded')")
        # Two owners and repeated submissions must not imply two people or a click log.
        for owner, email, offset in [
            ('before', 'old@example.test', "-interval '1 microsecond'"),
            ('midnight', ' Reader@Example.test ', ""),
            ('same-email', 'reader@example.test', "+interval '1 second'"),
            ('other', 'other@example.test', "+interval '2 seconds'"),
            ('blank', '   ', "+interval '3 seconds'"),
            ('tomorrow', 'future@example.test', "+interval '1 day'"),
        ]:
            db.execute(f"""INSERT INTO portfolio_report_requests(job_id,owner,email,created_at)
                VALUES ('report',%s,%s,(date_trunc('day',now() AT TIME ZONE 'Asia/Seoul')
                    AT TIME ZONE 'Asia/Seoul'){offset})""", (owner, email))
    result = client.get('/api/portfolio/admin/overview').json()
    assert result['today_unique_emails'] == 2
    assert result['total_unique_emails'] == 4
    assert result['total_report_requests'] == 6
    assert store.request_report('report', 'midnight', 'reader@example.test') is True
    assert client.get('/api/portfolio/admin/overview').json()['total_report_requests'] == 6


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
