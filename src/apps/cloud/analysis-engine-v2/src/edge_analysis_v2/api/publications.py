"""Read committed real-data publications without holding a connection between requests."""
from psycopg import sql
from psycopg.rows import dict_row

from edge_analysis_v2.storage.screens import assemble_screen


class PublicationReader:
    """Own one short-lived, read-only connection per database operation."""

    def __init__(self, connection_factory):
        self.connection_factory=connection_factory

    def find(self, identity):
        """Find an execution across both domains to prevent cross-kind ID reuse."""
        with self.connection_factory() as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute('''SELECT 'outlook' AS kind,analysis_id,etf_code,analysis_at,
                status,data_source,published_at FROM outlook_analyses WHERE analysis_id=%s
                UNION ALL SELECT 'movement' AS kind,analysis_id,etf_code,analysis_at,
                status,data_source,published_at FROM movement_analyses WHERE analysis_id=%s''',
                (identity,identity))
            rows=cur.fetchall()
            if len(rows)>1:
                raise ValueError('Ambiguous analysis identity')
            return rows[0] if rows else None

    def latest(self, ticker, kind, *, analysis_date=None):
        """Choose a published real analysis, optionally within one Korean calendar day.

        Args:
            ticker: ETF code.
            kind: Outlook or movement domain.
            analysis_date: Optional date of analysis_at in Asia/Seoul, not publication.
        """
        if kind not in ('movement','outlook'):
            raise ValueError('Unknown analysis kind')
        date_filter=sql.SQL('')
        params=[ticker]
        if analysis_date is not None:
            date_filter=sql.SQL('''AND analysis_at >= (%s::date::timestamp AT TIME ZONE 'Asia/Seoul')
                AND analysis_at < ((%s::date + 1)::timestamp AT TIME ZONE 'Asia/Seoul')''')
            params.extend([analysis_date,analysis_date])
        with self.connection_factory() as c, c.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.SQL('''SELECT analysis_id FROM {} WHERE etf_code=%s
                AND status='completed' AND data_source='database' AND published_at IS NOT NULL
                {}
                ORDER BY analysis_at DESC,published_at DESC,analysis_id DESC LIMIT 1''').format(
                    sql.Identifier(kind+'_analyses'),date_filter),params)
            return cur.fetchone()

    def screen(self, kind, identity, feature):
        """Reuse the exact assembly consumed by the screen-contract checks."""
        with self.connection_factory() as c:
            return assemble_screen(c,kind,identity,feature)
