"""Reject mismatched news snapshots before recording an analysis baseline."""
from types import SimpleNamespace

import pytest
from psycopg.pq import TransactionStatus

from edge_analysis_v2.storage.news_snapshots import read_snapshot, save_snapshot


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
