"""Read existing news relationships without querying mutable thread headers."""
from datetime import datetime

from .tools.news_threads import _timestamp, summarize_news_thread


def load_thread_summary(
    connection, thread_id: str, constituent_ids: list[str], *,
    start_at: str, analysis_at: str, preview_limit: int = 3, cursor: str | None = None,
) -> dict | None:
    """Read one scoped thread and assemble its event preview.

    Args:
        connection: Read-only repeatable-read connection returning dictionary rows.
        thread_id: Existing thread ID obtained through discovery.
        constituent_ids: Caller-supplied eligible ETF constituent scope, not top five.
        start_at: Inclusive article publication lower bound with UTC offset.
        analysis_at: Server-fixed inclusive availability cutoff with UTC offset.
        preview_limit: Maximum displayed unique events; does not limit SQL rows.
        cursor: Previous page's final event ID, within the same scope.

    Returns:
        Thread summary, or None for no eligible linked articles. Current surviving
        rows only: this does not reconstruct deleted or corrected historical rows.

    Raises:
        ValueError: Connection, scope, bounds or preview limit is invalid.
    """
    if (connection.read_only is not True
            or getattr(connection.isolation_level, "name", None) != "REPEATABLE_READ"):
        raise ValueError("News requires a read-only repeatable-read connection")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id is required")
    if (not constituent_ids or isinstance(constituent_ids, str)
            or any(not isinstance(value, str) or not value.strip() for value in constituent_ids)):
        raise ValueError("eligible constituent IDs are required")
    start_time, end_time = _timestamp(start_at), _timestamp(analysis_at)
    if start_time > end_time:
        raise ValueError("start_at must not exceed analysis_at")
    if type(preview_limit) is not int or preview_limit < 1:
        raise ValueError("preview_limit must be a positive integer")
    start, end = start_time.isoformat(), end_time.isoformat()
    targets = sorted(set(constituent_ids))
    slots = ",".join(["%s"] * len(targets))
    with connection.cursor() as db_cursor:
        db_cursor.execute(f"""
            SELECT DISTINCT l.thread_id, s.source_event_id, s.lifecycle_stage,
                s.event_type_code, s.predicate_code, l.novelty_status,
                d.document_id, d.title, d.published_at,
                d.available_at AS document_available_at,
                s.available_at AS event_available_at, l.evaluated_at AS link_evaluated_at
            FROM event_thread_link l
            JOIN source_event s ON s.source_event_id = l.source_event_id
            JOIN event_evidence e ON e.source_event_id = s.source_event_id
            JOIN document_assertion a ON a.assertion_id = e.assertion_id
            JOIN document d ON d.document_id = a.document_id
            WHERE l.thread_id = %s AND l.source_class = 'NEWS'
              AND s.source_class = 'NEWS' AND s.event_status = 'ACTIVE'
              AND d.document_type = 'NEWS'
              AND d.published_at >= %s AND d.published_at <= %s
              AND d.available_at <= %s AND a.available_at <= %s
              AND s.available_at <= %s AND l.evaluated_at <= %s
              AND EXISTS (SELECT 1 FROM document_entity entity
                          WHERE entity.document_id = d.document_id
                            AND entity.entity_id IN ({slots}))
        """, (thread_id, start, end, end, end, end, end, *targets))
        rows = [{key: value.isoformat() if isinstance(value, datetime) else value
                 for key, value in row.items()} for row in db_cursor.fetchall()]
    return summarize_news_thread(thread_id, rows, start_at=start, end_at=end,
                                 preview_limit=preview_limit, cursor=cursor)
