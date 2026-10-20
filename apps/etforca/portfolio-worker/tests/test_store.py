import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from edge_portfolio import store


@pytest.fixture
def isolated_db(monkeypatch):
    if not os.getenv("PORTFOLIO_DATABASE_URL"):
        pytest.skip("Dedicated integration database required")
    schema = "test_" + uuid4().hex
    original = store.connect
    with original() as db:
        db.execute(f'CREATE SCHEMA "{schema}"')
    def connect():
        db = original()
        db.execute(f'SET search_path TO "{schema}"')
        db.commit()
        return db
    monkeypatch.setattr(store, "connect", connect)
    store.initialize()
    yield
    with original() as db:
        db.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_duplicate_submission_is_one_job_and_other_owner_cannot_read(isolated_db):
    with ThreadPoolExecutor(2) as pool:
        jobs = list(pool.map(lambda _: store.submit("owner", "same", {"value": 1}), range(2)))
    assert jobs[0]["id"] == jobs[1]["id"]
    assert store.read(jobs[0]["id"], "other") is None
    with pytest.raises(ValueError, match="CONFLICT"):
        store.submit("owner", "same", {"value": 2})


def test_same_deployment_initialization_never_relocks_active_analysis_tables(isolated_db, monkeypatch, tmp_path):
    deployment = tmp_path / 'deployment.json'
    deployment.write_text('{"version":"' + 'a' * 40 + '"}')
    monkeypatch.setattr(store, 'DEPLOYMENT', deployment, raising=False)
    store.initialize()
    with store.connect() as active_analysis:
        active_analysis.execute('LOCK TABLE portfolio_jobs IN ACCESS SHARE MODE')
        store.initialize()  # Repeated ALTER TABLE would block here and time out.


def test_new_deployment_initializes_once_and_failed_initialization_is_not_recorded(isolated_db, monkeypatch, tmp_path):
    from edge_portfolio import dashboard
    deployment = tmp_path / 'deployment.json'
    monkeypatch.setattr(store, 'DEPLOYMENT', deployment, raising=False)
    calls = []
    original = dashboard.initialize
    def initialize(db):
        calls.append(True)
        original(db)
    monkeypatch.setattr(dashboard, 'initialize', initialize)
    deployment.write_text('{"version":"' + 'a' * 40 + '"}')
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: store.initialize(), range(2)))
    store.initialize()
    assert len(calls) == 1
    deployment.write_text('{"version":"' + 'b' * 40 + '"}')
    def fail(db):
        raise RuntimeError('migration failed')
    monkeypatch.setattr(dashboard, 'initialize', fail)
    with pytest.raises(RuntimeError, match='migration failed'):
        store.initialize()
    monkeypatch.setattr(dashboard, 'initialize', initialize)
    store.initialize()
    store.initialize()
    assert len(calls) == 2


def test_service_failures_do_not_consume_customer_retry_allowance(isolated_db):
    for index in range(4):
        job = store.submit('retry-owner', str(index), {'question': 'same input'}, reuse=True)
        assert store.claim()['id'] == job['id']
        store.finish(job['id'], failure='ANALYSIS_FAILED')
    retried = store.submit('retry-owner', 'retry', {'question': 'same input'}, reuse=True)
    assert retried['state'] == 'queued'
    assert store.submit('retry-owner', 'retry', {'question': 'same input'}, reuse=True)['id'] == retried['id']


def test_two_workers_cannot_claim_same_job(isolated_db):
    store.submit("owner", "same", {})
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: store.claim(), range(2)))
    assert sum(row is not None for row in claimed) == 1


def test_shutdown_release_preserves_job_and_cannot_release_a_newer_claim(isolated_db):
    original = store.submit('owner', 'same', {})
    claimed = store.claim()
    assert store.release_claim(claimed['id'], claimed['started_at'])
    queued = store.read(original['id'], 'owner')
    assert queued['state'] == 'queued'
    assert queued['created_at'] == original['created_at']
    again = store.claim()
    assert again['id'] == original['id']
    assert not store.release_claim(claimed['id'], claimed['started_at'])
    store.stage(again['id'], 'question')
    assert not store.release_claim(again['id'], again['started_at'])


def test_crashed_worker_result_cannot_overwrite_expiration(isolated_db):
    job = store.submit("owner", "same", {})
    store.claim()
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET started_at=now()-interval '12 minutes'")
    assert store.read(job["id"], "owner")["failure"] == "WORKER_INTERRUPTED"
    assert store.finish(job["id"], result={"late": True}) is False


def test_interrupted_job_retains_last_checkpoint_without_becoming_a_completed_report(isolated_db):
    job = store.submit('owner', 'checkpoint', {})
    store.claim()
    store.checkpoint(job['id'], {'diagnostics': {'events': [{'event': 'tool_started', 'tool': 'fetch_source'}]}})
    store.checkpoint(job['id'], {'partial_report': {'sections': {'question': {'summary': 'saved'}}}})
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET started_at=now()-interval '12 minutes' WHERE id=%s", (job['id'],))
    failed = store.read(job['id'], 'owner')
    assert failed['state'] == 'failed'
    assert failed['failure'] == 'WORKER_INTERRUPTED'
    assert failed['result']['diagnostics']['events'][0]['tool'] == 'fetch_source'
    assert failed['result']['partial_report']['sections']['question']['summary'] == 'saved'
    assert not store.checkpoint(job['id'], {'late': True})


