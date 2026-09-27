"""Read existing news relationships without querying mutable thread headers."""
from datetime import datetime

from .tools.news_threads import _timestamp, summarize_news_thread


def _scope(connection, constituent_ids, start_at, analysis_at):
    """Validate the shared read boundary and normalize timestamp parameters."""
    if (connection.read_only is not True
            or getattr(connection.isolation_level, "name", None) != "REPEATABLE_READ"):
        raise ValueError("News requires a read-only repeatable-read connection")
    if (not constituent_ids or isinstance(constituent_ids, str)
            or any(not isinstance(value, str) or not value.strip() for value in constituent_ids)):
        raise ValueError("eligible constituent IDs are required")
    start, end = _timestamp(start_at), _timestamp(analysis_at)
    if start > end:
        raise ValueError("start_at must not exceed analysis_at")
    return start.isoformat(), end.isoformat(), sorted(set(constituent_ids))


def load_thread_summary(
    connection, thread_id: str, constituent_ids: list[str], *,
    start_at: str, analysis_at: str, preview_limit: int = 3, cursor: str | None = None,
    end_at: str | None = None,
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
        end_at: Optional publication upper bound, no later than analysis_at.

    Returns:
        Thread summary, or None for no eligible linked articles. Current surviving
        rows only: this does not reconstruct deleted or corrected historical rows.

    Raises:
        ValueError: Connection, scope, bounds or preview limit is invalid.
    """
    start, end, targets = _scope(connection, constituent_ids, start_at, analysis_at)
    publication_end = end if end_at is None else _timestamp(end_at).isoformat()
    if not _timestamp(start) <= _timestamp(publication_end) <= _timestamp(end):
        raise ValueError('end_at must be between start_at and analysis_at')
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id is required")
    if type(preview_limit) is not int or preview_limit < 1:
        raise ValueError("preview_limit must be a positive integer")
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
        """, (thread_id, start, publication_end, end, end, end, end, *targets))
        rows = [{key: value.isoformat() if isinstance(value, datetime) else value
                 for key, value in row.items()} for row in db_cursor.fetchall()]
    return summarize_news_thread(thread_id, rows, start_at=start, end_at=end,
                                 preview_limit=preview_limit, cursor=cursor)


def load_issue_evidence(connection, news_ids: list[str], include_body: bool,
                        constituent_ids: list[str], *, start_at: str, analysis_at: str) -> dict:
    """Read eligible article excerpts or title-only final references.

    Args:
        connection: Read-only repeatable-read dictionary-row connection.
        news_ids: One to ten distinct article IDs from exploration.
        include_body: True for available lead excerpts; False for final references.
        constituent_ids: Server-owned constituent scope.
        start_at: Inclusive publication lower bound.
        analysis_at: Inclusive publication and availability cutoff.

    Returns:
        News objects in requested order. Missing excerpts are explicitly unavailable.

    Raises:
        ValueError: Invalid arguments or any requested article is unavailable in scope.
    """
    start, end, targets = _scope(connection, constituent_ids, start_at, analysis_at)
    if (not isinstance(news_ids, list) or not 1 <= len(news_ids) <= 10
            or any(not isinstance(value, str) or not value.strip() for value in news_ids)
            or len(set(news_ids)) != len(news_ids)):
        raise ValueError("news_ids requires one to ten distinct article IDs")
    if type(include_body) is not bool:
        raise ValueError("include_body must be boolean")
    article_slots, target_slots = ','.join(['%s'] * len(news_ids)), ','.join(['%s'] * len(targets))
    with connection.cursor() as db_cursor:
        db_cursor.execute(f"""
            SELECT d.document_id, d.title, n.lead_text, n.lead_observed_at
            FROM document d LEFT JOIN news_document n ON n.document_id=d.document_id
            WHERE d.document_type='NEWS' AND d.document_id IN ({article_slots})
              AND d.published_at >= %s AND d.published_at <= %s AND d.available_at <= %s
              AND EXISTS (SELECT 1 FROM document_entity e WHERE e.document_id=d.document_id
                          AND e.entity_id IN ({target_slots}))
        """, (*news_ids, start, end, end, *targets))
        rows = db_cursor.fetchall()
    indexed = {row['document_id']: row for row in rows}
    if set(indexed) != set(news_ids) or len(rows) != len(indexed):
        raise ValueError("requested article unavailable or inconsistent in analysis scope")
    news = []
    for identity in news_ids:
        row = indexed[identity]
        item = {'document_id': identity, 'title': row['title']}
        if include_body:
            observed = row['lead_observed_at']
            observed = observed.isoformat() if isinstance(observed, datetime) else observed
            available = bool(row['lead_text'] and observed and _timestamp(observed) <= _timestamp(end))
            item.update(lead_text=row['lead_text'] if available else None,
                        content_kind='lead_excerpt' if available else 'unavailable')
        news.append(item)
    return {'news': news}
