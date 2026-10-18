from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from edge_portfolio import store
from test_store import isolated_db  # noqa: F401


def test_different_request_keys_share_one_analysis_but_never_another_owners_result(isolated_db):
    with ThreadPoolExecutor(2) as pool:
        jobs = list(pool.map(lambda key: store.submit('owner', key, {'question': 'same'}, reuse=True), ['a', 'b']))
    assert jobs[0]['id'] == jobs[1]['id']
    store.claim()
    store.finish(jobs[0]['id'], result={'answer': 'saved'})
    assert store.submit('owner', 'c', {'question': 'same'}, reuse=True)['result'] == {'answer': 'saved'}
    assert store.submit('other', 'c', {'question': 'same'}, reuse=True)['id'] != jobs[0]['id']
    assert store.latest('nobody') is None


def test_cache_retry_remains_idempotent_after_cache_expiry_and_rejects_changed_payload(isolated_db, monkeypatch):
    first = store.submit('owner', 'a', {'question': 'same'}, reuse=True)
    store.submit('owner', 'b', {'question': 'same'}, reuse=True)
    monkeypatch.setattr(store, 'analysis_cache_key', lambda _: 'next-day-or-version')
    assert store.submit('owner', 'b', {'question': 'same'}, reuse=True)['id'] == first['id']
    assert store.submit('owner', 'new', {'question': 'same'}, reuse=True)['id'] != first['id']
    with pytest.raises(ValueError, match='IDEMPOTENCY_CONFLICT'):
        store.submit('owner', 'b', {'question': 'changed'}, reuse=True)


def test_failed_analysis_is_retried_and_latest_tracks_the_result_requested_again(isolated_db):
    first = store.submit('owner', 'a', {'question': 'first'}, reuse=True)
    store.claim()
    store.finish(first['id'], failure='MODEL_TIMEOUT')
    second = store.submit('owner', 'b', {'question': 'first'}, reuse=True)
    assert second['id'] != first['id']
    third = store.submit('owner', 'c', {'question': 'different'}, reuse=True)
    assert store.latest('owner')['id'] == third['id']
    assert store.submit('owner', 'd', {'question': 'first'}, reuse=True)['id'] == second['id']
    assert store.latest('owner')['id'] == second['id'], 'A cache hit must be the latest requested analysis without spending another quota'


def test_cache_key_ignores_only_the_draft_handle_and_changes_with_analysis_version(monkeypatch):
    first = store.analysis_cache_key({'draft_id': 'one', 'question': 'same', 'holdings': [1]})
    assert store.analysis_cache_key({'holdings': [1], 'question': 'same', 'draft_id': 'two'}) == first
    assert store.analysis_cache_key({'question': 'different', 'holdings': [1]}) != first
    monkeypatch.setattr(store, 'analysis_version', lambda: 'changed')
    assert store.analysis_cache_key({'question': 'same', 'holdings': [1]}) != first


def test_latest_endpoint_uses_owner_cookie_and_is_not_confused_with_a_job_id(isolated_db):
    from fastapi.testclient import TestClient
    from edge_portfolio.api import app
    with TestClient(app) as client:
        assert client.get('/api/portfolio/analyses/latest').status_code == 401
        client.get('/api/portfolio/session')
        assert client.get('/api/portfolio/analyses/latest').json() is None
        payload = {'holdings': [{'name': 'VOO', 'amount_krw': 100}], 'horizon': '10년 이상', 'preferences': {'contribution': '이미 다 매수한 상태에요'}}
        first = client.post('/api/portfolio/analyses', json=payload, headers={'Origin': 'http://localhost:5180', 'Idempotency-Key': str(uuid4())})
        again = client.post('/api/portfolio/analyses', json=payload, headers={'Origin': 'http://localhost:5180', 'Idempotency-Key': str(uuid4())})
        assert first.status_code == again.status_code == 202
        assert first.json()['id'] == again.json()['id'] == client.get('/api/portfolio/analyses/latest').json()['id']
