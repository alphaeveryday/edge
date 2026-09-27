"""Protect unique events and time boundaries when compressing news threads."""
from copy import deepcopy

import pytest

from edge_analysis_v2.tools.news_threads import summarize_news_thread


START = "2026-09-21T00:00:00+09:00"
END = "2026-09-21T08:30:00+09:00"


def row(event="e1", article="n1", novelty="FIRST_IN_THREAD", **changes):
    """Build a synthetic joined row using explicit source availability times."""
    return {
        "thread_id": "t1", "source_event_id": event, "document_id": article,
        "novelty_status": novelty, "lifecycle_stage": "ONGOING",
        "event_type_code": "COMPANY.PRODUCTION.CAPACITY_CHANGE",
        "predicate_code": "EXPAND", "title": "A사 생산시설 증설 착수",
        "published_at": "2026-09-21T08:00:00+09:00",
        "document_available_at": "2026-09-21T08:01:00+09:00",
        "event_available_at": "2026-09-21T08:02:00+09:00",
        "link_evaluated_at": "2026-09-21T08:03:00+09:00", **changes,
    }


def summarize(rows, limit=3):
    return summarize_news_thread("t1", rows, start_at=START, end_at=END, preview_limit=limit)


def test_same_stage_preserves_separate_events_and_corrections():
    result = summarize([row(), row("e2", "n2", "FOLLOW_UP_STAGE"),
                        row("e3", "n3", "CORRECTION", lifecycle_stage=None)])
    events = [event for stage in result["stages"] for event in stage["events"]]
    assert {event["source_event_id"] for event in events} == {"e1", "e2", "e3"}
    assert any(stage["stage"] is None for stage in result["stages"])
    assert result["duplicate_count"] == 0


def test_duplicate_count_uses_article_ids_across_all_events_before_preview():
    rows = [row(), row("e2", "n2"), row("d1", "n3", "DUPLICATE_REBROADCAST"),
            row("d2", "n3", "DUPLICATE_REBROADCAST"),
            row("d3", "n2", "DUPLICATE_REBROADCAST")]
    rows.append(deepcopy(rows[2]))
    assert summarize(rows, 1)["duplicate_count"] == summarize(rows, 3)["duplicate_count"] == 1
    assert summarize(rows, 1)["has_more_events"] is True


@pytest.mark.parametrize("field", ["published_at", "document_available_at", "event_available_at", "link_evaluated_at"])
def test_future_information_never_enters_preview_or_count(field):
    future = row("d", "future", "DUPLICATE_REBROADCAST", **{field: "2026-09-21T08:30:01+09:00"})
    assert summarize([row(), future]) == summarize([row()])


def test_representative_and_order_do_not_depend_on_join_order():
    rows = [row(article="n2"), row(article="n1"), row("e2", "n3")]
    before = deepcopy(rows)
    result = summarize(rows)
    assert result == summarize(list(reversed(rows)))
    assert rows == before
    assert result["stages"][0]["events"][0]["document_id"] == "n1"


def test_missing_timezone_or_conflicting_event_metadata_fails_loudly():
    with pytest.raises(ValueError, match="offset"):
        summarize([row(published_at="2026-09-21T08:00:00")])
    with pytest.raises(ValueError, match="conflicting"):
        summarize([row(), row(article="n2", lifecycle_stage="COMPLETED")])


def test_empty_unknown_and_out_of_window_do_not_invent_events():
    assert summarize([]) is None
    assert summarize([row(thread_id="other")]) is None
    assert summarize([row(published_at="2026-09-20T23:59:59+09:00")]) is None
    with pytest.raises(ValueError, match="preview_limit"):
        summarize([row()], 0)


def test_pages_keep_every_unique_event_and_scope_wide_duplicate_count():
    rows = [row('e1', 'n1', lifecycle_stage='PLANNED'), row('e2', 'n2'),
            row('e3', 'n3', lifecycle_stage='PLANNED'), row('e4', 'n4'),
            row('duplicate', 'n4', 'DUPLICATE_REBROADCAST'),
            row('duplicate2', 'n5', 'DUPLICATE_REBROADCAST')]
    first = summarize_news_thread('t1', rows, start_at=START, end_at=END, preview_limit=2)
    second = summarize_news_thread('t1', list(reversed(rows)), start_at=START,
                                   end_at=END, preview_limit=2, cursor=first['next_cursor'])
    ids = [e['source_event_id'] for page in (first, second)
           for stage in page['stages'] for e in stage['events']]
    assert ids == ['e1', 'e2', 'e3', 'e4']
    assert first['duplicate_count'] == second['duplicate_count'] == 1
    assert second['next_cursor'] is None
    assert second['has_more_events'] is False
    with pytest.raises(ValueError, match='cursor'):
        summarize_news_thread('t1', rows, start_at=START, end_at=END, cursor='other-event')
