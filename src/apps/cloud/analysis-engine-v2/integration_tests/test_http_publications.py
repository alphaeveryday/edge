"""Exercise the real SQL and SELECT-only role on an isolated local PostgreSQL."""
from datetime import date, datetime, timedelta, timezone
import os
from uuid import uuid4

import psycopg
from psycopg import sql
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


@pytest.mark.parametrize('kind',['outlook','movement'])
def test_analysis_day_boundaries_republication_and_no_fallback(kind):
    """Historical reads stay on their KST day even when later publications exist."""
    dsn=os.environ['V2_TEST_DSN']
    args=conninfo_to_dict(dsn)
    assert args.get('host')=='127.0.0.1' and args.get('port')=='55439' and args.get('dbname')=='analysis_v2'
    table=sql.Identifier(kind+'_analyses')
    identities=[uuid4().hex for _ in range(11)]
    published=datetime.fromisoformat('2026-10-03T10:00:00+09:00')
    def read_connection():
        conn=psycopg.connect(dsn,autocommit=True)
        conn.execute('SET ROLE edge_analysis_v2_api_reader')
        conn.execute('SET default_transaction_read_only=on')
        return conn
    reader=PublicationReader(read_connection)
    with psycopg.connect(dsn,autocommit=True) as c:
        def insert(n,stamp,*,state='completed',source='database',publication=published):
            columns=['analysis_id','etf_code','analysis_at','status','data_source','published_at']
            values=[identities[n],'API002',datetime.fromisoformat(stamp),state,source,publication]
            if kind=='movement':
                columns.append('trading_date')
                values.append(values[2].astimezone(timezone(timedelta(hours=9))).date())
            c.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(table,
                sql.SQL(',').join(map(sql.Identifier,columns)),
                sql.SQL(',').join(sql.Placeholder() for _ in values)),values)
        try:
            insert(0,'2026-09-30T14:59:59.999999+00:00')
            insert(1,'2026-09-30T15:00:00+00:00')
            assert reader.latest('API002',kind,analysis_date=date(2026,10,1))['analysis_id']==identities[1]
            insert(2,'2026-10-01T14:59:59.999999+00:00')
            insert(3,'2026-10-01T15:00:00+00:00')
            assert reader.latest('API002',kind,analysis_date=date(2026,10,1))['analysis_id']==identities[2]
            # A republished result wins only when its analysis time is equally recent.
            for n in (4,5):
                insert(n,'2026-10-01T14:59:59.999999+00:00',publication=published+timedelta(minutes=1))
                assert reader.latest('API002',kind,analysis_date=date(2026,10,1))['analysis_id']==max(identities[4:n+1])
            insert(6,'2026-10-03T10:00:00+09:00',state='running',publication=None)
            insert(7,'2026-10-03T10:01:00+09:00',state='failed',publication=None)
            insert(8,'2026-10-03T10:02:00+09:00',source='synthetic')
            insert(9,'2026-10-03T10:03:00+09:00',publication=None)
            insert(10,'2026-10-01T08:30:00+09:00',publication=published+timedelta(days=2))
            assert reader.latest('API002',kind)['analysis_id']==identities[3]
            assert reader.latest('API002',kind,analysis_date=date(2026,10,1))['analysis_id']==max(identities[4:6])
            assert reader.latest('API002',kind,analysis_date=date(2026,9,30))['analysis_id']==identities[0]
            assert reader.latest('API002',kind,analysis_date=date(2026,10,3)) is None
            assert reader.latest('API002',kind,analysis_date=date(2000,1,1)) is None
            assert reader.latest('API002',kind,analysis_date=date.max) is None
        finally:
            c.execute(sql.SQL('DELETE FROM {} WHERE analysis_id=ANY(%s)').format(table),(identities,))