@pytest.mark.parametrize('kind,age,state', [
    ('analysis', 7, 'running'), ('analysis', 10, 'running'), ('analysis', 12, 'failed'),
    ('chat', 5, 'running'), ('chat', 7, 'running'), ('chat', 10, 'running'), ('chat', 12, 'failed'),
])
def test_polling_does_not_expire_analysis_before_its_deadline(isolated_db, kind, age, state):
    job = store.submit('owner', 'deadline', {})
    store.claim()
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET kind=%s,started_at=now()-%s*interval '1 minute' WHERE id=%s",
                   (kind, age, job['id']))
    assert store.read(job['id'], 'owner')['state'] == state
    if state == 'running':
        assert store.finish(job['id'], result={'completed': True}), 'Polling must not discard a valid late result'


def test_slow_checkpoint_transaction_releases_lock_before_final_publication(isolated_db):
    import time
    import psycopg
    job = store.submit('owner', 'slow-checkpoint', {})
    store.claim()
    with store.connect() as db:
        db.execute("""CREATE FUNCTION delay_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.state='running' AND NEW.result IS NOT NULL THEN PERFORM pg_sleep(8); END IF;
            RETURN NEW; END $$""")
        db.execute('CREATE TRIGGER delay_checkpoint AFTER UPDATE ON portfolio_jobs FOR EACH ROW EXECUTE FUNCTION delay_checkpoint()')
    with ThreadPoolExecutor(1) as pool:
        saving = pool.submit(store.checkpoint, job['id'], {'diagnostics': {}})
        # Wait until the checkpoint UPDATE really holds the job row lock.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with store.connect() as db:
                blocked = db.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event='PgSleep' AND query LIKE 'UPDATE portfolio_jobs SET result=COALESCE%'").fetchone()['n']
            if blocked:
                break
            time.sleep(.01)
        assert blocked
        assert store.finish(job['id'], result={'complete': True})
        with pytest.raises(psycopg.errors.QueryCanceled):
            saving.result()
    assert store.read(job['id'], 'owner')['result'] == {'complete': True}


def test_api_rejects_other_origins_and_preserves_idempotency(isolated_db):
    from fastapi.testclient import TestClient
    from edge_portfolio.api import app
    with TestClient(app) as client:
        client.get('/api/portfolio/session')
        payload = {"holdings": [{"name": "VOO", "amount_krw": 100}], "horizon": "10년 이상",
                   "preferences": {"contribution": "이미 다 매수한 상태에요", "interests": ["성장"]}}
        headers = {"Idempotency-Key": str(uuid4()), "Origin": "https://untrusted.example"}
        assert client.post('/api/portfolio/analyses', json=payload, headers=headers).status_code == 403
        headers['Origin'] = 'http://localhost:5180'
        response = client.post('/api/portfolio/analyses', json=payload, headers=headers)
        assert response.status_code == 202
        assert client.post('/api/portfolio/analyses', json=payload, headers=headers).json()['id'] == response.json()['id']


def test_chat_quota_is_atomic_and_failure_refunds_it(isolated_db):
    parent = store.submit('owner', 'parent', {})
    store.claim(); store.finish(parent['id'], result={})
    def submit_chat(key):
        try:
            return store.submit('owner', key, {'question': 'question'}, parent_id=parent['id'])
        except ValueError as error:
            return str(error)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit_chat, ['chat1', 'chat2']))
    assert results.count('CHAT_LIMIT') == 1
    chat = next(r for r in results if isinstance(r, dict))
    store.claim(); store.finish(chat['id'], failure='ANALYSIS_FAILED')
    retry = submit_chat('retry')
    assert isinstance(retry, dict)
    store.claim(); store.finish(retry['id'], failure='ANALYSIS_FAILED')
    assert isinstance(submit_chat('second-retry'), dict)


def test_email_is_bound_to_owned_completed_analysis(isolated_db):
    parent = store.submit('owner', 'parent', {})
    store.claim(); store.finish(parent['id'], result={})
    assert store.request_report(parent['id'], 'other', 'reader@example.com') is False
    assert store.request_report(parent['id'], 'owner', 'reader@example.com') is True


