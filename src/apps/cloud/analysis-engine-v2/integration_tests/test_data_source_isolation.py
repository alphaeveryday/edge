"""Real publications must never inherit synthetic explanations or evidence."""
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.analysis.service import _previous
from edge_analysis_v2.storage.publications import PublicationStore


@pytest.fixture
def store():
    dsn = os.environ['V2_FACTOR_TEST_DSN']
    settings = conninfo_to_dict(dsn)
    assert settings.get('host') == '127.0.0.1' and settings.get('port') == '55440' and settings.get('dbname') == 'analysis_v2'
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.transaction():
            # Roll back every inserted test row, including failed assertions.
            try:
                yield connection
            finally:
                connection.execute('ROLLBACK')


@pytest.mark.parametrize('kind', ['movement', 'outlook'])
def test_latest_query_rejects_synthetic_and_unknown_rows(store, kind):
    etf = uuid4().hex
    for index, source in enumerate(['database', 'synthetic', 'unknown']):
        extra = ",trading_date" if kind == 'movement' else ''
        value = ",'2026-09-30'" if kind == 'movement' else ''
        store.execute(f"INSERT INTO {kind}_analyses (analysis_id,etf_code,analysis_at,status,data_source{extra}) "
                      f"VALUES (%s,%s,%s,'completed',%s{value})",
                      (source+etf, etf, f'2026-09-30T10:0{index}:00+09:00', source))
    at = datetime.fromisoformat('2026-09-30T11:00:00+09:00')
    assert _previous(store, kind, etf, at, 'database') == 'database'+etf


def test_cross_source_predecessor_and_reused_identity_are_rejected():
    dsn = os.environ['V2_FACTOR_TEST_DSN']
    settings = conninfo_to_dict(dsn)
    assert settings.get('host') == '127.0.0.1' and settings.get('port') == '55440' and settings.get('dbname') == 'analysis_v2'
    identity, etf = uuid4().hex, uuid4().hex
    at = datetime.now(timezone.utc)
    with psycopg.connect(dsn, autocommit=True) as connection:
        publications = PublicationStore(connection)
        try:
            publications.begin('movement', identity, etf, at, data_source='synthetic')
            connection.execute("UPDATE movement_analyses SET status='completed' WHERE analysis_id=%s", (identity,))
            with pytest.raises(ValueError):
                publications.begin('movement', uuid4().hex, etf, at+timedelta(minutes=1), identity, data_source='database')
            with pytest.raises(ValueError):
                publications.begin('movement', identity, etf, at, data_source='database')
        finally:
            connection.execute('DELETE FROM movement_analyses WHERE etf_code=%s', (etf,))
