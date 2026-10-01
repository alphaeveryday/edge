"""Exercise the real SQL and SELECT-only role on an isolated local PostgreSQL."""
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.api.publications import PublicationReader


def test_latest_selection_and_screen_work_under_read_only_permissions():
    dsn=os.environ['V2_TEST_DSN']
    args=conninfo_to_dict(dsn)
    assert args.get('host')=='127.0.0.1' and args.get('port')=='55439' and args.get('dbname')=='analysis_v2'
    identities=[uuid4().hex for _ in range(5)]
    now=datetime.now(timezone.utc)
    def read_connection():
        conn=psycopg.connect(dsn,autocommit=True)
        conn.execute('SET ROLE edge_analysis_v2_api_reader')
        conn.execute('SET default_transaction_read_only=on')
        return conn
    reader=PublicationReader(read_connection)
    with psycopg.connect(dsn,autocommit=True) as c:
        try:
            for n,(state,source,published) in enumerate([
                ('completed','database',now),('completed','database',now-timedelta(hours=1)),
                ('running','database',None),('failed','database',None),('completed','synthetic',now)]):
                c.execute('''INSERT INTO movement_analyses(analysis_id,etf_code,analysis_at,status,
                    data_source,published_at,trading_date,summary) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (identities[n],'API001',now+timedelta(minutes=n),state,source,published,now.date(),'saved'))
            # Analysis time, not completion order, chooses identity[1].
            assert reader.latest('API001','movement')['analysis_id']==identities[1]
            assert reader.find(identities[1])['kind']=='movement'
            screen=reader.screen('movement',identities[1],'summary')
            assert screen['summary']=='saved' and screen['publication']['etf_code']=='API001'
            assert reader.latest('ABSENT','movement') is None
            with read_connection() as restricted:
                assert restricted.execute("SELECT has_table_privilege(current_user,'tool_runs','SELECT')").fetchone()[0] is False
                with pytest.raises(psycopg.Error):
                    restricted.execute('UPDATE movement_analyses SET summary=%s WHERE analysis_id=%s',('changed',identities[1]))
            assert reader.screen('movement',identities[1],'summary')['summary']=='saved'
        finally:
            c.execute('DELETE FROM movement_analyses WHERE analysis_id=ANY(%s)',(identities,))
