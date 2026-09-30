"""Read committed analysis and tool evidence for the review dashboard."""

from datetime import date, datetime
from decimal import Decimal

from psycopg import Connection, sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row


def _row_json(row: dict) -> dict:
    """Serialize SQL date columns without changing nested tool output values."""
    return {key: value.isoformat() if isinstance(value, (date, datetime)) else value
            for key, value in row.items()}


def read_storage(connection, kind, identity):
    """Read actual publication rows and linked evidence in one read-only snapshot."""
    if kind not in ('movement', 'outlook'):
        raise ValueError('Unknown analysis kind')
    if not connection.autocommit or connection.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('Storage reader requires idle autocommit connection')
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        cur.execute(sql.SQL('SELECT * FROM {} WHERE analysis_id=%s').format(sql.Identifier(kind+'_analyses')), (identity,))
        parent = cur.fetchone()
        if parent is None:
            return None
        tables = {kind+'_analyses': [parent]}
        if kind == 'movement':
            cur.execute('''SELECT * FROM movement_items WHERE analysis_id=%s OR item_id=ANY(%s)
                           ORDER BY created_at,item_id''', (identity, parent['selected_item_ids']))
            tables['movement_items'] = cur.fetchall()
        else:
            for table, order in (
                ('outlook_items', ('section','position')),
                ('outlook_factors', ('type',)),
                ('outlook_conclusion_keywords', ('kind','position')),
                ('outlook_factor_metrics', ('factor_type','position')),
                ('outlook_issue_items', ('position',)),
            ):
                cur.execute(sql.SQL('SELECT * FROM {} WHERE analysis_id=%s ORDER BY {}').format(
                    sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, order))), (identity,))
                tables[table] = cur.fetchall()
        references = sorted({ref for rows in tables.values() for row in rows for ref in row.get('tool_run_ids', [])})
        cur.execute(sql.SQL('SELECT * FROM tool_runs WHERE {}=%s OR tool_run_id=ANY(%s) ORDER BY started_at,tool_run_id').format(
            sql.Identifier(kind+'_analysis_id')), (identity, references))
        tables['tool_runs'] = cur.fetchall()
        cur.execute('SELECT * FROM tool_definitions WHERE tool_id=ANY(%s) ORDER BY tool_id',
                    (sorted({row['tool_id'] for row in tables['tool_runs']}),))
        tables['tool_definitions'] = cur.fetchall()
    return {'storage':'postgresql', 'tables': {
        table: [{key: str(value) if isinstance(value, Decimal) else value
                 for key, value in _row_json(row).items()} for row in rows]
        for table, rows in tables.items()}}


def read_analysis_evidence(connection: Connection, analysis_kind: str,
                           analysis_id: str) -> dict | None:
    """Read one analysis and its committed executions from a consistent snapshot.

    Args:
        connection: Caller-owned idle autocommit connection to the result database.
        analysis_kind: Either movement or outlook.
        analysis_id: Identifier of the analysis being reviewed.

    Returns:
        Dashboard evidence with unchanged stored outputs, or None if absent.

    Raises:
        ValueError: Unknown kind or an active caller transaction.
        psycopg.Error: Database query failed; no local-file fallback is attempted.
    """
    if analysis_kind not in ("movement", "outlook"):
        raise ValueError("Unknown analysis kind")
    if (not connection.autocommit
            or connection.info.transaction_status != TransactionStatus.IDLE):
        raise ValueError("Evidence reader requires an idle autocommit connection")
    with connection.transaction(), connection.cursor(row_factory=dict_row) as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute(sql.SQL("SELECT * FROM {} WHERE analysis_id = %s").format(
            sql.Identifier(analysis_kind + "_analyses")), (analysis_id,))
        analysis = cur.fetchone()
        if analysis is None:
            return None
        cur.execute(sql.SQL("""
            SELECT r.*, d.function_name, d.version, d.description,
                   d.formula_latex, d.source_names
            FROM tool_runs r JOIN tool_definitions d USING (tool_id)
            WHERE r.{} = %s
            ORDER BY r.started_at, r.tool_run_id
            """).format(sql.Identifier(analysis_kind + "_analysis_id")), (analysis_id,))
        records = cur.fetchall()
    definitions = {}
    for record in records:
        definitions[record["tool_id"]] = {
            "description": record["description"], "formula_latex": record["formula_latex"],
            "source_name": record["source_names"],
        }
    return {"storage": "postgresql", "analysis": _row_json(analysis),
            "tool_runs": [_row_json(record) for record in records],
            "definitions": definitions}
