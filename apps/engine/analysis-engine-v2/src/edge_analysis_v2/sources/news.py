"""Capture event-first news and its documents in the caller's repeatable-read transaction."""
from datetime import timedelta


def rows(connection, statement, parameters):
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall()


def load_news(connection, at, targets, issuers):
    start = at - timedelta(days=180)
    # Source events, not article popularity, determine the event universe. Rejected rows
    # remain in the snapshot so a later analysis can observe their changed status.
    events = rows(connection, """SELECT s.source_event_id AS event_id,s.event_type_code AS event_type,
        s.event_date,s.lifecycle_stage AS stage,s.event_status AS status,s.available_at,
        l.thread_id,l.novelty_status AS novelty,l.evaluated_at,l.unknown_reason,
        ARRAY(SELECT DISTINCT a.entity_id FROM event_argument a
              WHERE a.source_event_id=s.source_event_id AND a.entity_id=ANY(%s) ORDER BY a.entity_id) AS issuer_ids,
        ARRAY(SELECT DISTINCT a.entity_id FROM event_argument a
              WHERE a.source_event_id=s.source_event_id AND a.entity_id=ANY(%s) ORDER BY a.entity_id) AS related_entity_ids
        FROM source_event s LEFT JOIN event_thread_link l
          ON l.source_event_id=s.source_event_id AND l.source_class='NEWS' AND l.evaluated_at<=%s
        WHERE s.source_class='NEWS' AND s.available_at BETWEEN %s AND %s
          AND EXISTS(SELECT 1 FROM event_argument a WHERE a.source_event_id=s.source_event_id AND a.entity_id=ANY(%s))
        ORDER BY s.source_event_id""", (issuers, targets, at, start, at, targets))
    identities = [r['event_id'] for r in events]
    evidence = rows(connection, """SELECT DISTINCT e.source_event_id AS event_id,a.document_id AS news_id
        FROM event_evidence e JOIN document_assertion a USING(assertion_id)
        JOIN document d USING(document_id) JOIN news_document n USING(document_id)
        WHERE e.source_event_id=ANY(%s) AND a.available_at<=%s
          AND d.document_type='NEWS' AND d.available_at<=%s AND d.published_at<=%s
        ORDER BY e.source_event_id,a.document_id""", (identities, at, at, at))
    document_ids = sorted({r['news_id'] for r in evidence})
    # An OR between evidence IDs and a correlated entity lookup scans unrelated
    # documents. Resolve the two indexed ID sets first, preserving the same union.
    articles = rows(connection, """WITH candidates AS (
        SELECT unnest(%s::text[]) AS document_id
        UNION
        SELECT d.document_id FROM document_entity de JOIN document d USING(document_id)
        WHERE de.entity_id=ANY(%s) AND d.document_type='NEWS'
          AND d.published_at BETWEEN %s AND %s AND d.available_at<=%s
        ) SELECT d.document_id,d.title,d.published_at,d.available_at,d.source_uri,
        n.lead_text,n.lead_observed_at FROM candidates c JOIN document d USING(document_id)
        JOIN news_document n USING(document_id)
        WHERE d.document_type='NEWS' AND d.available_at<=%s AND d.published_at<=%s
        ORDER BY d.published_at DESC,d.document_id""", (document_ids, targets, start, at, at, at, at))
    membership = {}
    for row in evidence:
        membership.setdefault(row['event_id'], []).append(row['news_id'])
    for row in events:
        for key in ('event_date', 'available_at', 'evaluated_at'):
            row[key] = row[key].isoformat() if row[key] is not None else None
        row['novelty'] = row['novelty'] or 'UNKNOWN'
        row['news_ids'] = membership.get(row['event_id'], [])
    news = []
    for row in articles:
        observed = row['lead_observed_at']
        news.append({'news_id': row['document_id'], 'title': row['title'], 'body_kind': 'excerpt',
                     'body': row['lead_text'] if observed is not None and observed <= at else None,
                     'lead_observed_at': observed.isoformat() if observed is not None and observed <= at else None,
                     'source_uri': row['source_uri'], 'published_at': row['published_at'].isoformat(),
                     'available_at': row['available_at'].isoformat()})
    return {'news': news, 'news_events': events, 'news_links': [], 'news_limit_reached': False,
            'news_scope': {'version': 1, 'lookback_days': 180, 'start_at': start.isoformat(), 'end_at': at.isoformat(),
                           'target_ids': sorted(set(targets)), 'issuer_ids': sorted(set(issuers)),
                           'event_time_field': 'available_at', 'historical_relation_state': 'current_rows_filtered_by_evaluated_at; older revisions unavailable',
                           'max_articles': None, 'limit_reached': False,
                           'universe': 'ETF/constituent/issuer-linked NEWS events plus related articles; one repeatable-read snapshot'}}
