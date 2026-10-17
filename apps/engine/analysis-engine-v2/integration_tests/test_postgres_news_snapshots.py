"""News baselines survive retries but never become a failed analysis's memory."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import os
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from edge_analysis_v2.storage.news_snapshots import read_snapshot, save_snapshot
from edge_analysis_v2.storage.publications import PublicationStore
from edge_analysis_v2.tools.execution import ToolInputError


@pytest.fixture
def database():
    dsn = os.environ['V2_NEWS_TEST_DSN']
    params = conninfo_to_dict(dsn)
    if (params.get('host') not in ('localhost', '127.0.0.1')
            or params.get('hostaddr') not in (None, '127.0.0.1')
            or params.get('port') != '15449' or params.get('dbname') not in {'testanalysis', 'analysis_v2'}):
        raise ValueError('News integration tests require localhost:15449/testanalysis or analysis_v2')
    identity = 'news-snapshot-test-' + uuid4().hex
    at = datetime(2026, 10, 17, tzinfo=timezone.utc)
    context = {'etf_code': '069500', 'analysis_at': at.isoformat()}
    snapshot = {'version': 1, 'snapshot_id': 'sample-hash', **context,
                'scope': {'complete': True}, 'events': [{'id': 'event-1'}], 'documents': []}
    with psycopg.connect(dsn, autocommit=True) as conn:
        for kind in ('outlook', 'movement'):
            PublicationStore(conn).begin(kind, identity, context['etf_code'], at)
        try:
            yield conn, dsn, identity, context, snapshot
        finally:
            conn.execute('DELETE FROM tool_runs WHERE outlook_analysis_id=%s OR movement_analysis_id=%s',
                         (identity, identity))
            conn.execute('DELETE FROM outlook_analyses WHERE analysis_id=%s', (identity,))
            conn.execute('DELETE FROM movement_analyses WHERE analysis_id=%s', (identity,))


def complete(conn, identity, kind='outlook'):
    # Publication shape validation is tested elsewhere; here only lifecycle eligibility matters.
    conn.execute(f"UPDATE {kind}_analyses SET status='completed' WHERE analysis_id=%s", (identity,))


def test_only_completed_publication_baseline_can_be_read(database):
    conn, _, identity, context, snapshot = database
    assert read_snapshot(conn, 'outlook', identity) is None  # pre-upgrade/no baseline
    save_snapshot(conn, 'outlook', identity, context, snapshot)
    assert read_snapshot(conn, 'outlook', identity) is None  # running is not previous publication
    complete(conn, identity)
    assert read_snapshot(conn, 'outlook', identity) == snapshot
    assert read_snapshot(conn, 'movement', identity) is None
    conn.execute("UPDATE outlook_analyses SET status='failed' WHERE analysis_id=%s", (identity,))
    assert read_snapshot(conn, 'outlook', identity) is None


def test_retry_after_commit_reply_lost_or_publication_preserves_one_record(database):
    conn, _, identity, context, snapshot = database
    save_snapshot(conn, 'outlook', identity, context, snapshot)
    first = conn.execute('SELECT started_at, finished_at FROM tool_runs WHERE outlook_analysis_id=%s',
                         (identity,)).fetchone()
    complete(conn, identity)
    save_snapshot(conn, 'outlook', identity, context, deepcopy(snapshot))
    rows = conn.execute('SELECT started_at, finished_at FROM tool_runs WHERE outlook_analysis_id=%s',
                        (identity,)).fetchall()
    assert rows == [first]
    changed = deepcopy(snapshot)
    changed['events'].append({'id': 'different-content-with-same-snapshot-id'})
    with pytest.raises((ValueError, psycopg.errors.UniqueViolation)):
        save_snapshot(conn, 'outlook', identity, context, changed)


def test_concurrent_identical_snapshots_are_one_immutable_baseline(database):
    conn, dsn, identity, context, snapshot = database
    def save():
        with psycopg.connect(dsn, autocommit=True) as other:
            save_snapshot(other, 'outlook', identity, context, snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: save(), range(2)))
    assert conn.execute('SELECT count(*) FROM tool_runs WHERE outlook_analysis_id=%s',
                        (identity,)).fetchone()[0] == 1


@pytest.mark.parametrize('status', ['completed', 'failed'])
def test_cannot_insert_baseline_after_analysis_finished(database, status):
    conn, _, identity, context, snapshot = database
    conn.execute('UPDATE outlook_analyses SET status=%s WHERE analysis_id=%s', (status, identity))
    with pytest.raises(ValueError, match='running'):
        save_snapshot(conn, 'outlook', identity, context, snapshot)


def test_parent_context_is_checked_and_failed_retries_are_refused(database):
    conn, _, identity, context, snapshot = database
    foreign = context | {'etf_code': 'OTHER'}
    with pytest.raises(ValueError, match='parent'):
        save_snapshot(conn, 'outlook', identity, foreign, snapshot | {'etf_code': 'OTHER'})
    save_snapshot(conn, 'outlook', identity, context, snapshot)
    conn.execute("UPDATE outlook_analyses SET status='failed' WHERE analysis_id=%s", (identity,))
    with pytest.raises(ValueError):
        save_snapshot(conn, 'outlook', identity, context, snapshot)


def test_distinct_kinds_have_distinct_records_and_json_types_stay_immutable(database):
    conn, _, identity, context, snapshot = database
    for kind in ('outlook', 'movement'):
        save_snapshot(conn, kind, identity, context, snapshot)
        complete(conn, identity, kind)
        assert read_snapshot(conn, kind, identity) == snapshot
    assert conn.execute('SELECT count(*) FROM tool_runs WHERE outlook_analysis_id=%s OR movement_analysis_id=%s',
                        (identity, identity)).fetchone()[0] == 2
    changed = deepcopy(snapshot)
    changed['scope']['complete'] = 1  # Python equates True and 1; immutable JSON must not.
    with pytest.raises(psycopg.errors.UniqueViolation):
        save_snapshot(conn, 'outlook', identity, context, changed)


def test_rejected_save_releases_session_lock_and_read_rechecks_parent(database):
    conn, dsn, identity, context, snapshot = database
    foreign = context | {'etf_code': 'OTHER'}
    with pytest.raises(ValueError):
        save_snapshot(conn, 'outlook', identity, foreign, snapshot | {'etf_code': 'OTHER'})
    key = str(uuid5(NAMESPACE_URL, f'edge:capture_news_snapshot:v1:outlook:{identity}'))
    with psycopg.connect(dsn, autocommit=True) as other:
        assert other.execute('SELECT pg_try_advisory_lock(hashtextextended(%s, 0))', (key,)).fetchone()[0]
        other.execute('SELECT pg_advisory_unlock(hashtextextended(%s, 0))', (key,))
    save_snapshot(conn, 'outlook', identity, context, snapshot)
    complete(conn, identity)
    conn.execute("UPDATE outlook_analyses SET etf_code='OTHER' WHERE analysis_id=%s", (identity,))
    with pytest.raises(ValueError, match='parent'):
        read_snapshot(conn, 'outlook', identity)


def test_internal_baseline_cannot_be_cited_as_final_news_evidence(database):
    conn, _, identity, context, snapshot = database
    save_snapshot(conn, 'outlook', identity, context, snapshot)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT * FROM outlook_analyses WHERE analysis_id=%s', (identity,))
        parent = cur.fetchone()
        cur.execute('SELECT tool_run_id FROM tool_runs WHERE outlook_analysis_id=%s', (identity,))
        run_id = cur.fetchone()['tool_run_id']
        with pytest.raises(ToolInputError, match='not final-eligible'):
            PublicationStore(conn, final_tool_names={'get_issue_evidence'})._evidence(cur, [run_id], parent)