def test_service_recovery_does_not_consume_the_commercial_question_and_is_bounded(isolated_db):
    parent = store.submit('owner', 'parent', {})
    store.claim(); store.finish(parent['id'], result={})
    for number in range(2):
        recovery = store.submit('owner', f'recovery-{number}', {'question': 'finish the missing answer', 'mode': 'recovery'}, parent_id=parent['id'])
        assert store.submit('owner', f'recovery-{number}', recovery['input'], parent_id=parent['id'])['id'] == recovery['id']
        store.claim(); store.finish(recovery['id'], result={})
    with pytest.raises(ValueError, match='RECOVERY_LIMIT'):
        store.submit('owner', 'third-recovery', {'question': 'again', 'mode': 'recovery'}, parent_id=parent['id'])
    normal = store.submit('owner', 'normal', {'question': 'new question'}, parent_id=parent['id'])
    store.claim(); store.finish(normal['id'], result={})
    with pytest.raises(ValueError, match='CHAT_LIMIT'):
        store.submit('owner', 'second-normal', {'question': 'again'}, parent_id=parent['id'])


def test_failed_parent_can_be_recovered_only_by_its_owner_and_failed_attempts_are_refunded(isolated_db):
    parent = store.submit('owner', 'parent', {})
    store.claim(); store.finish(parent['id'], failure='WORKER_INTERRUPTED')
    with pytest.raises(ValueError, match='PARENT_NOT_FOUND'):
        store.submit('other', 'wrong-owner', {'question': 'retry', 'mode': 'recovery'}, parent_id=parent['id'])
    with pytest.raises(ValueError, match='PARENT_NOT_FOUND'):
        store.submit('owner', 'normal', {'question': 'question'}, parent_id=parent['id'])
    for number in range(3):
        recovery = store.submit('owner', f'recovery-{number}', {'question': 'retry', 'mode': 'recovery'}, parent_id=parent['id'])
        store.claim(); store.finish(recovery['id'], failure='MODEL_TIMEOUT')


def test_draft_sections_are_saved_atomically_and_private(isolated_db):
    from edge_portfolio.contracts import DraftInput
    draft_id = str(uuid4())
    payload = DraftInput(holdings=[], preferences={'contribution': 'already bought', 'interests': ['custom']}).model_dump(mode='json')
    store.save_draft(draft_id, 'owner', payload)
    with store.connect() as db:
        for name in store.INPUT_TABLES:
            assert db.execute(f'SELECT count(*) AS n FROM portfolio_input_{name} WHERE draft_id=%s', (draft_id,)).fetchone()['n'] == 1
        assert db.execute('SELECT payload FROM portfolio_input_interests WHERE draft_id=%s', (draft_id,)).fetchone()['payload']['interests'] == ['custom']
    with pytest.raises(ValueError, match='NOT_FOUND'):
        store.save_draft(draft_id, 'other', payload)
    with pytest.raises(ValueError, match='NOT_FOUND'):
        store.submit('other', 'key', {'draft_id': draft_id})


def test_tax_popup_payload_round_trips_through_owned_draft_api(isolated_db):
    from fastapi.testclient import TestClient
    from edge_portfolio.api import app
    draft_id = str(uuid4())
    payload = {'holdings': [{'name': '현금', 'kind': 'cash', 'instrument_id': 'CASH:KRW', 'amount_krw': '1000000'}],
               'preferences': {'accounts': ['일반 계좌', 'ISA'], 'tax_profile': {'residence': '대한민국 거주자', 'age': '19~54세', 'income': '확인 필요', 'financial_income_tax': '아니요',
               'recommended_accounts': ['IRP', '연금저축'],
               'accounts': [{'account': 'ISA', 'subtype': '중개형 · 일반형', 'opened': '', 'remaining_man': '', 'paid_man': '0'}]}}}
    headers = {'Origin': 'http://localhost:5180'}
    with TestClient(app) as client:
        client.get('/api/portfolio/session')
        assert client.post('/api/portfolio/drafts/' + draft_id, json=payload, headers=headers).status_code == 200
        with store.connect() as db:
            tax = db.execute('SELECT payload FROM portfolio_input_tax WHERE draft_id=%s', (draft_id,)).fetchone()['payload']['tax_profile']
        assert tax['accounts'][0]['remaining_man'] == ''  # Unknown must not become zero.
        assert tax['accounts'][0]['paid_man'] == '0'
        assert tax['recommended_accounts'] == ['IRP', '연금저축']  # Strategy targets need not be existing accounts.
        client.cookies.clear()
        client.get('/api/portfolio/session')
        assert client.post('/api/portfolio/drafts/' + draft_id, json=payload, headers=headers).status_code == 404


def test_old_drafts_do_not_permanently_block_new_forms(isolated_db):
    from edge_portfolio.contracts import DraftInput
    payload = DraftInput(holdings=[]).model_dump(mode='json')
    first = str(uuid4())
    store.save_draft(first, 'owner', payload)
    for _ in range(11):
        store.save_draft(str(uuid4()), 'owner', payload)
    with store.connect() as db:
        assert db.execute('SELECT count(*) AS n FROM portfolio_input_drafts').fetchone()['n'] == 10
        assert db.execute('SELECT id FROM portfolio_input_drafts WHERE id=%s', (first,)).fetchone() is None
        assert db.execute('SELECT count(*) AS n FROM portfolio_input_tax').fetchone()['n'] == 10
