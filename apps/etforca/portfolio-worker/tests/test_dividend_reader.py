from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from edge_portfolio import dividend_reader, outlook_gateway


def test_gateway_accepts_one_public_identity_not_arbitrary_sql(monkeypatch):
    calls = []
    monkeypatch.setattr(outlook_gateway, 'read_distributions', lambda query: calls.append(query) or {'status': 'missing'})
    body = {'ticker': '0093A0', 'start': '2025-01-01', 'end': '2026-12-31'}
    with TestClient(outlook_gateway.app) as client:
        for change in ({'ticker': ['0093A0']}, {'sql': 'SELECT *'}, {'market_code': 'US'}, {'end': '2024-01-01'}):
            assert client.post('/dividends', json={**body, **change}).status_code == 422
        assert not calls
        assert client.post('/dividends', json=body).json()['status'] == 'missing'
        assert len(calls) == 1


@pytest.mark.parametrize('state,rows,expected,complete', [
    ('complete', [], 'empty', True), ('failed', [], 'missing', False),
    ('partial', [{'amount_per_share': Decimal('0.123456789')}], 'found', False),
    (None, [], 'missing', False),
])
def test_reader_keeps_partial_cash_but_never_claims_failure_is_zero(monkeypatch, state, rows, expected, complete):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None): return self
        def fetchone(self):
            return {'status': state, 'coverage_from': date(2025, 1, 1), 'coverage_to': date(2027, 1, 1)} if state else None
        def fetchall(self): return rows
    monkeypatch.setenv('PORTFOLIO_OUTLOOK_DSN', 'test')
    monkeypatch.setattr(dividend_reader.psycopg, 'connect', lambda *a, **kw: Connection())
    result = dividend_reader.read_distributions(dividend_reader.DividendQuery(ticker='069500', start=date(2025, 1, 1), end=date(2026, 1, 1)))
    assert result['status'] == expected
    assert result['window_complete'] == complete
    if rows:
        assert result['events'][0]['amount_per_share'] == '0.123456789'
