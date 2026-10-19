import pytest
from pydantic import ValidationError
from edge_portfolio.dashboard import Workbook, Save, from_analysis, read, save
from test_store import isolated_db  # noqa: F401
from fastapi.testclient import TestClient
from psycopg import OperationalError
from edge_portfolio import api


def test_percent_input_is_not_invented_money_or_cost():
    result = from_analysis({'input': {'input_mode': 'percent', 'holdings': [
        {'name': 'ETF', 'account': 'ISA', 'amount_krw': '60'}]}})
    assert result['assets'][0]['weight'] == 60
    assert result['assets'][0]['amount'] is None
    assert result['assets'][0]['average'] is None


def test_original_holdings_are_preserved_without_recommendation():
    result = from_analysis({'input': {'holdings': [
        {'name': 'ETF', 'account': 'IRP', 'amount_krw': '123456.78'}]}})
    assert result['assets'][0]['amount'] == 123456.78
    assert result['fx'] is None


def test_us_security_keeps_trading_currency_without_inventing_fx():
    result = from_analysis({'input': {'holdings': [{'name': 'VOO', 'instrument_id': 'US:VOO', 'account': 'ISA', 'amount_krw': '10000'}]}})
    assert result['assets'][0]['currency'] == 'USD'
    assert result['assets'][0]['amount'] == 10000
    assert result['fx'] is None


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf'), True])
def test_invalid_money_cannot_be_saved(value):
    with pytest.raises(ValidationError):
        Workbook(assets=[{'id': 'a', 'name': 'ETF', 'account': 'ISA', 'amount': value}])


def test_dividends_cannot_silently_lose_their_holding():
    with pytest.raises(ValidationError):
        Workbook(dividends=[{'id': 'd', 'asset_id': 'missing', 'date': '2026-10-17', 'status': '실수령', 'amount': 10}])


def test_boolean_fx_is_not_one_won_per_dollar():
    with pytest.raises(ValidationError):
        Workbook(fx=True)
    with pytest.raises(ValidationError):
        Workbook(assets=[{'id': 'a', 'name': 'ETF', 'account': 'ISA', 'cost_fx': True}])


def test_save_is_private_and_stale_tabs_cannot_destroy_newer_changes(isolated_db):
    first = save('owner', Save(version=0, workbook=Workbook(title='첫 저장')))
    assert read('other')['workbook'] is None
    assert read('owner') == first
    with pytest.raises(ValueError, match='WORKBOOK_CONFLICT'):
        save('owner', Save(version=0, workbook=Workbook(title='오래된 탭')))
    assert read('owner')['workbook']['title'] == '첫 저장'
    second = save('owner', Save(version=1, workbook=Workbook(title='수정')))
    assert second['version'] == 2


def test_api_requires_session_and_same_origin_before_writes(monkeypatch):
    monkeypatch.delenv('PORTFOLIO_ORIGIN_SECRET', raising=False)
    monkeypatch.setenv('PORTFOLIO_ORIGIN', 'http://localhost:5180')
    client = TestClient(api.app)
    assert client.get('/api/portfolio/workbook').status_code == 401
    body = {'version': 0, 'workbook': {}}
    assert client.post('/api/portfolio/workbook', json=body).status_code == 403
    assert client.post('/api/portfolio/workbook', json=body, headers={'origin': 'http://localhost:5180'}).status_code == 401


def test_source_snapshot_never_reads_another_visitors_job(monkeypatch):
    monkeypatch.delenv('PORTFOLIO_ORIGIN_SECRET', raising=False)
    calls = []
    monkeypatch.setattr(api.store, 'read', lambda job, owner: calls.append((job, owner)))
    client = TestClient(api.app)
    client.cookies.set(api.COOKIE, 'a' * 64)
    assert client.get('/api/portfolio/analyses/other-job/workbook').status_code == 404
    import hashlib
    assert calls == [('other-job', hashlib.sha256(('a' * 64).encode()).hexdigest())]


def test_active_visitor_keeps_same_owner_with_renewed_cookie(monkeypatch):
    monkeypatch.delenv('PORTFOLIO_ORIGIN_SECRET', raising=False)
    client = TestClient(api.app)
    client.cookies.set(api.COOKIE, 'a' * 64)
    response = client.get('/api/portfolio/session')
    assert f'{api.COOKIE}=' + 'a' * 64 in response.headers['set-cookie']
    assert 'Max-Age=2592000' in response.headers['set-cookie']
def test_successful_save_remains_successful_if_optional_tracking_read_fails(monkeypatch, caplog):
    from edge_portfolio import api
    monkeypatch.setattr(api.dashboard, 'save', lambda *args: {'version': 2, 'workbook': {'assets': []}})
    def fail(*args):
        raise OperationalError('temporary read outage')
    monkeypatch.setattr(api.dashboard_work, 'tracking', fail)
    monkeypatch.setattr(api.dashboard, 'read', lambda *args: {'version': 2, 'workbook': {'assets': []}})
    client = TestClient(api.app)
    client.cookies.set(api.COOKIE, 'a' * 64)
    loaded = client.get('/api/portfolio/workbook')
    assert loaded.status_code == 200 and loaded.json() == {'version': 2, 'workbook': {'assets': []}}
    response = client.post('/api/portfolio/workbook', headers={'origin': 'http://localhost:5180'}, json={'version': 1, 'workbook': {'assets': []}})
    assert response.status_code == 200 and response.json() == {'version': 2, 'workbook': {'assets': []}}
    assert 'saved_workbook_tracking_unavailable' in caplog.text
