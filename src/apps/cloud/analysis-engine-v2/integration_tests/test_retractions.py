"""A delayed analysis or duplicate reversion must not restore a withdrawn post."""
from datetime import timedelta
import json

import pytest

from test_delivery import delivery
from edge_analysis_v2.storage.delivery import enqueue_movement
from edge_analysis_v2.storage.retractions import retract_movement


@pytest.fixture
def reversion(delivery):
    conn, dsn, tenant, identity, make = delivery
    etf, cutoff = conn.execute('SELECT etf_code,analysis_at FROM movement_analyses WHERE analysis_id=%s',(identity,)).fetchone()
    event = {'event_id':etf,'event_type':'ExposureReverted','payload':{
        'entity_id':etf,'session_id':etf,'window_start':cutoff.isoformat()}}
    def link(analysis_id, offset=0, session=None):
        request = {'etf_code':etf,'source':{'session_id':session or etf,
                   'window_start':(cutoff+timedelta(minutes=offset)).isoformat()}}
        conn.execute('''INSERT INTO analysis_execution_requests(analysis_id,input_json,execution_arn)
            VALUES (%s,%s,%s)''',(analysis_id,json.dumps(request),analysis_id))
    link(identity)
    yield conn, tenant, identity, make, event, link
    conn.execute('RESET ROLE')
    conn.execute('DELETE FROM tenant_delivery WHERE target_movement_analysis_id IN (SELECT analysis_id FROM movement_analyses WHERE etf_code=%s)',(etf,))
    conn.execute('DELETE FROM movement_retractions WHERE etf_code=%s',(etf,))
    conn.execute('DELETE FROM analysis_execution_requests WHERE input_json::jsonb->>\'etf_code\'=%s',(etf,))


def test_repeated_reversion_withdraws_once_and_preserves_evidence(reversion):
    conn, tenant, identity, _, event, _ = reversion
    conn.execute('SET ROLE edge_analysis_v2_writer')
    assert enqueue_movement(conn,identity)>0
    assert retract_movement(conn,event)>0
    assert retract_movement(conn,event)==0
    assert enqueue_movement(conn,identity)==0
    assert conn.execute('SELECT delivery_type FROM tenant_delivery WHERE tenant_id=%s ORDER BY cursor',(tenant,)).fetchall()==[('NEW',),('INVALIDATION',)]
    assert conn.execute('SELECT withdrawn_at FROM movement_analyses WHERE analysis_id=%s',(identity,)).fetchone()[0]
    assert conn.execute('SELECT count(*) FROM tool_runs WHERE movement_analysis_id=%s',(identity,)).fetchone()[0]==1


def test_reversion_before_analysis_finishes_blocks_post_but_not_later_trigger(reversion):
    conn, tenant, identity, make, event, link = reversion
    later=make('-later',offset=5); link(later,5)
    different_session=make('-other',offset=10); link(different_session,-5,session='another-session')
    conn.execute('SET ROLE edge_analysis_v2_writer')
    assert retract_movement(conn,event)==0
    assert enqueue_movement(conn,identity)==0
    assert enqueue_movement(conn,later)>0
    assert enqueue_movement(conn,different_session)>0
    assert retract_movement(conn,event)==0


def test_same_event_cannot_change_its_target(reversion):
    conn, _, _, _, event, _ = reversion
    retract_movement(conn,event)
    event['payload']['session_id']='forged'
    with pytest.raises(ValueError,match='identity conflict'):
        retract_movement(conn,event)
