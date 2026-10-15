"""Register real movement publications in the existing tenant delivery cursor."""
from psycopg.rows import dict_row

from edge_analysis_v2.storage.inspection import read_published_movement_evidence
from edge_analysis_v2.storage.retractions import is_retracted


def enqueue_movement(connection, analysis_id):
    """Deliver a completed movement once, preserving the v1 cursor lock.

    Args:
        connection: Idle autocommit result-writer connection.
        analysis_id: Completed real-data movement identifier.

    Returns:
        Number of tenant delivery rows committed, or zero for no new publication.

    Raises:
        ValueError: Missing, unfinished, synthetic, or invalid final evidence.
        psycopg.Error: Registration failed; retry the same completed analysis.
    """
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT status,data_source,published_at FROM movement_analyses WHERE analysis_id=%s", (analysis_id,))
        row = cur.fetchone()
    if not row or row['status'] != 'completed' or row['data_source'] != 'database':
        raise ValueError('Delivery requires completed real-data movement')
    if row['published_at'] is None:
        return 0
    evidence = read_published_movement_evidence(connection, analysis_id)
    if not evidence['items']:
        raise ValueError('Published movement requires selected evidence')
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cur:
        # Same lock as v1: cursor order must also be transaction commit order.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('tenant-delivery-fanout')::bigint)")
        if is_retracted(cur, analysis_id):
            cur.execute('UPDATE movement_analyses SET withdrawn_at=COALESCE(withdrawn_at,now()) WHERE analysis_id=%s', (analysis_id,))
            return 0
        cur.execute("""SELECT 1 FROM tenant_delivery d
            JOIN movement_analyses delivered ON delivered.analysis_id=d.movement_analysis_id
            JOIN movement_analyses current ON current.analysis_id=%s
            WHERE delivered.etf_code=current.etf_code AND (
                delivered.analysis_id=current.analysis_id
                OR delivered.analysis_at>=current.analysis_at
                OR (delivered.trading_date=current.trading_date
                    AND delivered.published_at=current.published_at
                    AND delivered.selected_item_ids=current.selected_item_ids)) LIMIT 1""", (analysis_id,))
        if cur.fetchone():
            return 0
        cur.execute("""INSERT INTO tenant_delivery(tenant_id,cursor,delivery_type,movement_analysis_id)
            SELECT t.tenant_id,COALESCE(MAX(d.cursor),0)+1,'NEW',%s
            FROM tenant t LEFT JOIN tenant_delivery d ON d.tenant_id=t.tenant_id
            GROUP BY t.tenant_id""", (analysis_id,))
        return cur.rowcount
