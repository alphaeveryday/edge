"""Search must preserve unlinked articles and all pages of the eligible scope."""
import pytest

from test_thread_repository import db, article, START, AT
from edge_analysis_v2.news_search import search_news_threads


def search(db, **kwargs):
    return search_news_threads(db, ['sixth-stock'], start_at=START, end_at=AT, analysis_at=AT, **kwargs)


def test_thread_and_unlinked_pages_have_no_gaps_or_repeated_join_articles(db):
    for number in range(5):
        article(db, number)
        db.raw.execute('UPDATE event_thread_link SET thread_id=? WHERE source_event_id=?', ('t' + str(number), str(number)))
    db.raw.execute("DELETE FROM event_thread_link WHERE source_event_id IN ('3','4')")
    db.raw.execute("INSERT INTO event_evidence VALUES ('0','0')")
    article(db, 'outside', target='unrelated')
    seen, cursor = [], None
    for _ in range(3):
        page = search(db, page_size=2, cursor=cursor)
        seen.extend(t['thread_id'] for t in page['threads'])
        seen.extend(n['document_id'] for n in page['unthreaded_articles'])
        cursor = page['next_cursor']
    assert set(seen) == {'t0', 't1', 't2', '3', '4'}
    assert len(seen) == 5 and cursor is None
    assert page['scope'] == {'start_at': START, 'end_at': AT}


def test_late_link_does_not_hide_an_article_and_cursor_is_bound_to_search(db):
    article(db, 0)
    article(db, 1)
    db.raw.execute("UPDATE event_thread_link SET evaluated_at=? WHERE source_event_id='0'", ('2026-09-21T08:30:01+09:00',))
    page = search(db, page_size=1)
    assert page['next_cursor']
    rest = search(db, cursor=page['next_cursor'], page_size=1)
    articles = page['unthreaded_articles'] + rest['unthreaded_articles']
    assert [item['document_id'] for item in articles] == ['0']
    with pytest.raises(ValueError, match='cursor'):
        search(db, cursor=page['next_cursor'], query='different query')
    with pytest.raises(ValueError, match='analysis_at'):
        search_news_threads(db, ['sixth-stock'], start_at=START, end_at='2026-09-22T00:00:00+09:00', analysis_at=AT)


def test_search_filters_titles_but_preserves_whole_thread_duplicate_counts(db):
    article(db, 0)
    article(db, 1, 'DUPLICATE_REBROADCAST')
    db.raw.execute("UPDATE document SET title='HBM 출시' WHERE document_id='0'")
    result = search(db, query='HBM')
    assert result['threads'][0]['duplicate_count'] == 1
    assert search(db, query='unmatched')['threads'] == []
