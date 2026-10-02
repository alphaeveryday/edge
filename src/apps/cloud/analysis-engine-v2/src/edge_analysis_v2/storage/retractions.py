"""Withdraw price-event publications without deleting their analysis or evidence."""
from datetime import datetime

from psycopg.rows import dict_row

REASON = '가격이 기준선으로 복귀하여 자동 노출을 종료했습니다.'


def is_retracted(cur, analysis_id):
    """Check durable reversion coordinates even when analysis finishes afterwards."""
    cur.execute('''SELECT 1 FROM analysis_execution_requests q
        JOIN movement_retractions r ON r.etf_code=q.input_json::jsonb->>'etf_code'
            AND r.session_id=q.input_json::jsonb->'source'->>'session_id'
            AND r.window_start>=(q.input_json::jsonb->'source'->>'window_start')::timestamptz
        WHERE q.analysis_id=%s LIMIT 1''', (analysis_id,))
    return cur.fetchone() is not None


def retract_movement(connection, event):
    """Record a verified reversion and append each tenant's withdrawal once.

    Args:
        connection: Idle result-writer connection.
        event: Original outbox envelope verified by load_queue_event.

    Returns:
        Number of withdrawal deliveries added in the transaction.
    """
    p = event['payload']
    if event['event_type'] != 'ExposureReverted':
        raise ValueError('Expected a price reversion')
    cutoff = datetime.fromisoformat(p['window_start'])
    if cutoff.utcoffset() is None:
        raise ValueError('Reversion timestamp requires offset')
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('tenant-delivery-fanout')::bigint)")
        cur.execute('''INSERT INTO movement_retractions(event_id,etf_code,session_id,window_start)
            VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
            (event['event_id'],p['entity_id'],p['session_id'],cutoff))
        cur.execute('SELECT * FROM movement_retractions WHERE event_id=%s', (event['event_id'],))
        row = cur.fetchone()
        if (row['etf_code'],row['session_id'],row['window_start']) != (p['entity_id'],p['session_id'],cutoff):
            raise ValueError('Reversion identity conflict')
        cur.execute('''UPDATE movement_analyses a SET withdrawn_at=COALESCE(a.withdrawn_at,now())
            FROM analysis_execution_requests q WHERE q.analysis_id=a.analysis_id
            AND a.etf_code=%s AND q.input_json::jsonb->'source'->>'session_id'=%s
            AND (q.input_json::jsonb->'source'->>'window_start')::timestamptz<=%s''',
            (p['entity_id'],p['session_id'],cutoff))
        cur.execute('''SELECT d.tenant_id,d.movement_analysis_id FROM tenant_delivery d
            JOIN movement_analyses a ON a.analysis_id=d.movement_analysis_id
            WHERE a.etf_code=%s AND a.withdrawn_at IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM tenant_delivery old WHERE old.tenant_id=d.tenant_id
                AND old.target_movement_analysis_id=d.movement_analysis_id)
            ORDER BY d.tenant_id,d.cursor''', (p['entity_id'],))
        targets = cur.fetchall()
        for target in targets:
            cur.execute('''INSERT INTO tenant_delivery(tenant_id,cursor,delivery_type,target_movement_analysis_id,reason)
                SELECT %s,COALESCE(MAX(cursor),0)+1,'INVALIDATION',%s,%s FROM tenant_delivery WHERE tenant_id=%s''',
                (target['tenant_id'],target['movement_analysis_id'],REASON,target['tenant_id']))
        return len(targets)
