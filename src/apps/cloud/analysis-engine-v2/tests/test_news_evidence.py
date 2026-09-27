"""Prevent final citations from escaping the target or analysis-time boundary."""
import pytest

from test_thread_repository import db, article, START, AT
from edge_analysis_v2.thread_repository import load_issue_evidence


def evidence(db, ids, body):
    return load_issue_evidence(db, ids, body, ['sixth-stock'], start_at=START, analysis_at=AT)


def test_excerpt_and_final_reference_are_distinct_without_losing_article_identity(db):
    article(db, 0)
    db.raw.execute('INSERT INTO news_document VALUES (?, ?, ?)', ('0', 'A사가 공장을 증설해요.', AT))
    explored = evidence(db, ['0'], True)['news'][0]
    final = evidence(db, ['0'], False)['news'][0]
    assert explored == {**final, 'lead_text': 'A사가 공장을 증설해요.', 'content_kind': 'lead_excerpt'}
    assert set(final) == {'document_id', 'title'}


@pytest.mark.parametrize('observed', [None, '2026-09-21T08:30:01+09:00'])
def test_missing_or_future_excerpt_is_not_returned_as_available_body(db, observed):
    article(db, 0)
    db.raw.execute('INSERT INTO news_document VALUES (?, ?, ?)', ('0', 'future text', observed))
    result = evidence(db, ['0'], True)['news'][0]
    assert result['lead_text'] is None
    assert result['content_kind'] == 'unavailable'
    assert evidence(db, ['0'], False)['news'][0]['document_id'] == '0'


def test_unrelated_future_and_missing_articles_fail_instead_of_partial_success(db):
    article(db, 0)
    article(db, 1, target='other')
    article(db, 2)
    db.raw.execute("UPDATE document SET available_at=? WHERE document_id='2'", ('2026-09-21T08:30:01+09:00',))
    for identity in ('1', '2', 'absent'):
        with pytest.raises(ValueError, match='unavailable'):
            evidence(db, ['0', identity], False)
    for invalid in ([], ['0', '0'], '0'):
        with pytest.raises(ValueError, match='news_ids'):
            evidence(db, invalid, False)
    with pytest.raises(ValueError, match='include_body'):
        evidence(db, ['0'], 'false')
