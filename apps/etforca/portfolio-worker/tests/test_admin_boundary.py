from fastapi.testclient import TestClient

from edge_portfolio.api import app as public_app


def test_public_app_has_no_admin_routes_even_with_origin_credentials(monkeypatch):
    monkeypatch.setenv('PORTFOLIO_ORIGIN_SECRET', 'test-origin')
    client = TestClient(public_app)
    headers = {'x-portfolio-origin': 'test-origin', 'Origin': 'http://localhost:5180'}
    for path in ('overview', 'records?kind=analyses', 'records/drafts/example', 'login', 'logout'):
        assert client.get('/api/portfolio/admin/' + path, headers=headers).status_code == 404
    assert client.post('/api/portfolio/admin/login', headers=headers, json={'username': 'admin', 'password': 'unused'}).status_code == 404


def test_private_app_blocks_rebinding_and_cross_site_reads(monkeypatch):
    from edge_portfolio.admin_app import app
    from edge_portfolio import admin_store
    monkeypatch.setattr(admin_store, 'overview', lambda scope='all': {'active_visitors': 3})
    client = TestClient(app, base_url='http://localhost:18080')
    path = '/api/portfolio/admin/overview'
    assert client.get(path).json() == {'active_visitors': 3}
    for headers in ({'Host': 'evil.example:18080'}, {'Origin': 'https://evil.example'},
                    {'Sec-Fetch-Site': 'cross-site'}, {'Origin': 'null'}):
        assert client.get(path, headers=headers).status_code == 403
    response = client.get(path, headers={'Origin': 'http://localhost:18080', 'Sec-Fetch-Site': 'same-origin'})
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Frame-Options'] == 'DENY'
    assert 'access-control-allow-origin' not in response.headers
    assert client.post(path).status_code == 405
