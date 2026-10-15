"""Assemble committed app contracts for HTTP consumers and the review dashboard."""
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus


def assemble_screen(connection, kind, identity, feature, *, store=None):
    """Use the same DB assembly for screen routes and contract audits."""
    from edge_analysis_v2.storage.publications import PublicationStore
    store = store or PublicationStore(connection)
    idle = connection.info.transaction_status == TransactionStatus.IDLE
    with connection.transaction():
        if idle:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        return _assemble_screen(connection, kind, identity, feature, store)


def _assemble_screen(connection, kind, identity, feature, store):
    """Project one feature from the caller's read-only publication snapshot."""
    result = store.get_completed_in_snapshot(kind, identity)
    if result is None:
        return None
    # Publication metadata belongs to the server, never the model response.
    with connection.cursor(row_factory=dict_row) as cur:
        query = ('SELECT etf_code,analysis_at,published_at FROM movement_analyses WHERE analysis_id=%s'
                 if kind == 'movement' else
                 'SELECT etf_code,analysis_at,published_at FROM outlook_analyses WHERE analysis_id=%s')
        cur.execute(query, (identity,))
        publication = cur.fetchone()
        publication = {key: value.isoformat() if hasattr(value, 'isoformat') else value
                       for key, value in publication.items()}
        if kind == 'outlook':
            publication['forecast_period'] = '향후 1개월'
        else:
            cur.execute('''SELECT i.item_id,i.source_as_of,i.created_at AS added_at
                FROM movement_analyses a
                CROSS JOIN LATERAL unnest(a.selected_item_ids) WITH ORDINALITY selected(id,position)
                JOIN movement_items i ON i.item_id=selected.id
                WHERE a.analysis_id=%s ORDER BY selected.position''', (identity,))
            metadata = cur.fetchall()
            for item, meta in zip(result['items'], metadata, strict=True):
                item.update({key: value.isoformat() if hasattr(value, 'isoformat') else value
                             for key, value in meta.items()})
        result['publication'] = publication
    if feature == 'all':
        return result
    if kind == 'movement':
        if feature == 'summary':
            return {'summary':result['summary'], 'publication':publication}
        if feature == 'detail':
            return {'items':result['items'], 'publication':publication}
        return None
    if feature == 'summary':
        return {key:result[key] for key in ('outlook','summary_card','publication')}
    if feature in ('detail','factors','conclusion'):
        return {feature:result[feature], 'publication':publication}
    if feature == 'factor_details':
        from edge_analysis_v2.storage.factors import read_factor_details
        details = read_factor_details(connection, identity)
        return None if details is None else details | {'publication': publication}
    return None


