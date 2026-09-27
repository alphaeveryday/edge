"""Discover existing threads and unlinked articles within a fixed read transaction."""
import base64
from datetime import datetime
import hashlib
import json

from .thread_repository import _scope, load_thread_summary
from .tools.news_threads import _timestamp


def search_news_threads(connection, constituent_ids: list[str], *, start_at: str,
                        end_at: str, analysis_at: str, query: str = '',
                        cursor: str | None = None, page_size: int = 10) -> dict:
    """Return one stable page of scoped thread previews and unlinked articles.

    Args:
        connection: Read-only repeatable-read dictionary-row connection.
        constituent_ids: Eligible target IDs, not a top-weight subset.
        start_at: Inclusive publication lower bound.
        end_at: Inclusive publication upper bound, no later than analysis_at.
        analysis_at: Fixed availability cutoff for articles and relationships.
        query: Literal case-insensitive title substring; empty matches all.
        cursor: Opaque next-page value from the same search and transaction.
        page_size: Total thread and unlinked-article items, one to twenty.

    Returns:
        Threads, unthreaded articles, next cursor and publication scope. Keyword
        matches select threads; their previews and duplicate counts cover the
        whole time and constituent scope, not just matching titles.

    Raises:
        ValueError: Invalid scope, query, page size or cursor.
    """
    start, cutoff, targets = _scope(connection, constituent_ids, start_at, analysis_at)
    end = _timestamp(end_at).isoformat()
    if not _timestamp(start) <= _timestamp(end) <= _timestamp(cutoff):
        raise ValueError('end_at must be between start_at and analysis_at')
    if type(page_size) is not int or not 1 <= page_size <= 20:
        raise ValueError('page_size must be between 1 and 20')
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError('query must be a title substring of at most 200 characters')
    query = query.strip().casefold()
    scope_key = hashlib.sha256(json.dumps([start, end, cutoff, targets, query]).encode()).hexdigest()
    target_slots = ','.join(['%s'] * len(targets))
    with connection.cursor() as db_cursor:
        db_cursor.execute(f"""
            SELECT DISTINCT d.document_id,d.title,d.published_at,links.thread_id
            FROM document d LEFT JOIN (
                SELECT a.document_id,l.thread_id FROM document_assertion a
                JOIN event_evidence e ON e.assertion_id=a.assertion_id
                JOIN source_event s ON s.source_event_id=e.source_event_id
                JOIN event_thread_link l ON l.source_event_id=s.source_event_id
                WHERE a.available_at<=%s AND s.available_at<=%s AND l.evaluated_at<=%s
                  AND s.source_class='NEWS' AND l.source_class='NEWS'
                  AND s.event_status='ACTIVE' AND l.thread_id IS NOT NULL
            ) links ON links.document_id=d.document_id
            WHERE d.document_type='NEWS' AND d.published_at>=%s
              AND d.published_at<=%s AND d.available_at<=%s
              AND EXISTS(SELECT 1 FROM document_entity de WHERE de.document_id=d.document_id
                         AND de.entity_id IN ({target_slots}))
        """, (cutoff, cutoff, cutoff, start, end, cutoff, *targets))
        rows = db_cursor.fetchall()
    items = {}
    for row in rows:
        if query not in row['title'].casefold():
            continue
        published = row['published_at']
        published = published.isoformat() if isinstance(published, datetime) else published
        identity = ('thread:' + row['thread_id']) if row['thread_id'] else ('article:' + row['document_id'])
        candidate = {'key': identity, 'thread_id': row['thread_id'], 'published_at': published,
                     'document_id': row['document_id'], 'title': row['title']}
        if identity not in items or _timestamp(published) > _timestamp(items[identity]['published_at']):
            items[identity] = candidate
    ordered = sorted(items.values(), key=lambda item: (-_timestamp(item['published_at']).timestamp(), item['key']))
    keys = [item['key'] for item in ordered]
    offset = 0
    if cursor is not None:
        try:
            scope, after = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if scope != scope_key or after not in keys:
                raise ValueError()
            offset = keys.index(after) + 1
        except (ValueError, TypeError, AttributeError, UnicodeError):
            raise ValueError('cursor does not belong to this search') from None
    page = ordered[offset:offset + page_size]
    next_cursor = None
    if offset + len(page) < len(ordered):
        next_cursor = base64.urlsafe_b64encode(json.dumps([scope_key, page[-1]['key']]).encode()).decode()
    threads, articles = [], []
    for item in page:
        if item['thread_id']:
            threads.append(load_thread_summary(connection, item['thread_id'], targets,
                           start_at=start, end_at=end, analysis_at=cutoff))
        else:
            articles.append({key: item[key] for key in ('document_id', 'title', 'published_at')})
    return {'threads': threads, 'unthreaded_articles': articles, 'next_cursor': next_cursor,
            'scope': {'start_at': start, 'end_at': end}}
