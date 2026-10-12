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


def test_two_workers_cannot_claim_same_job(isolated_db):
    store.submit("owner", "same", {})
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: store.claim(), range(2)))
    assert sum(row is not None for row in claimed) == 1


def test_crashed_worker_result_cannot_overwrite_expiration(isolated_db):
    job = store.submit("owner", "same", {})
    store.claim()
    with store.connect() as db:
        db.execute("UPDATE portfolio_jobs SET started_at=now()-interval '7 minutes'")
    assert store.read(job["id"], "owner")["failure"] == "WORKER_INTERRUPTED"
    assert store.finish(job["id"], result={"late": True}) is False


def test_api_rejects_other_origins_and_preserves_idempotency(isolated_db):
    from fastapi.testclient import TestClient
    from edge_portfolio.api import app
    with TestClient(app) as client:
        client.get('/api/portfolio/session')
        payload = {"holdings": [{"name": "VOO", "amount_krw": 100}], "horizon": "10년 이상"}
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
    assert isinstance(submit_chat('retry'), dict)


def test_email_is_bound_to_owned_completed_analysis(isolated_db):
    parent = store.submit('owner', 'parent', {})
    store.claim(); store.finish(parent['id'], result={})
    assert store.request_report(parent['id'], 'other', 'reader@example.com') is False
    assert store.request_report(parent['id'], 'owner', 'reader@example.com') is True


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
               'accounts': [{'account': 'ISA', 'subtype': '중개형 · 일반형', 'opened': '', 'remaining_man': '', 'paid_man': '0'}]}}}
    headers = {'Origin': 'http://localhost:5180'}
    with TestClient(app) as client:
        client.get('/api/portfolio/session')
        assert client.post('/api/portfolio/drafts/' + draft_id, json=payload, headers=headers).status_code == 200
        with store.connect() as db:
            tax = db.execute('SELECT payload FROM portfolio_input_tax WHERE draft_id=%s', (draft_id,)).fetchone()['payload']['tax_profile']
        assert tax['accounts'][0]['remaining_man'] == ''  # Unknown must not become zero.
        assert tax['accounts'][0]['paid_man'] == '0'
        client.cookies.clear()
        client.get('/api/portfolio/session')
        assert client.post('/api/portfolio/drafts/' + draft_id, json=payload, headers=headers).status_code == 404
