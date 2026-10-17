"""Reject mismatched news snapshots before recording an analysis baseline."""
import base64
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from edge_analysis_v2.storage.news_snapshots import read_snapshot, save_snapshot
from edge_analysis_v2.storage import news_snapshots


CONTEXT = {'etf_code': '069500', 'analysis_at': '2026-10-17T00:00:00+00:00'}


def snapshot():
    return {'version': 1, 'snapshot_id': 'news-content-hash', **CONTEXT,
            'scope': {}, 'events': [], 'documents': []}


def connection():
    # No cursor: invalid values must fail before any database operation.
    return SimpleNamespace(autocommit=True,
                           info=SimpleNamespace(transaction_status=TransactionStatus.IDLE))


@pytest.mark.parametrize('change', [
    {'etf_code': 'OTHER'}, {'analysis_at': '2026-10-18T00:00:00+00:00'},
    {'analysis_at': '2026-10-17T00:00:00'}, {'version': True}, {'version': 2},
    {'snapshot_id': ''}, {'events': {'invalid': 'not a list'}},
    {'documents': [] , 'scope': {'invalid': float('nan')}},
])
def test_snapshot_cannot_change_target_time_or_format(change):
    with pytest.raises(ValueError):
        save_snapshot(connection(), 'outlook', 'analysis-1', CONTEXT, snapshot() | change)


@pytest.mark.parametrize('operation', [read_snapshot, save_snapshot])
def test_analysis_kind_is_not_a_sql_identifier_from_the_caller(operation):
    args = (CONTEXT, snapshot()) if operation is save_snapshot else ()
    with pytest.raises(ValueError, match='kind'):
        operation(connection(), 'outlook; DROP TABLE tool_runs', 'analysis-1', *args)


def test_internal_compression_is_lossless_canonical_and_preserves_json_types():
    original = snapshot() | {'documents': [{'title': '삼성전자', 'active': True}] * 1000}
    encoded = news_snapshots._encode(original)
    assert len(encoded['data']) < 1000
    assert news_snapshots._decode(encoded) == original
    assert news_snapshots._encode(dict(reversed(list(original.items())))) == encoded
    changed = original | {'documents': [{'title': '삼성전자', 'active': 1}] * 1000}
    assert news_snapshots._encode(changed) != encoded


@pytest.mark.parametrize('change', [
    {'encoding': 'plain'}, {'data': 'not-base64!'}, {'data': ''},
    {'sha256': 'wrong'}, {'data': None},
])
def test_corrupt_internal_snapshot_is_not_a_missing_baseline(change):
    encoded = news_snapshots._encode(snapshot())
    with pytest.raises(ValueError, match='compressed news snapshot'):
        news_snapshots._decode(encoded | change)


def test_trailing_bytes_are_not_silently_discarded_on_restore():
    encoded = news_snapshots._encode(snapshot())
    encoded['data'] = base64.b64encode(base64.b64decode(encoded['data']) + b'extra').decode('ascii')
    with pytest.raises(ValueError, match='compressed news snapshot'):
        news_snapshots._decode(encoded)
