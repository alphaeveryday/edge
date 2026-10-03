"""Durable admission records, independent from final publication tables."""
from psycopg.rows import dict_row

from edge_analysis_v2.cloud.admission import RequestConflict


class RequestStore:
    """Reserve immutable input and record AWS acceptance in short transactions.

    Args:
        connection_factory: Opens a connection allowed to write admission records.
            No transaction or database lock is held while calling AWS.
    """

    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def reserve(self, request, execution_arn, input_json):
        """Claim a request ID and event ID without overwriting an earlier input."""
        with self.connection_factory() as connection, connection.transaction():
            with connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute('''INSERT INTO analysis_execution_requests
                    (analysis_id, source_event_id, input_json, execution_arn)
                    VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''',
                    (request['analysis_id'], request.get('source',{}).get('event_id'), input_json, execution_arn))
                cursor.execute('SELECT * FROM analysis_execution_requests WHERE analysis_id=%s',
                               (request['analysis_id'],))
                row = cursor.fetchone()
                if row is None or (row['input_json'],row['execution_arn']) != (input_json,execution_arn):
                    raise RequestConflict('Request or source event identity conflict')
                return row

    def accept(self, identity, execution_arn):
        """Persist acceptance; a failed commit must prevent the caller's ACK."""
        with self.connection_factory() as connection, connection.transaction():
            row = connection.execute('''UPDATE analysis_execution_requests
                SET accepted_at=COALESCE(accepted_at,now())
                WHERE analysis_id=%s AND execution_arn=%s RETURNING analysis_id''',
                (identity,execution_arn)).fetchone()
            if row is None:
                raise RequestConflict('No matching execution reservation')
