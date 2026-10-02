"""A successful movement is delivered once under the production writer grants."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edge_analysis_v2.storage.delivery import enqueue_movement


@pytest.fixture
def delivery():
    dsn = os.environ['V2_TEST_DSN']
    args = conninfo_to_dict(dsn)
    if (args.get('host'),args.get('port'),args.get('dbname')) != ('127.0.0.1','55439','analysis_v2'):
        raise ValueError('Requires isolated local analysis_v2 database')
    key=uuid4().hex
    now=datetime.now(timezone.utc)
    with psycopg.connect(dsn,autocommit=True) as conn:
        tenant=conn.execute("INSERT INTO tenant(tenant_name,environment,status) VALUES (%s,'DEV','ACTIVE') RETURNING tenant_id",(key,)).fetchone()[0]
        conn.execute("INSERT INTO tool_definitions(tool_id,function_name,version,description) VALUES (%s,'sum',%s,'Net flow sum')",(key,key))
        def make(suffix='', offset=0, source='database', published=True):
            identity=key+suffix
            conn.execute("""INSERT INTO movement_analyses(analysis_id,etf_code,analysis_at,trading_date,status,published_at,data_source,summary,selected_item_ids)
                VALUES (%s,%s,%s,%s,'completed',%s,%s,'Summary',%s)""",
                (identity,key,now+timedelta(minutes=offset),now.date(),now if published else None,source,[identity]))
            conn.execute("""INSERT INTO tool_runs(tool_run_id,tool_id,movement_analysis_id,output,status,finished_at)
                VALUES (%s,%s,%s,jsonb_build_object('tool_run_id',%s::text,'result',jsonb_build_object('amount_krw',18)),'completed',now())""",(identity,key,identity,identity))
            conn.execute("""INSERT INTO movement_items(item_id,analysis_id,type,title_keyword,sentence,sentiment,tool_run_ids)
                VALUES (%s,%s,'수급','Foreign flow','Net buying','positive',%s)""",(identity,identity,[identity]))
            return identity
        identity=make()
        try:
            yield conn, dsn, tenant, identity, make
        finally:
            conn.execute('RESET ROLE')
            conn.execute('DELETE FROM tenant_delivery WHERE movement_analysis_id IN (SELECT analysis_id FROM movement_analyses WHERE etf_code=%s)',(key,))
            conn.execute('DELETE FROM movement_items WHERE analysis_id IN (SELECT analysis_id FROM movement_analyses WHERE etf_code=%s)',(key,))
            conn.execute('DELETE FROM tool_runs WHERE tool_id=%s',(key,))
            conn.execute('DELETE FROM tool_definitions WHERE tool_id=%s',(key,))
            conn.execute('DELETE FROM movement_analyses WHERE etf_code=%s',(key,))
            conn.execute('DELETE FROM tenant WHERE tenant_id=%s',(tenant,))


def test_writer_can_append_once_but_cannot_change_v1_or_delete_deliveries(delivery):
    conn,dsn,tenant,identity,make=delivery
    conn.execute('SET ROLE edge_analysis_v2_writer')
    assert enqueue_movement(conn,identity)>=1
    assert enqueue_movement(conn,identity)==0
    assert conn.execute('SELECT cursor FROM tenant_delivery WHERE tenant_id=%s',(tenant,)).fetchall()==[(1,)]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute('DELETE FROM tenant_delivery WHERE tenant_id=%s',(tenant,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("INSERT INTO tenant_delivery(tenant_id,cursor,delivery_type,explanation_result_id) VALUES (%s,2,'NEW','forged')",(tenant,))


def test_concurrent_retries_commit_one_cursor(delivery):
    conn,dsn,tenant,identity,make=delivery
    def send(_):
        with psycopg.connect(dsn,autocommit=True) as other:
            other.execute('SET ROLE edge_analysis_v2_writer')
            return enqueue_movement(other,identity)
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts=list(pool.map(send,range(2)))
    assert sum(c>0 for c in counts)==1
    assert conn.execute('SELECT count(*) FROM tenant_delivery WHERE tenant_id=%s',(tenant,)).fetchone()[0]==1


def test_synthetic_unpublished_broken_and_stale_results_do_not_become_new_posts(delivery):
    conn,dsn,tenant,identity,make=delivery
    with pytest.raises(ValueError,match='real-data'):
        enqueue_movement(conn,make('-fake',source='synthetic'))
    assert enqueue_movement(conn,make('-empty',published=False))==0
    broken=make('-broken')
    conn.execute("UPDATE movement_items SET tool_run_ids=ARRAY['missing'] WHERE item_id=%s",(broken,))
    with pytest.raises(ValueError,match='evidence'):
        enqueue_movement(conn,broken)
    newer=make('-new',offset=1)
    assert enqueue_movement(conn,newer)>=1
    assert enqueue_movement(conn,identity)==0
    assert conn.execute('SELECT movement_analysis_id FROM tenant_delivery WHERE tenant_id=%s',(tenant,)).fetchall()==[(newer,)]


def test_unchanged_selection_does_not_republish_and_new_selection_does(delivery):
    conn,dsn,tenant,identity,make=delivery
    assert enqueue_movement(conn,identity)>=1
    assert enqueue_movement(conn,make('-same-time'))==0
    unchanged=make('-same',offset=1)
    conn.execute('UPDATE movement_analyses SET selected_item_ids=%s WHERE analysis_id=%s',([identity],unchanged))
    assert enqueue_movement(conn,unchanged)==0
    changed=make('-change',offset=2)
    assert enqueue_movement(conn,changed)>=1
    assert conn.execute('SELECT cursor FROM tenant_delivery WHERE tenant_id=%s ORDER BY cursor',(tenant,)).fetchall()==[(1,),(2,)]
