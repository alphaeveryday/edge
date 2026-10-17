"""Event-first source SQL must retain sparse facts behind thousands of repeated articles."""
from datetime import datetime
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from edge_analysis_v2.sources.news import load_news
from edge_analysis_v2.tools.news_events import NewsEvents


def test_event_corpus_has_no_article_cap_and_honors_every_evidence_clock():
    dsn = os.getenv('V2_FACTOR_TEST_DSN')
    if not dsn:
        pytest.skip('local news SQL database not configured')
    target = conninfo_to_dict(dsn)
    assert target['host'] == '127.0.0.1' and target['port'] == '15449' and target['dbname'] == 'analysis_v2'
    at = datetime.fromisoformat('2026-10-16T12:00:00+00:00')
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as connection:
        connection.execute('''
            CREATE TEMP TABLE source_event(source_event_id text,event_type_code text,event_date date,
                lifecycle_stage text,event_status text,available_at timestamptz,source_class text);
            CREATE TEMP TABLE event_argument(source_event_id text,entity_id text);
            CREATE TEMP TABLE event_thread_link(source_event_id text,source_class text,thread_id text,
                novelty_status text,evaluated_at timestamptz,unknown_reason text);
            CREATE TEMP TABLE document(document_id text,title text,document_type text,published_at timestamptz,
                available_at timestamptz,source_uri text);
            CREATE TEMP TABLE news_document(document_id text,lead_text text,lead_observed_at timestamptz);
            CREATE TEMP TABLE document_entity(document_id text,entity_id text);
            CREATE TEMP TABLE document_assertion(assertion_id text,document_id text,available_at timestamptz);
            CREATE TEMP TABLE event_evidence(source_event_id text,assertion_id text);
            INSERT INTO source_event
                SELECT 'repeat-'||i,'CONTRACT','2026-10-15','SIGNED','ACTIVE','2026-10-15T12:00Z','NEWS'
                FROM generate_series(1,1001) i;
            INSERT INTO source_event VALUES
                ('first','CONTRACT','2026-10-14','SIGNED','ACTIVE','2026-10-14T12:00Z','NEWS'),
                ('correction','CONTRACT','2026-10-15','SIGNED','ACTIVE','2026-10-15T13:00Z','NEWS'),
                ('independent','CONTRACT','2026-01-01','SIGNED','ACTIVE','2026-10-14T12:00Z','NEWS'),
                ('rejected','CONTRACT','2026-10-14','SIGNED','REJECTED','2026-10-14T12:00Z','NEWS'),
                ('future','CONTRACT','2026-10-17','SIGNED','ACTIVE','2026-10-17T12:00Z','NEWS');
            INSERT INTO event_argument SELECT source_event_id,'issuer' FROM source_event;
            INSERT INTO event_thread_link SELECT source_event_id,'NEWS','thread','DUPLICATE_REBROADCAST',available_at,NULL
                FROM source_event WHERE source_event_id LIKE 'repeat-%';
            INSERT INTO event_thread_link VALUES
                ('first','NEWS','thread','FIRST_IN_THREAD','2026-10-14T12:00Z',NULL),
                ('correction','NEWS','thread','CORRECTION','2026-10-15T13:00Z',NULL),
                ('independent','NEWS','wrong-future','FIRST_IN_THREAD','2026-10-17T12:00Z',NULL);
            INSERT INTO document SELECT source_event_id,source_event_id,'NEWS',available_at,available_at,
                'https://example.com/'||source_event_id FROM source_event;
            UPDATE document SET published_at='2026-01-01T00:00Z' WHERE document_id='independent';
            INSERT INTO document VALUES
                ('loose','loose','NEWS','2026-10-15T12:00Z','2026-10-15T12:00Z','https://example.com/loose'),
                ('future-assertion','bad','NEWS','2026-10-15T12:00Z','2026-10-15T12:00Z',NULL),
                ('future-acquired','bad','NEWS','2026-10-15T12:00Z','2026-10-17T12:00Z',NULL);
            INSERT INTO news_document SELECT document_id,'excerpt',available_at FROM document;
            UPDATE news_document SET lead_observed_at='2026-10-17T12:00Z' WHERE document_id='correction';
            INSERT INTO document_entity VALUES ('loose','issuer'),('future-acquired','issuer');
            INSERT INTO document_assertion SELECT document_id,document_id,available_at FROM document;
            UPDATE document_assertion SET available_at='2026-10-17T12:00Z' WHERE document_id='future-assertion';
            INSERT INTO event_evidence SELECT source_event_id,source_event_id FROM source_event;
            INSERT INTO event_evidence VALUES ('correction','future-assertion'),('correction','future-acquired');
            INSERT INTO source_event VALUES ('etf-event','FUND_CHANGE','2026-10-15',NULL,'ACTIVE','2026-10-15T12:00Z','NEWS');
            INSERT INTO event_argument VALUES ('etf-event','etf-instrument');
            INSERT INTO event_argument VALUES ('first','issuer'),('first',NULL),('first','external-issuer');
            INSERT INTO source_event VALUES ('issuer-only','CONTRACT','2026-10-15',NULL,'ACTIVE','2026-10-15T12:00Z','NEWS');
            INSERT INTO event_argument VALUES ('issuer-only','external-issuer');
        ''')
        connection.autocommit = False
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        loaded = load_news(connection, at, ['issuer', 'etf-instrument'], ['issuer', 'external-issuer'])
        assert float(connection.execute('SHOW cursor_tuple_fraction').fetchone()['cursor_tuple_fraction']) == 1.0
        articles = {row['news_id']: row for row in loaded['news']}
        assert len(articles) == 1006  # 1001 repeats + first/correction/independent/rejected + loose.
        assert {'future', 'future-assertion', 'future-acquired'}.isdisjoint(articles)
        assert articles['correction']['body'] is None
        assert articles['independent']['body'] == 'excerpt'  # Old publication newly available to this event is evidence.
        events = {row['event_id']: row for row in loaded['news_events']}
        assert events['independent']['thread_id'] is None
        assert events['rejected']['status'] == 'REJECTED'
        assert events['etf-event']['related_entity_ids'] == ['etf-instrument']
        assert events['etf-event']['issuer_ids'] == []
        assert events['first']['issuer_ids'] == ['external-issuer', 'issuer']
        assert events['first']['related_entity_ids'] == ['issuer']
        assert 'issuer-only' not in events  # Issuer metadata alone never widens the target universe.
        assert events['correction']['news_ids'] == ['correction']
        tools = NewsEvents(loaded | {'context': {'etf_code': '091160', 'analysis_at': at.isoformat()}})
        assert tools.listing()['total_count'] == 3
        assert tools.listing()['unlinked_article_count'] == 2
        timeline = tools.timeline('thread:thread')
        assert {row['event_id'] for row in timeline['items']} == {'first', 'correction'}
        assert timeline['folded_rebroadcast_count'] == 1001
