"""Keep alternative evidence reachable even when previews collapse articles."""
import pytest

from test_thread_repository import db, article, START, AT
from edge_analysis_v2.thread_repository import load_thread_articles


def test_duplicate_only_thread_can_be_explored_without_relabeling_its_events(db):
    for number in range(12):
        article(db, number, 'DUPLICATE_REBROADCAST')
    first = load_thread_articles(db, 'thread', ['sixth-stock'], start_at=START, analysis_at=AT)
    second = load_thread_articles(db, 'thread', ['sixth-stock'], start_at=START, analysis_at=AT, cursor=first['next_cursor'])
    ids = [a['document_id'] for page in (first, second) for a in page['articles']]
    assert len(ids) == len(set(ids)) == 12
    assert second['next_cursor'] is None


def test_event_filter_returns_other_articles_of_same_event_only(db):
    article(db, 0)
    article(db, 1)
    article(db, 2)
    db.raw.execute("DELETE FROM event_evidence WHERE source_event_id='1'")
    db.raw.execute("INSERT INTO event_evidence VALUES ('0','1')")
    result = load_thread_articles(db, 'thread', ['sixth-stock'], start_at=START, analysis_at=AT, source_event_id='0')
    assert {a['document_id'] for a in result['articles']} == {'0', '1'}
    with pytest.raises(ValueError, match='cursor'):
        load_thread_articles(db, 'thread', ['sixth-stock'], start_at=START, analysis_at=AT, source_event_id='0', cursor='2')
